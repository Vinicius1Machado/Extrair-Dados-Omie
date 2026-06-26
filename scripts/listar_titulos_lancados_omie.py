import argparse
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


BASE_DIR = Path(__file__).resolve().parent.parent
API_URL = "https://app.omie.com.br/api/v1/financas/pesquisartitulos/"
CLIENTES_API_URL = "https://app.omie.com.br/api/v1/geral/clientes/"
REGISTROS_POR_PAGINA = 20
MAX_TENTATIVAS = 5
INTERVALO_ENTRE_PAGINAS = 0.3
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "listar_titulos_lancados_omie.log"
CHECKPOINT_FILE = LOG_DIR / "listar_titulos_lancados_omie.checkpoint.json"

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


def lista(valor: Any) -> list[Any]:
    return valor if isinstance(valor, list) else []


def json_objeto(valor: Any) -> str:
    return json.dumps(objeto(valor), ensure_ascii=False)


def json_lista(valor: Any) -> str:
    return json.dumps(lista(valor), ensure_ascii=False)


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


def chamar_api(pagina: int) -> dict[str, Any]:
    payload = {
        "call": "PesquisarLancamentos",
        "param": [
            {
                "nPagina": pagina,
                "nRegPorPagina": REGISTROS_POR_PAGINA,
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
                raise RuntimeError(dados.get("faultstring") or dados.get("faultcode"))
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


def conectar_mysql(usar_banco: bool = True, admin: bool = False) -> pymysql.connections.Connection:
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
                CREATE TABLE IF NOT EXISTS raw_omie_titulos_lancados (
                    codigo_titulo BIGINT NOT NULL,
                    codigo_titulo_repeticao BIGINT NULL,
                    codigo_titulo_integracao VARCHAR(120) NULL,
                    natureza CHAR(1) NULL,
                    codigo_cliente BIGINT NOT NULL,
                    cnpj_cpf_cliente VARCHAR(30) NULL,
                    codigo_categoria VARCHAR(40) NULL,
                    codigo_tipo_documento VARCHAR(20) NULL,
                    id_conta_corrente BIGINT NULL,
                    origem VARCHAR(20) NULL,
                    numero_titulo VARCHAR(120) NULL,
                    numero_documento_fiscal VARCHAR(120) NULL,
                    numero_parcela VARCHAR(30) NULL,
                    codigo_barras VARCHAR(255) NULL,
                    observacao TEXT NULL,
                    data_emissao VARCHAR(10) NULL,
                    data_pagamento VARCHAR(10) NULL,
                    data_previsao VARCHAR(10) NULL,
                    data_registro VARCHAR(10) NULL,
                    data_vencimento VARCHAR(10) NULL,
                    valor_titulo DECIMAL(18, 4) NULL,
                    valor_cofins DECIMAL(18, 4) NULL,
                    valor_csll DECIMAL(18, 4) NULL,
                    valor_inss DECIMAL(18, 4) NULL,
                    valor_ir DECIMAL(18, 4) NULL,
                    valor_iss DECIMAL(18, 4) NULL,
                    valor_pis DECIMAL(18, 4) NULL,
                    retem_cofins CHAR(1) NULL,
                    retem_csll CHAR(1) NULL,
                    retem_inss CHAR(1) NULL,
                    retem_ir CHAR(1) NULL,
                    retem_iss CHAR(1) NULL,
                    retem_pis CHAR(1) NULL,
                    status_titulo VARCHAR(40) NULL,
                    liquidado CHAR(1) NULL,
                    valor_aberto DECIMAL(18, 4) NULL,
                    valor_liquido DECIMAL(18, 4) NULL,
                    valor_pago DECIMAL(18, 4) NULL,
                    valor_desconto DECIMAL(18, 4) NULL,
                    valor_juros DECIMAL(18, 4) NULL,
                    valor_multa DECIMAL(18, 4) NULL,
                    primeiro_codigo_lancamento BIGINT NULL,
                    primeiro_id_lancamento_cc BIGINT NULL,
                    primeiro_codigo_lancamento_integracao VARCHAR(120) NULL,
                    primeiro_data_lancamento VARCHAR(10) NULL,
                    primeiro_valor_lancamento DECIMAL(18, 4) NULL,
                    primeiro_observacao_lancamento TEXT NULL,
                    primeiro_codigo_conta_corrente BIGINT NULL,
                    pagina_api INT NULL,
                    total_de_paginas_api INT NULL,
                    registros_api INT NULL,
                    total_de_registros_api INT NULL,
                    cabec_titulo_json JSON NOT NULL,
                    categorias_json JSON NOT NULL,
                    departamentos_json JSON NOT NULL,
                    lancamentos_json JSON NOT NULL,
                    resumo_json JSON NOT NULL,
                    dados_json JSON NOT NULL,
                    dados_flat_json JSON NOT NULL,
                    extraido_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    PRIMARY KEY (codigo_titulo),
                    KEY idx_raw_omie_tl_cliente (codigo_cliente),
                    KEY idx_raw_omie_tl_natureza (natureza),
                    KEY idx_raw_omie_tl_categoria (codigo_categoria),
                    KEY idx_raw_omie_tl_conta_corrente (id_conta_corrente),
                    KEY idx_raw_omie_tl_status (status_titulo),
                    KEY idx_raw_omie_tl_vencimento (data_vencimento),
                    KEY idx_raw_omie_tl_titulo_repeticao (codigo_titulo_repeticao),
                    KEY idx_raw_omie_tl_lancamento (primeiro_codigo_lancamento),
                    CONSTRAINT fk_raw_omie_tl_cliente
                        FOREIGN KEY (codigo_cliente)
                        REFERENCES raw_omie_clientes (codigo_cliente_omie)
                        ON UPDATE CASCADE
                        ON DELETE RESTRICT
                )
                CHARACTER SET utf8mb4
                COLLATE utf8mb4_unicode_ci
                """
            )
        conn.commit()


def buscar_clientes_ausentes(titulos: list[Any]) -> list[int]:
    codigos = sorted(
        {
            codigo
            for titulo in titulos
            if isinstance(titulo, dict)
            for cabecalho in [objeto(titulo.get("cabecTitulo"))]
            for codigo in [inteiro(cabecalho.get("nCodCliente"))]
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
            existentes = {int(linha["codigo_cliente_omie"]) for linha in cursor.fetchall()}

    return [codigo for codigo in codigos if codigo not in existentes]


def garantir_clientes_cadastrados(titulos: list[Any]) -> int:
    codigos_ausentes = buscar_clientes_ausentes(titulos)
    if not codigos_ausentes:
        return 0

    logging.warning(
        "Encontrados %s clientes/fornecedores ausentes em raw_omie_clientes: %s. "
        "Consultando cadastros antes de gravar titulos lancados.",
        len(codigos_ausentes),
        codigos_ausentes,
    )
    clientes = [consultar_cliente(codigo) for codigo in codigos_ausentes]
    return listar_clientes_omie.salvar_clientes_no_banco(clientes)


def salvar_titulos_no_banco(dados: dict[str, Any]) -> int:
    titulos = dados.get("titulosEncontrados", [])
    if not isinstance(titulos, list):
        raise RuntimeError("Resposta inesperada: campo 'titulosEncontrados' nao e uma lista.")

    clientes_inseridos = garantir_clientes_cadastrados(titulos)
    if clientes_inseridos:
        logging.info(
            "%s clientes/fornecedores ausentes foram gravados em raw_omie_clientes.",
            clientes_inseridos,
        )

    registros = []
    for titulo in titulos:
        if not isinstance(titulo, dict):
            continue

        cabecalho = objeto(titulo.get("cabecTitulo"))
        resumo = objeto(titulo.get("resumo"))
        lancamentos = lista(titulo.get("lancamentos"))
        primeiro_lancamento = objeto(lancamentos[0]) if lancamentos else {}

        codigo_titulo = inteiro(cabecalho.get("nCodTitulo"))
        codigo_cliente = inteiro(cabecalho.get("nCodCliente"))
        if not codigo_titulo or not codigo_cliente:
            continue

        registros.append(
            {
                "codigo_titulo": codigo_titulo,
                "codigo_titulo_repeticao": inteiro(cabecalho.get("nCodTitRepet")),
                "codigo_titulo_integracao": texto(cabecalho.get("cCodIntTitulo")),
                "natureza": texto(cabecalho.get("cNatureza")),
                "codigo_cliente": codigo_cliente,
                "cnpj_cpf_cliente": texto(cabecalho.get("cCPFCNPJCliente")),
                "codigo_categoria": texto(cabecalho.get("cCodCateg")),
                "codigo_tipo_documento": texto(cabecalho.get("cTipo")),
                "id_conta_corrente": inteiro(cabecalho.get("nCodCC")),
                "origem": texto(cabecalho.get("cOrigem")),
                "numero_titulo": texto(cabecalho.get("cNumTitulo")),
                "numero_documento_fiscal": texto(cabecalho.get("cNumDocFiscal")),
                "numero_parcela": texto(cabecalho.get("cNumParcela")),
                "codigo_barras": texto(cabecalho.get("cCodigoBarras")),
                "observacao": texto(cabecalho.get("observacao")),
                "data_emissao": texto(cabecalho.get("dDtEmissao")),
                "data_pagamento": texto(cabecalho.get("dDtPagamento")),
                "data_previsao": texto(cabecalho.get("dDtPrevisao")),
                "data_registro": texto(cabecalho.get("dDtRegistro")),
                "data_vencimento": texto(cabecalho.get("dDtVenc")),
                "valor_titulo": decimal(cabecalho.get("nValorTitulo")),
                "valor_cofins": decimal(cabecalho.get("nValorCOFINS")),
                "valor_csll": decimal(cabecalho.get("nValorCSLL")),
                "valor_inss": decimal(cabecalho.get("nValorINSS")),
                "valor_ir": decimal(cabecalho.get("nValorIR")),
                "valor_iss": decimal(cabecalho.get("nValorISS")),
                "valor_pis": decimal(cabecalho.get("nValorPIS")),
                "retem_cofins": texto(cabecalho.get("cRetCOFINS")),
                "retem_csll": texto(cabecalho.get("cRetCSLL")),
                "retem_inss": texto(cabecalho.get("cRetINSS")),
                "retem_ir": texto(cabecalho.get("cRetIR")),
                "retem_iss": texto(cabecalho.get("cRetISS")),
                "retem_pis": texto(cabecalho.get("cRetPIS")),
                "status_titulo": texto(cabecalho.get("cStatus")),
                "liquidado": texto(resumo.get("cLiquidado")),
                "valor_aberto": decimal(resumo.get("nValAberto")),
                "valor_liquido": decimal(resumo.get("nValLiquido")),
                "valor_pago": decimal(resumo.get("nValPago")),
                "valor_desconto": decimal(resumo.get("nDesconto")),
                "valor_juros": decimal(resumo.get("nJuros")),
                "valor_multa": decimal(resumo.get("nMulta")),
                "primeiro_codigo_lancamento": inteiro(primeiro_lancamento.get("nCodLanc")),
                "primeiro_id_lancamento_cc": inteiro(primeiro_lancamento.get("nIdLancCC")),
                "primeiro_codigo_lancamento_integracao": texto(
                    primeiro_lancamento.get("cCodIntLanc")
                ),
                "primeiro_data_lancamento": texto(primeiro_lancamento.get("dDtLanc")),
                "primeiro_valor_lancamento": decimal(primeiro_lancamento.get("nValLanc")),
                "primeiro_observacao_lancamento": texto(primeiro_lancamento.get("cObsLanc")),
                "primeiro_codigo_conta_corrente": inteiro(primeiro_lancamento.get("nCodCC")),
                "pagina_api": inteiro(dados.get("nPagina")),
                "total_de_paginas_api": inteiro(dados.get("nTotPaginas")),
                "registros_api": inteiro(dados.get("nRegistros")),
                "total_de_registros_api": inteiro(dados.get("nTotRegistros")),
                "cabec_titulo_json": json_objeto(titulo.get("cabecTitulo")),
                "categorias_json": json.dumps(lista(cabecalho.get("aCodCateg")), ensure_ascii=False),
                "departamentos_json": json_lista(titulo.get("departamentos")),
                "lancamentos_json": json_lista(titulo.get("lancamentos")),
                "resumo_json": json_objeto(titulo.get("resumo")),
                "dados_json": json.dumps(titulo, ensure_ascii=False),
                "dados_flat_json": json.dumps(achatar_json(titulo), ensure_ascii=False),
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
        if coluna != "codigo_titulo"
    )
    sql = f"""
        INSERT INTO raw_omie_titulos_lancados ({lista_colunas})
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
            logging.info("Pagina %s sem titulos lancados. Fim da consulta.", pagina)
            break

        titulos = dados.get("titulosEncontrados", [])
        total_paginas_api = dados.get("nTotPaginas")
        if total_paginas_api is not None:
            total_paginas = int(total_paginas_api)

        if not isinstance(titulos, list):
            raise RuntimeError("Resposta inesperada: campo 'titulosEncontrados' nao e uma lista.")

        if not titulos:
            logging.info("Pagina %s sem titulos lancados. Fim da consulta.", pagina)
            break

        salvos = salvar_titulos_no_banco(dados)
        total += salvos
        if pagina_final is None:
            salvar_checkpoint(pagina, total)
        logging.info(
            "Pagina %s/%s processada: %s titulos lancados gravados no banco.",
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
        description="Extrai titulos lancados da API Omie para o MySQL."
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
        total = extrair_e_salvar(args.pagina_inicial, args.pagina_final)
    except (pymysql.MySQLError, requests.RequestException, RuntimeError, OSError) as exc:
        logging.exception("Erro ao executar extracao: %s", exc)
        return 1

    logging.info(
        "Extracao finalizada: %s titulos lancados gravados no banco %s.",
        total,
        MYSQL_DATABASE,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
