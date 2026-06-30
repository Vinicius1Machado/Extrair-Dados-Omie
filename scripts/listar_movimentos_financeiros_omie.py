import argparse
import hashlib
import json
import logging
import os
import sys
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pymysql
import requests
from dotenv import load_dotenv

import listar_clientes_omie
import listar_titulos_lancados_omie


BASE_DIR = Path(__file__).resolve().parent.parent
API_URL = "https://app.omie.com.br/api/v1/financas/mf/"
CLIENTES_API_URL = "https://app.omie.com.br/api/v1/geral/clientes/"
TITULOS_API_URL = "https://app.omie.com.br/api/v1/financas/pesquisartitulos/"
REGISTROS_POR_PAGINA = 500
MAX_TENTATIVAS = 5
INTERVALO_ENTRE_PAGINAS = 0.3
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "listar_movimentos_financeiros_omie.log"
CHECKPOINT_FILE = LOG_DIR / "listar_movimentos_financeiros_omie.checkpoint.json"

load_dotenv(BASE_DIR / ".env")

APP_KEY = os.getenv("OMIE_APP_KEY", "")
APP_SECRET = os.getenv("OMIE_APP_SECRET", "")
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3307"))
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "omie_db")
MYSQL_USER = os.getenv("MYSQL_USER", "omie_user")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "omie_123")
MYSQL_ADMIN_USER = os.getenv("MYSQL_ADMIN_USER", "root")
MYSQL_ADMIN_PASSWORD = os.getenv("MYSQL_ADMIN_PASSWORD", "omie_root_123")


class PaginaSemRegistros(Exception):
    pass


def configurar_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )


def carregar_checkpoint() -> int:
    if not CHECKPOINT_FILE.exists():
        return 0

    try:
        dados = json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
        return max(int(dados.get("ultima_pagina_concluida", 0)), 0)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        logging.warning("Checkpoint invalido. A extracao sera iniciada do comeco.")
        return 0


def salvar_checkpoint(pagina: int, total_registros: int) -> None:
    CHECKPOINT_FILE.write_text(
        json.dumps(
            {
                "ultima_pagina_concluida": pagina,
                "total_registros_processados": total_registros,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def remover_checkpoint() -> None:
    CHECKPOINT_FILE.unlink(missing_ok=True)


def texto(valor: Any) -> str | None:
    if valor is None:
        return None
    valor = str(valor).strip()
    return valor or None


def inteiro(valor: Any) -> int | None:
    if valor in (None, ""):
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def decimal(valor: Any) -> Decimal | None:
    if valor in (None, ""):
        return None
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None


def objeto(valor: Any) -> dict[str, Any]:
    return valor if isinstance(valor, dict) else {}


def achatar_json(valor: Any, prefixo: str = "") -> dict[str, Any]:
    linha: dict[str, Any] = {}

    if isinstance(valor, dict):
        for chave, item in valor.items():
            nova_chave = f"{prefixo}.{chave}" if prefixo else str(chave)
            linha.update(achatar_json(item, nova_chave))
    elif isinstance(valor, list):
        linha[prefixo] = json.dumps(valor, ensure_ascii=False)
    else:
        linha[prefixo] = valor

    return linha


def gerar_chave_movimento(movimento: dict[str, Any]) -> str:
    detalhes = objeto(movimento.get("detalhes"))
    codigo_movimento = inteiro(detalhes.get("nCodMovCC"))
    codigo_titulo = inteiro(detalhes.get("nCodTitulo"))
    codigo_cliente = inteiro(detalhes.get("nCodCliente"))

    if codigo_movimento and codigo_titulo:
        return f"CC:{codigo_movimento}:TIT:{codigo_titulo}"
    if codigo_movimento and codigo_cliente:
        return f"CC:{codigo_movimento}:CLI:{codigo_cliente}"
    if codigo_movimento:
        return f"CC:{codigo_movimento}"
    if codigo_titulo:
        return f"TIT:{codigo_titulo}"

    conteudo = json.dumps(
        movimento,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"HASH:{hashlib.sha256(conteudo.encode('utf-8')).hexdigest()}"


def chamar_api(pagina: int) -> dict[str, Any]:
    payload = {
        "call": "ListarMovimentos",
        "param": [
            {
                "nPagina": pagina,
                "nRegPorPagina": REGISTROS_POR_PAGINA,
                "lDadosCad": True,
                "cExibirDepartamentos": "S",
            }
        ],
        "app_key": APP_KEY,
        "app_secret": APP_SECRET,
    }

    for tentativa in range(1, MAX_TENTATIVAS + 1):
        try:
            response = requests.post(API_URL, json=payload, timeout=(15, 90))

            if response.status_code == 500:
                try:
                    erro = response.json()
                except ValueError:
                    erro = {}

                if erro.get("faultcode") == "SOAP-ENV:Client-5113":
                    raise PaginaSemRegistros

            response.raise_for_status()
            dados = response.json()
            if not isinstance(dados, dict):
                raise RuntimeError("Resposta inesperada: JSON raiz nao e um objeto.")
            if dados.get("faultcode"):
                raise RuntimeError(dados.get("faultstring") or dados["faultcode"])
            return dados
        except PaginaSemRegistros:
            raise
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            if tentativa == MAX_TENTATIVAS:
                raise RuntimeError(
                    f"Falha na pagina {pagina} apos {MAX_TENTATIVAS} tentativas: {exc}"
                ) from exc

            espera = min(2 ** (tentativa - 1), 30)
            logging.warning(
                "Pagina %s falhou na tentativa %s/%s: %s. Nova tentativa em %ss.",
                pagina,
                tentativa,
                MAX_TENTATIVAS,
                exc,
                espera,
            )
            time.sleep(espera)

    raise RuntimeError(f"Nao foi possivel consultar a pagina {pagina}.")


def consultar_cliente(codigo_cliente_omie: int) -> dict[str, Any]:
    payload = {
        "call": "ConsultarCliente",
        "param": [
            {
                "codigo_cliente_omie": codigo_cliente_omie,
                "codigo_cliente_integracao": "",
            }
        ],
        "app_key": APP_KEY,
        "app_secret": APP_SECRET,
    }

    for tentativa in range(1, MAX_TENTATIVAS + 1):
        try:
            response = requests.post(CLIENTES_API_URL, json=payload, timeout=(15, 90))
            response.raise_for_status()
            dados = response.json()
            if not isinstance(dados, dict):
                raise RuntimeError("Resposta inesperada: JSON raiz nao e um objeto.")
            if dados.get("faultcode"):
                raise RuntimeError(dados.get("faultstring") or dados["faultcode"])
            return dados
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            if tentativa == MAX_TENTATIVAS:
                raise RuntimeError(
                    "Falha ao consultar cliente "
                    f"{codigo_cliente_omie} apos {MAX_TENTATIVAS} tentativas: {exc}"
                ) from exc

            espera = min(2 ** (tentativa - 1), 30)
            logging.warning(
                "Cliente %s falhou na tentativa %s/%s: %s. Nova tentativa em %ss.",
                codigo_cliente_omie,
                tentativa,
                MAX_TENTATIVAS,
                exc,
                espera,
            )
            time.sleep(espera)

    raise RuntimeError(f"Nao foi possivel consultar o cliente {codigo_cliente_omie}.")


def consultar_titulo(codigo_titulo: int) -> dict[str, Any]:
    payload = {
        "call": "PesquisarLancamentos",
        "param": [
            {
                "nPagina": 1,
                "nRegPorPagina": 20,
                "nCodTitulo": codigo_titulo,
            }
        ],
        "app_key": APP_KEY,
        "app_secret": APP_SECRET,
    }

    for tentativa in range(1, MAX_TENTATIVAS + 1):
        try:
            response = requests.post(TITULOS_API_URL, json=payload, timeout=(15, 90))
            response.raise_for_status()
            dados = response.json()
            if not isinstance(dados, dict):
                raise RuntimeError("Resposta inesperada: JSON raiz nao e um objeto.")
            if dados.get("faultcode"):
                raise RuntimeError(dados.get("faultstring") or dados["faultcode"])
            return dados
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            if tentativa == MAX_TENTATIVAS:
                raise RuntimeError(
                    f"Falha ao consultar titulo {codigo_titulo} "
                    f"apos {MAX_TENTATIVAS} tentativas: {exc}"
                ) from exc

            espera = min(2 ** (tentativa - 1), 30)
            logging.warning(
                "Titulo %s falhou na tentativa %s/%s: %s. Nova tentativa em %ss.",
                codigo_titulo,
                tentativa,
                MAX_TENTATIVAS,
                exc,
                espera,
            )
            time.sleep(espera)

    raise RuntimeError(f"Nao foi possivel consultar o titulo {codigo_titulo}.")


def conectar_mysql(
    usar_banco: bool = True,
    admin: bool = False,
) -> pymysql.connections.Connection:
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_ADMIN_USER if admin else MYSQL_USER,
        password=MYSQL_ADMIN_PASSWORD if admin else MYSQL_PASSWORD,
        database=MYSQL_DATABASE if usar_banco else None,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def preparar_banco() -> None:
    with conectar_mysql(usar_banco=False, admin=True) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DATABASE}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            cursor.execute(
                "CREATE USER IF NOT EXISTS %s@'%%' IDENTIFIED BY %s",
                (MYSQL_USER, MYSQL_PASSWORD),
            )
            cursor.execute(
                f"GRANT ALL PRIVILEGES ON `{MYSQL_DATABASE}`.* TO %s@'%%'",
                (MYSQL_USER,),
            )
        conn.commit()

    with conectar_mysql() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS raw_omie_movimentos_financeiros (
                    chave_movimento VARCHAR(100) NOT NULL,
                    codigo_titulo BIGINT NULL,
                    codigo_titulo_vinculado BIGINT NULL,
                    codigo_titulo_repeticao BIGINT NULL,
                    codigo_titulo_integracao VARCHAR(120) NULL,
                    codigo_cliente BIGINT NULL,
                    cnpj_cpf_cliente VARCHAR(30) NULL,
                    codigo_contrato BIGINT NULL,
                    numero_contrato VARCHAR(120) NULL,
                    codigo_ordem_servico BIGINT NULL,
                    numero_ordem_servico VARCHAR(120) NULL,
                    codigo_categoria VARCHAR(40) NULL,
                    grupo VARCHAR(40) NULL,
                    natureza CHAR(1) NULL,
                    operacao VARCHAR(10) NULL,
                    numero_documento_fiscal VARCHAR(120) NULL,
                    numero_parcela VARCHAR(30) NULL,
                    numero_titulo VARCHAR(120) NULL,
                    origem VARCHAR(20) NULL,
                    status_titulo VARCHAR(40) NULL,
                    codigo_tipo_documento VARCHAR(20) NULL,
                    data_emissao VARCHAR(10) NULL,
                    data_pagamento VARCHAR(10) NULL,
                    data_previsao VARCHAR(10) NULL,
                    data_registro VARCHAR(10) NULL,
                    data_vencimento VARCHAR(10) NULL,
                    id_conta_corrente BIGINT NULL,
                    valor_titulo DECIMAL(18, 4) NULL,
                    valor_pis DECIMAL(18, 4) NULL,
                    retem_pis CHAR(1) NULL,
                    valor_cofins DECIMAL(18, 4) NULL,
                    retem_cofins CHAR(1) NULL,
                    valor_csll DECIMAL(18, 4) NULL,
                    retem_csll CHAR(1) NULL,
                    valor_ir DECIMAL(18, 4) NULL,
                    retem_ir CHAR(1) NULL,
                    valor_iss DECIMAL(18, 4) NULL,
                    retem_iss CHAR(1) NULL,
                    valor_inss DECIMAL(18, 4) NULL,
                    retem_inss CHAR(1) NULL,
                    codigo_projeto BIGINT NULL,
                    observacao TEXT NULL,
                    codigo_vendedor BIGINT NULL,
                    codigo_comprador BIGINT NULL,
                    codigo_barras VARCHAR(255) NULL,
                    nsu VARCHAR(120) NULL,
                    codigo_nota_fiscal BIGINT NULL,
                    numero_boleto VARCHAR(120) NULL,
                    chave_nfe VARCHAR(44) NULL,
                    codigo_movimento_conta_corrente BIGINT NULL,
                    valor_movimento_conta_corrente DECIMAL(18, 4) NULL,
                    codigo_movimento_repeticao BIGINT NULL,
                    desconto_movimento DECIMAL(18, 4) NULL,
                    juros_movimento DECIMAL(18, 4) NULL,
                    multa_movimento DECIMAL(18, 4) NULL,
                    codigo_baixa BIGINT NULL,
                    data_credito VARCHAR(10) NULL,
                    data_conciliacao VARCHAR(10) NULL,
                    hora_conciliacao VARCHAR(8) NULL,
                    usuario_conciliacao VARCHAR(80) NULL,
                    data_inclusao VARCHAR(10) NULL,
                    hora_inclusao VARCHAR(8) NULL,
                    usuario_inclusao VARCHAR(80) NULL,
                    data_alteracao VARCHAR(10) NULL,
                    hora_alteracao VARCHAR(8) NULL,
                    usuario_alteracao VARCHAR(80) NULL,
                    liquidado CHAR(1) NULL,
                    valor_desconto DECIMAL(18, 4) NULL,
                    valor_juros DECIMAL(18, 4) NULL,
                    valor_multa DECIMAL(18, 4) NULL,
                    valor_aberto DECIMAL(18, 4) NULL,
                    valor_liquido DECIMAL(18, 4) NULL,
                    valor_pago DECIMAL(18, 4) NULL,
                    pagina_api INT NULL,
                    total_de_paginas_api INT NULL,
                    registros_api INT NULL,
                    total_de_registros_api INT NULL,
                    detalhes_json JSON NOT NULL,
                    resumo_json JSON NOT NULL,
                    categorias_json JSON NOT NULL,
                    departamentos_json JSON NOT NULL,
                    dados_json JSON NOT NULL,
                    dados_flat_json JSON NOT NULL,
                    extraido_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        ON UPDATE CURRENT_TIMESTAMP,
                    PRIMARY KEY (chave_movimento),
                    KEY idx_raw_omie_mf_titulo (codigo_titulo),
                    KEY idx_raw_omie_mf_titulo_vinculado (codigo_titulo_vinculado),
                    KEY idx_raw_omie_mf_cliente (codigo_cliente),
                    KEY idx_raw_omie_mf_titulo_repeticao (codigo_titulo_repeticao),
                    KEY idx_raw_omie_mf_categoria (codigo_categoria),
                    KEY idx_raw_omie_mf_grupo (grupo),
                    KEY idx_raw_omie_mf_natureza (natureza),
                    KEY idx_raw_omie_mf_conta_corrente (id_conta_corrente),
                    KEY idx_raw_omie_mf_contrato (codigo_contrato),
                    KEY idx_raw_omie_mf_ordem_servico (codigo_ordem_servico),
                    KEY idx_raw_omie_mf_nota_fiscal (codigo_nota_fiscal),
                    KEY idx_raw_omie_mf_movimento_cc (codigo_movimento_conta_corrente),
                    KEY idx_raw_omie_mf_status (status_titulo),
                    KEY idx_raw_omie_mf_vencimento (data_vencimento),
                    CONSTRAINT fk_raw_omie_mf_cliente
                        FOREIGN KEY (codigo_cliente)
                        REFERENCES raw_omie_clientes (codigo_cliente_omie)
                        ON UPDATE CASCADE
                        ON DELETE RESTRICT,
                    CONSTRAINT fk_raw_omie_mf_titulo
                        FOREIGN KEY (codigo_titulo_vinculado)
                        REFERENCES raw_omie_titulos_lancados (codigo_titulo)
                        ON UPDATE CASCADE
                        ON DELETE RESTRICT
                )
                CHARACTER SET utf8mb4
                COLLATE utf8mb4_unicode_ci
                """
            )

            cursor.execute("SHOW COLUMNS FROM raw_omie_movimentos_financeiros")
            colunas_existentes = {linha["Field"] for linha in cursor.fetchall()}
            colunas_adicionais = {
                "chave_movimento": "VARCHAR(100) NULL",
                "codigo_titulo_vinculado": "BIGINT NULL",
                "codigo_titulo_integracao": "VARCHAR(120) NULL",
                "codigo_contrato": "BIGINT NULL",
                "numero_contrato": "VARCHAR(120) NULL",
                "codigo_ordem_servico": "BIGINT NULL",
                "numero_ordem_servico": "VARCHAR(120) NULL",
                "operacao": "VARCHAR(10) NULL",
                "valor_pis": "DECIMAL(18, 4) NULL",
                "retem_pis": "CHAR(1) NULL",
                "valor_cofins": "DECIMAL(18, 4) NULL",
                "retem_cofins": "CHAR(1) NULL",
                "valor_csll": "DECIMAL(18, 4) NULL",
                "retem_csll": "CHAR(1) NULL",
                "valor_ir": "DECIMAL(18, 4) NULL",
                "retem_ir": "CHAR(1) NULL",
                "valor_iss": "DECIMAL(18, 4) NULL",
                "retem_iss": "CHAR(1) NULL",
                "valor_inss": "DECIMAL(18, 4) NULL",
                "retem_inss": "CHAR(1) NULL",
                "codigo_projeto": "BIGINT NULL",
                "observacao": "TEXT NULL",
                "codigo_vendedor": "BIGINT NULL",
                "codigo_comprador": "BIGINT NULL",
                "codigo_barras": "VARCHAR(255) NULL",
                "nsu": "VARCHAR(120) NULL",
                "codigo_nota_fiscal": "BIGINT NULL",
                "numero_boleto": "VARCHAR(120) NULL",
                "chave_nfe": "VARCHAR(44) NULL",
                "codigo_movimento_conta_corrente": "BIGINT NULL",
                "valor_movimento_conta_corrente": "DECIMAL(18, 4) NULL",
                "codigo_movimento_repeticao": "BIGINT NULL",
                "desconto_movimento": "DECIMAL(18, 4) NULL",
                "juros_movimento": "DECIMAL(18, 4) NULL",
                "multa_movimento": "DECIMAL(18, 4) NULL",
                "codigo_baixa": "BIGINT NULL",
                "data_credito": "VARCHAR(10) NULL",
                "data_conciliacao": "VARCHAR(10) NULL",
                "hora_conciliacao": "VARCHAR(8) NULL",
                "usuario_conciliacao": "VARCHAR(80) NULL",
                "data_inclusao": "VARCHAR(10) NULL",
                "hora_inclusao": "VARCHAR(8) NULL",
                "usuario_inclusao": "VARCHAR(80) NULL",
                "data_alteracao": "VARCHAR(10) NULL",
                "hora_alteracao": "VARCHAR(8) NULL",
                "usuario_alteracao": "VARCHAR(80) NULL",
                "categorias_json": "JSON NULL",
                "departamentos_json": "JSON NULL",
            }
            for coluna, definicao in colunas_adicionais.items():
                if coluna not in colunas_existentes:
                    cursor.execute(
                        "ALTER TABLE raw_omie_movimentos_financeiros "
                        f"ADD COLUMN `{coluna}` {definicao}"
                    )

            cursor.execute("SHOW INDEX FROM raw_omie_movimentos_financeiros")
            indices = cursor.fetchall()
            colunas_pk = [
                linha["Column_name"]
                for linha in sorted(indices, key=lambda item: item["Seq_in_index"])
                if linha["Key_name"] == "PRIMARY"
            ]

            if colunas_pk == ["codigo_titulo"]:
                cursor.execute(
                    """
                    UPDATE raw_omie_movimentos_financeiros
                    SET chave_movimento = CASE
                        WHEN codigo_movimento_conta_corrente IS NOT NULL
                             AND codigo_titulo IS NOT NULL
                            THEN CONCAT(
                                'CC:', codigo_movimento_conta_corrente,
                                ':TIT:', codigo_titulo
                            )
                        WHEN codigo_movimento_conta_corrente IS NOT NULL
                             AND codigo_cliente IS NOT NULL
                            THEN CONCAT(
                                'CC:', codigo_movimento_conta_corrente,
                                ':CLI:', codigo_cliente
                            )
                        WHEN codigo_movimento_conta_corrente IS NOT NULL
                            THEN CONCAT('CC:', codigo_movimento_conta_corrente)
                        WHEN codigo_titulo IS NOT NULL
                            THEN CONCAT('TIT:', codigo_titulo)
                        ELSE CONCAT(
                            'HASH:',
                            SHA2(CAST(dados_json AS CHAR CHARACTER SET utf8mb4), 256)
                        )
                    END
                    WHERE chave_movimento IS NULL
                    """
                )
                cursor.execute(
                    """
                    SELECT COUNT(*) AS duplicadas
                    FROM (
                        SELECT chave_movimento
                        FROM raw_omie_movimentos_financeiros
                        GROUP BY chave_movimento
                        HAVING COUNT(*) > 1
                    ) AS chaves_duplicadas
                    """
                )
                if cursor.fetchone()["duplicadas"]:
                    raise RuntimeError(
                        "A migracao encontrou chaves de movimento duplicadas."
                    )

                cursor.execute(
                    """
                    SELECT COUNT(*) AS existe
                    FROM information_schema.REFERENTIAL_CONSTRAINTS
                    WHERE CONSTRAINT_SCHEMA = %s
                      AND TABLE_NAME = 'raw_omie_movimentos_financeiros'
                      AND CONSTRAINT_NAME = 'fk_raw_omie_mf_cliente'
                    """,
                    (MYSQL_DATABASE,),
                )
                if cursor.fetchone()["existe"]:
                    cursor.execute(
                        "ALTER TABLE raw_omie_movimentos_financeiros "
                        "DROP FOREIGN KEY fk_raw_omie_mf_cliente"
                    )

                cursor.execute(
                    """
                    ALTER TABLE raw_omie_movimentos_financeiros
                        DROP PRIMARY KEY,
                        MODIFY chave_movimento VARCHAR(100) NOT NULL,
                        MODIFY codigo_titulo BIGINT NULL,
                        MODIFY codigo_cliente BIGINT NULL,
                        ADD PRIMARY KEY (chave_movimento)
                    """
                )

            cursor.execute("SHOW INDEX FROM raw_omie_movimentos_financeiros")
            indices_existentes = {linha["Key_name"] for linha in cursor.fetchall()}
            indices_adicionais = {
                "idx_raw_omie_mf_titulo": "codigo_titulo",
                "idx_raw_omie_mf_titulo_vinculado": "codigo_titulo_vinculado",
                "idx_raw_omie_mf_contrato": "codigo_contrato",
                "idx_raw_omie_mf_ordem_servico": "codigo_ordem_servico",
                "idx_raw_omie_mf_nota_fiscal": "codigo_nota_fiscal",
                "idx_raw_omie_mf_movimento_cc": "codigo_movimento_conta_corrente",
            }
            for indice, coluna in indices_adicionais.items():
                if indice not in indices_existentes:
                    cursor.execute(
                        "ALTER TABLE raw_omie_movimentos_financeiros "
                        f"ADD INDEX `{indice}` (`{coluna}`)"
                    )

            cursor.execute(
                """
                SELECT COUNT(*) AS existe
                FROM information_schema.REFERENTIAL_CONSTRAINTS
                WHERE CONSTRAINT_SCHEMA = %s
                  AND TABLE_NAME = 'raw_omie_movimentos_financeiros'
                  AND CONSTRAINT_NAME = 'fk_raw_omie_mf_cliente'
                """,
                (MYSQL_DATABASE,),
            )
            if not cursor.fetchone()["existe"]:
                cursor.execute(
                    """
                    ALTER TABLE raw_omie_movimentos_financeiros
                    ADD CONSTRAINT fk_raw_omie_mf_cliente
                        FOREIGN KEY (codigo_cliente)
                        REFERENCES raw_omie_clientes (codigo_cliente_omie)
                        ON UPDATE CASCADE
                        ON DELETE RESTRICT
                    """
                )
        conn.commit()


def buscar_clientes_ausentes(movimentos: list[Any]) -> list[int]:
    codigos = sorted(
        {
            codigo
            for movimento in movimentos
            if isinstance(movimento, dict)
            for detalhes in [objeto(movimento.get("detalhes"))]
            for codigo in [inteiro(detalhes.get("nCodCliente"))]
            if codigo
        }
    )

    if not codigos:
        return []

    placeholders = ", ".join(["%s"] * len(codigos))
    with conectar_mysql() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT codigo_cliente_omie
                FROM raw_omie_clientes
                WHERE codigo_cliente_omie IN ({placeholders})
                """,
                codigos,
            )
            existentes = {
                int(linha["codigo_cliente_omie"]) for linha in cursor.fetchall()
            }

    return [codigo for codigo in codigos if codigo not in existentes]


def garantir_clientes_cadastrados(movimentos: list[Any]) -> int:
    codigos_ausentes = buscar_clientes_ausentes(movimentos)
    if not codigos_ausentes:
        return 0

    logging.warning(
        "Encontrados %s clientes/fornecedores ausentes em raw_omie_clientes. "
        "Consultando cadastros antes de gravar os movimentos.",
        len(codigos_ausentes),
    )
    clientes = [consultar_cliente(codigo) for codigo in codigos_ausentes]
    return listar_clientes_omie.salvar_clientes_no_banco(clientes)


def buscar_titulos_ausentes(codigos: list[int]) -> list[int]:
    codigos = sorted({codigo for codigo in codigos if codigo})
    if not codigos:
        return []

    placeholders = ", ".join(["%s"] * len(codigos))
    with conectar_mysql() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT codigo_titulo
                FROM raw_omie_titulos_lancados
                WHERE codigo_titulo IN ({placeholders})
                """,
                codigos,
            )
            existentes = {int(linha["codigo_titulo"]) for linha in cursor.fetchall()}

    return [codigo for codigo in codigos if codigo not in existentes]


def importar_titulos_ausentes(codigos: list[int]) -> int:
    codigos_ausentes = buscar_titulos_ausentes(codigos)
    if not codigos_ausentes:
        return 0

    logging.warning(
        "Encontrados %s titulos ausentes em raw_omie_titulos_lancados. "
        "Consultando os titulos antes de criar os vinculos.",
        len(codigos_ausentes),
    )
    titulos: list[dict[str, Any]] = []
    for posicao, codigo in enumerate(codigos_ausentes, start=1):
        dados = consultar_titulo(codigo)
        encontrados = dados.get("titulosEncontrados", [])
        if isinstance(encontrados, list):
            titulos.extend(
                titulo for titulo in encontrados if isinstance(titulo, dict)
            )
        if posicao % 25 == 0:
            logging.info(
                "%s/%s titulos ausentes consultados.",
                posicao,
                len(codigos_ausentes),
            )

    if titulos:
        listar_titulos_lancados_omie.salvar_titulos_no_banco(
            {"titulosEncontrados": titulos}
        )

    ainda_ausentes = buscar_titulos_ausentes(codigos_ausentes)
    if ainda_ausentes:
        raise RuntimeError(
            "Nao foi possivel criar o vinculo para os titulos: "
            + ", ".join(str(codigo) for codigo in ainda_ausentes[:20])
        )

    return len(codigos_ausentes)


def garantir_titulos_dos_movimentos(movimentos: list[Any]) -> int:
    codigos = [
        codigo
        for movimento in movimentos
        if isinstance(movimento, dict)
        for detalhes in [objeto(movimento.get("detalhes"))]
        if not str(detalhes.get("cGrupo") or "").startswith("PREVISAO_")
        for codigo in [inteiro(detalhes.get("nCodTitulo"))]
        if codigo
    ]
    return importar_titulos_ausentes(codigos)


def sincronizar_titulos_da_tabela() -> int:
    with conectar_mysql() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT movimento.codigo_titulo
                FROM raw_omie_movimentos_financeiros AS movimento
                LEFT JOIN raw_omie_titulos_lancados AS titulo
                    ON titulo.codigo_titulo = movimento.codigo_titulo
                WHERE movimento.codigo_titulo IS NOT NULL
                  AND movimento.codigo_titulo <> 0
                  AND movimento.grupo NOT LIKE 'PREVISAO\\_%'
                  AND titulo.codigo_titulo IS NULL
                """
            )
            codigos = [int(linha["codigo_titulo"]) for linha in cursor.fetchall()]

    importados = importar_titulos_ausentes(codigos)

    with conectar_mysql() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE raw_omie_movimentos_financeiros AS movimento
                LEFT JOIN raw_omie_titulos_lancados AS titulo
                    ON titulo.codigo_titulo = movimento.codigo_titulo
                SET movimento.codigo_titulo_vinculado = titulo.codigo_titulo
                WHERE movimento.codigo_titulo_vinculado IS NULL
                  AND titulo.codigo_titulo IS NOT NULL
                """
            )
        conn.commit()

    return importados


def garantir_fk_titulo() -> None:
    with conectar_mysql() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) AS existe
                FROM information_schema.REFERENTIAL_CONSTRAINTS
                WHERE CONSTRAINT_SCHEMA = %s
                  AND TABLE_NAME = 'raw_omie_movimentos_financeiros'
                  AND CONSTRAINT_NAME = 'fk_raw_omie_mf_titulo'
                """,
                (MYSQL_DATABASE,),
            )
            if not cursor.fetchone()["existe"]:
                cursor.execute(
                    """
                    ALTER TABLE raw_omie_movimentos_financeiros
                    ADD CONSTRAINT fk_raw_omie_mf_titulo
                        FOREIGN KEY (codigo_titulo_vinculado)
                        REFERENCES raw_omie_titulos_lancados (codigo_titulo)
                        ON UPDATE CASCADE
                        ON DELETE RESTRICT
                    """
                )
        conn.commit()


def salvar_movimentos_no_banco(dados: dict[str, Any]) -> int:
    movimentos = dados.get("movimentos", [])
    if not isinstance(movimentos, list):
        raise RuntimeError("Resposta inesperada: campo 'movimentos' nao e uma lista.")

    titulos_inseridos = garantir_titulos_dos_movimentos(movimentos)
    if titulos_inseridos:
        logging.info(
            "%s titulos ausentes foram gravados em raw_omie_titulos_lancados.",
            titulos_inseridos,
        )

    clientes_inseridos = garantir_clientes_cadastrados(movimentos)
    if clientes_inseridos:
        logging.info(
            "%s clientes/fornecedores ausentes foram gravados em raw_omie_clientes.",
            clientes_inseridos,
        )

    registros = []
    for movimento in movimentos:
        if not isinstance(movimento, dict):
            continue

        detalhes = objeto(movimento.get("detalhes"))
        resumo = objeto(movimento.get("resumo"))
        codigo_titulo = inteiro(detalhes.get("nCodTitulo"))
        codigo_cliente = inteiro(detalhes.get("nCodCliente"))
        grupo = texto(detalhes.get("cGrupo"))
        codigo_titulo_vinculado = (
            None
            if not codigo_titulo or str(grupo or "").startswith("PREVISAO_")
            else codigo_titulo
        )

        registros.append(
            {
                "chave_movimento": gerar_chave_movimento(movimento),
                "codigo_titulo": codigo_titulo,
                "codigo_titulo_vinculado": codigo_titulo_vinculado,
                "codigo_titulo_repeticao": inteiro(detalhes.get("nCodTitRepet")),
                "codigo_titulo_integracao": texto(detalhes.get("cCodIntTitulo")),
                "codigo_cliente": codigo_cliente,
                "cnpj_cpf_cliente": texto(detalhes.get("cCPFCNPJCliente")),
                "codigo_contrato": inteiro(detalhes.get("nCodCtr")),
                "numero_contrato": texto(detalhes.get("cNumCtr")),
                "codigo_ordem_servico": inteiro(detalhes.get("nCodOS")),
                "numero_ordem_servico": texto(detalhes.get("cNumOS")),
                "codigo_categoria": texto(detalhes.get("cCodCateg")),
                "grupo": grupo,
                "natureza": texto(detalhes.get("cNatureza")),
                "operacao": texto(detalhes.get("cOperacao")),
                "numero_documento_fiscal": texto(detalhes.get("cNumDocFiscal")),
                "numero_parcela": texto(detalhes.get("cNumParcela")),
                "numero_titulo": texto(detalhes.get("cNumTitulo")),
                "origem": texto(detalhes.get("cOrigem")),
                "status_titulo": texto(detalhes.get("cStatus")),
                "codigo_tipo_documento": texto(detalhes.get("cTipo")),
                "data_emissao": texto(detalhes.get("dDtEmissao")),
                "data_pagamento": texto(detalhes.get("dDtPagamento")),
                "data_previsao": texto(detalhes.get("dDtPrevisao")),
                "data_registro": texto(detalhes.get("dDtRegistro")),
                "data_vencimento": texto(detalhes.get("dDtVenc")),
                "id_conta_corrente": inteiro(detalhes.get("nCodCC")),
                "valor_titulo": decimal(detalhes.get("nValorTitulo")),
                "valor_pis": decimal(detalhes.get("nValorPIS")),
                "retem_pis": texto(detalhes.get("cRetPIS")),
                "valor_cofins": decimal(detalhes.get("nValorCOFINS")),
                "retem_cofins": texto(detalhes.get("cRetCOFINS")),
                "valor_csll": decimal(detalhes.get("nValorCSLL")),
                "retem_csll": texto(detalhes.get("cRetCSLL")),
                "valor_ir": decimal(detalhes.get("nValorIR")),
                "retem_ir": texto(detalhes.get("cRetIR")),
                "valor_iss": decimal(detalhes.get("nValorISS")),
                "retem_iss": texto(detalhes.get("cRetISS")),
                "valor_inss": decimal(detalhes.get("nValorINSS")),
                "retem_inss": texto(detalhes.get("cRetINSS")),
                "codigo_projeto": inteiro(detalhes.get("cCodProjeto")),
                "observacao": texto(detalhes.get("observacao")),
                "codigo_vendedor": inteiro(detalhes.get("cCodVendedor")),
                "codigo_comprador": inteiro(detalhes.get("nCodComprador")),
                "codigo_barras": texto(detalhes.get("cCodigoBarras")),
                "nsu": texto(detalhes.get("cNSU")),
                "codigo_nota_fiscal": inteiro(detalhes.get("nCodNF")),
                "numero_boleto": texto(detalhes.get("cNumBoleto")),
                "chave_nfe": texto(detalhes.get("cChaveNFe")),
                "codigo_movimento_conta_corrente": inteiro(
                    detalhes.get("nCodMovCC")
                ),
                "valor_movimento_conta_corrente": decimal(
                    detalhes.get("nValorMovCC")
                ),
                "codigo_movimento_repeticao": inteiro(
                    detalhes.get("nCodMovCCRepet")
                ),
                "desconto_movimento": decimal(detalhes.get("nDesconto")),
                "juros_movimento": decimal(detalhes.get("nJuros")),
                "multa_movimento": decimal(detalhes.get("nMulta")),
                "codigo_baixa": inteiro(detalhes.get("nCodBaixa")),
                "data_credito": texto(detalhes.get("dDtCredito")),
                "data_conciliacao": texto(detalhes.get("dDtConcilia")),
                "hora_conciliacao": texto(detalhes.get("cHrConcilia")),
                "usuario_conciliacao": texto(detalhes.get("cUsConcilia")),
                "data_inclusao": texto(detalhes.get("dDtInc")),
                "hora_inclusao": texto(detalhes.get("cHrInc")),
                "usuario_inclusao": texto(detalhes.get("cUsInc")),
                "data_alteracao": texto(detalhes.get("dDtAlt")),
                "hora_alteracao": texto(detalhes.get("cHrAlt")),
                "usuario_alteracao": texto(detalhes.get("cUsAlt")),
                "liquidado": texto(resumo.get("cLiquidado")),
                "valor_desconto": decimal(resumo.get("nDesconto")),
                "valor_juros": decimal(resumo.get("nJuros")),
                "valor_multa": decimal(resumo.get("nMulta")),
                "valor_aberto": decimal(resumo.get("nValAberto")),
                "valor_liquido": decimal(resumo.get("nValLiquido")),
                "valor_pago": decimal(resumo.get("nValPago")),
                "pagina_api": inteiro(dados.get("nPagina")),
                "total_de_paginas_api": inteiro(dados.get("nTotPaginas")),
                "registros_api": inteiro(dados.get("nRegistros")),
                "total_de_registros_api": inteiro(dados.get("nTotRegistros")),
                "detalhes_json": json.dumps(detalhes, ensure_ascii=False),
                "resumo_json": json.dumps(resumo, ensure_ascii=False),
                "categorias_json": json.dumps(
                    movimento.get("categorias", []),
                    ensure_ascii=False,
                ),
                "departamentos_json": json.dumps(
                    movimento.get("departamentos", []),
                    ensure_ascii=False,
                ),
                "dados_json": json.dumps(movimento, ensure_ascii=False),
                "dados_flat_json": json.dumps(
                    achatar_json(movimento),
                    ensure_ascii=False,
                ),
            }
        )

    if not registros:
        return 0

    colunas = list(registros[0])
    placeholders = ", ".join(f"%({coluna})s" for coluna in colunas)
    lista_colunas = ", ".join(f"`{coluna}`" for coluna in colunas)
    atualizacoes = ", ".join(
        f"`{coluna}` = VALUES(`{coluna}`)"
        for coluna in colunas
        if coluna != "chave_movimento"
    )
    sql = f"""
        INSERT INTO raw_omie_movimentos_financeiros ({lista_colunas})
        VALUES ({placeholders})
        ON DUPLICATE KEY UPDATE {atualizacoes}
    """

    with conectar_mysql() as conn:
        with conn.cursor() as cursor:
            cursor.executemany(sql, registros)
        conn.commit()

    return len(registros)


def extrair_e_salvar(
    pagina_inicial: int | None = None,
    pagina_final: int | None = None,
) -> int:
    ultima_pagina = carregar_checkpoint()
    pagina = pagina_inicial or (ultima_pagina + 1 if ultima_pagina else 1)
    total = 0
    total_paginas: int | None = None

    if pagina > 1:
        logging.info("Retomando a extracao a partir da pagina %s.", pagina)

    while True:
        if pagina_final is not None and pagina > pagina_final:
            logging.info("Pagina final configurada (%s) atingida.", pagina_final)
            break

        try:
            dados = chamar_api(pagina)
        except PaginaSemRegistros:
            logging.info("Pagina %s sem movimentos. Fim da consulta.", pagina)
            break

        movimentos = dados.get("movimentos", [])
        total_paginas_api = inteiro(dados.get("nTotPaginas"))
        if total_paginas_api is not None:
            total_paginas = total_paginas_api

        if not isinstance(movimentos, list):
            raise RuntimeError("Resposta inesperada: campo 'movimentos' nao e uma lista.")
        if not movimentos:
            logging.info("Pagina %s sem movimentos. Fim da consulta.", pagina)
            break

        salvos = salvar_movimentos_no_banco(dados)
        total += salvos
        if pagina_final is None:
            salvar_checkpoint(pagina, total)
        logging.info(
            "Pagina %s/%s processada: %s movimentos inseridos ou atualizados.",
            pagina,
            total_paginas or "?",
            salvos,
        )

        if total_paginas is not None and pagina >= total_paginas:
            logging.info("Ultima pagina informada pela API processada.")
            break

        pagina += 1
        time.sleep(INTERVALO_ENTRE_PAGINAS)

    if pagina_final is None:
        remover_checkpoint()
    return total


def argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extrai movimentos financeiros da API Omie para o MySQL."
    )
    parser.add_argument(
        "--pagina-inicial",
        type=int,
        help="Pagina inicial da extracao. Sobrescreve um checkpoint existente.",
    )
    parser.add_argument(
        "--pagina-final",
        type=int,
        help="Pagina final da extracao. Use para testes controlados.",
    )
    return parser.parse_args()


def main() -> int:
    configurar_logging()
    args = argumentos()

    if not APP_KEY or not APP_SECRET:
        logging.error("Defina OMIE_APP_KEY e OMIE_APP_SECRET no arquivo .env.")
        return 1

    try:
        preparar_banco()
        titulos_sincronizados = sincronizar_titulos_da_tabela()
        if titulos_sincronizados:
            logging.info(
                "%s titulos foram sincronizados antes de ativar a chave estrangeira.",
                titulos_sincronizados,
            )
        garantir_fk_titulo()
        total = extrair_e_salvar(args.pagina_inicial, args.pagina_final)
    except (pymysql.MySQLError, requests.RequestException, RuntimeError, OSError) as exc:
        logging.exception("Erro ao executar extracao: %s", exc)
        return 1

    logging.info(
        "Extracao finalizada: %s movimentos gravados no banco %s.",
        total,
        MYSQL_DATABASE,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
