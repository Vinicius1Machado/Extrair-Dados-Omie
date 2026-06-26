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


BASE_DIR = Path(__file__).resolve().parent.parent
API_URL = "https://app.omie.com.br/api/v1/financas/contapagar/"
REGISTROS_POR_PAGINA = 20
MAX_TENTATIVAS = 5
INTERVALO_ENTRE_PAGINAS = 0.3
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "listar_contas_pagar_omie.log"
CHECKPOINT_FILE = LOG_DIR / "listar_contas_pagar_omie.checkpoint.json"

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
        "call": "ListarContasPagar",
        "param": [
            {
                "pagina": pagina,
                "registros_por_pagina": REGISTROS_POR_PAGINA,
                "apenas_importado_api": "N",
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
                CREATE TABLE IF NOT EXISTS raw_omie_contas_pagar (
                    codigo_lancamento_omie BIGINT NOT NULL,
                    codigo_lancamento_integracao VARCHAR(120) NULL,
                    codigo_cliente_fornecedor BIGINT NOT NULL,
                    codigo_categoria VARCHAR(40) NULL,
                    codigo_tipo_documento VARCHAR(20) NULL,
                    codigo_projeto BIGINT NULL,
                    id_conta_corrente BIGINT NULL,
                    id_origem VARCHAR(20) NULL,
                    numero_documento VARCHAR(120) NULL,
                    numero_documento_fiscal VARCHAR(120) NULL,
                    numero_parcela VARCHAR(30) NULL,
                    codigo_barras_ficha_compensacao VARCHAR(255) NULL,
                    data_emissao VARCHAR(10) NULL,
                    data_entrada VARCHAR(10) NULL,
                    data_previsao VARCHAR(10) NULL,
                    data_vencimento VARCHAR(10) NULL,
                    valor_documento DECIMAL(18, 4) NULL,
                    valor_cofins DECIMAL(18, 4) NULL,
                    valor_csll DECIMAL(18, 4) NULL,
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
                    bloqueado CHAR(1) NULL,
                    baixa_bloqueada CHAR(1) NULL,
                    bloquear_exclusao CHAR(1) NULL,
                    cnab_codigo_forma_pagamento VARCHAR(20) NULL,
                    cnab_codigo_barras_boleto VARCHAR(255) NULL,
                    cnab_juros_boleto DECIMAL(18, 4) NULL,
                    cnab_multa_boleto DECIMAL(18, 4) NULL,
                    cnab_pix_qrcode TEXT NULL,
                    cnab_banco_transferencia VARCHAR(20) NULL,
                    cnab_agencia_transferencia VARCHAR(30) NULL,
                    cnab_conta_corrente_transferencia VARCHAR(60) NULL,
                    cnab_cpf_cnpj_transferencia VARCHAR(30) NULL,
                    cnab_nome_transferencia VARCHAR(200) NULL,
                    cnab_finalidade_transferencia VARCHAR(80) NULL,
                    omie_importado_api CHAR(1) NULL,
                    omie_data_inclusao VARCHAR(10) NULL,
                    omie_hora_inclusao VARCHAR(8) NULL,
                    omie_usuario_inclusao VARCHAR(80) NULL,
                    omie_data_alteracao VARCHAR(10) NULL,
                    omie_hora_alteracao VARCHAR(8) NULL,
                    omie_usuario_alteracao VARCHAR(80) NULL,
                    pagina_api INT NULL,
                    total_de_paginas_api INT NULL,
                    registros_api INT NULL,
                    total_de_registros_api INT NULL,
                    categorias_json JSON NOT NULL,
                    distribuicao_json JSON NOT NULL,
                    cnab_integracao_bancaria_json JSON NOT NULL,
                    info_json JSON NOT NULL,
                    dados_json JSON NOT NULL,
                    dados_flat_json JSON NOT NULL,
                    extraido_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    PRIMARY KEY (codigo_lancamento_omie),
                    KEY idx_raw_omie_cp_fornecedor (codigo_cliente_fornecedor),
                    KEY idx_raw_omie_cp_categoria (codigo_categoria),
                    KEY idx_raw_omie_cp_conta_corrente (id_conta_corrente),
                    KEY idx_raw_omie_cp_projeto (codigo_projeto),
                    KEY idx_raw_omie_cp_tipo_documento (codigo_tipo_documento),
                    KEY idx_raw_omie_cp_status (status_titulo),
                    KEY idx_raw_omie_cp_vencimento (data_vencimento),
                    CONSTRAINT fk_raw_omie_cp_fornecedor
                        FOREIGN KEY (codigo_cliente_fornecedor)
                        REFERENCES raw_omie_clientes (codigo_cliente_omie)
                        ON UPDATE CASCADE
                        ON DELETE RESTRICT
                )
                CHARACTER SET utf8mb4
                COLLATE utf8mb4_unicode_ci
                """
            )
        conn.commit()


def salvar_contas_pagar_no_banco(dados: dict[str, Any]) -> int:
    contas = dados.get("conta_pagar_cadastro", [])
    if not isinstance(contas, list):
        raise RuntimeError("Resposta inesperada: campo 'conta_pagar_cadastro' nao e uma lista.")

    registros = []
    for conta in contas:
        if not isinstance(conta, dict):
            continue

        codigo_lancamento_omie = inteiro(conta.get("codigo_lancamento_omie"))
        codigo_cliente_fornecedor = inteiro(conta.get("codigo_cliente_fornecedor"))
        if not codigo_lancamento_omie or not codigo_cliente_fornecedor:
            continue

        cnab = objeto(conta.get("cnab_integracao_bancaria"))
        info = objeto(conta.get("info"))

        registros.append(
            {
                "codigo_lancamento_omie": codigo_lancamento_omie,
                "codigo_lancamento_integracao": texto(conta.get("codigo_lancamento_integracao")),
                "codigo_cliente_fornecedor": codigo_cliente_fornecedor,
                "codigo_categoria": texto(conta.get("codigo_categoria")),
                "codigo_tipo_documento": texto(conta.get("codigo_tipo_documento")),
                "codigo_projeto": inteiro(conta.get("codigo_projeto")),
                "id_conta_corrente": inteiro(conta.get("id_conta_corrente")),
                "id_origem": texto(conta.get("id_origem")),
                "numero_documento": texto(conta.get("numero_documento")),
                "numero_documento_fiscal": texto(conta.get("numero_documento_fiscal")),
                "numero_parcela": texto(conta.get("numero_parcela")),
                "codigo_barras_ficha_compensacao": texto(
                    conta.get("codigo_barras_ficha_compensacao")
                ),
                "data_emissao": texto(conta.get("data_emissao")),
                "data_entrada": texto(conta.get("data_entrada")),
                "data_previsao": texto(conta.get("data_previsao")),
                "data_vencimento": texto(conta.get("data_vencimento")),
                "valor_documento": decimal(conta.get("valor_documento")),
                "valor_cofins": decimal(conta.get("valor_cofins")),
                "valor_csll": decimal(conta.get("valor_csll")),
                "valor_ir": decimal(conta.get("valor_ir")),
                "valor_iss": decimal(conta.get("valor_iss")),
                "valor_pis": decimal(conta.get("valor_pis")),
                "retem_cofins": texto(conta.get("retem_cofins")),
                "retem_csll": texto(conta.get("retem_csll")),
                "retem_inss": texto(conta.get("retem_inss")),
                "retem_ir": texto(conta.get("retem_ir")),
                "retem_iss": texto(conta.get("retem_iss")),
                "retem_pis": texto(conta.get("retem_pis")),
                "status_titulo": texto(conta.get("status_titulo")),
                "bloqueado": texto(conta.get("bloqueado")),
                "baixa_bloqueada": texto(conta.get("baixa_bloqueada")),
                "bloquear_exclusao": texto(conta.get("bloquear_exclusao")),
                "cnab_codigo_forma_pagamento": texto(cnab.get("codigo_forma_pagamento")),
                "cnab_codigo_barras_boleto": texto(cnab.get("codigo_barras_boleto")),
                "cnab_juros_boleto": decimal(cnab.get("juros_boleto")),
                "cnab_multa_boleto": decimal(cnab.get("multa_boleto")),
                "cnab_pix_qrcode": texto(cnab.get("pix_qrcode")),
                "cnab_banco_transferencia": texto(cnab.get("banco_transferencia")),
                "cnab_agencia_transferencia": texto(cnab.get("agencia_transferencia")),
                "cnab_conta_corrente_transferencia": texto(
                    cnab.get("conta_corrente_transferencia")
                ),
                "cnab_cpf_cnpj_transferencia": texto(cnab.get("cpf_cnpj_transferencia")),
                "cnab_nome_transferencia": texto(cnab.get("nome_transferencia")),
                "cnab_finalidade_transferencia": texto(cnab.get("finalidade_transferencia")),
                "omie_importado_api": texto(info.get("cImpAPI")),
                "omie_data_inclusao": texto(info.get("dInc")),
                "omie_hora_inclusao": texto(info.get("hInc")),
                "omie_usuario_inclusao": texto(info.get("uInc")),
                "omie_data_alteracao": texto(info.get("dAlt")),
                "omie_hora_alteracao": texto(info.get("hAlt")),
                "omie_usuario_alteracao": texto(info.get("uAlt")),
                "pagina_api": inteiro(dados.get("pagina")),
                "total_de_paginas_api": inteiro(dados.get("total_de_paginas")),
                "registros_api": inteiro(dados.get("registros")),
                "total_de_registros_api": inteiro(dados.get("total_de_registros")),
                "categorias_json": json_lista(conta.get("categorias")),
                "distribuicao_json": json_lista(conta.get("distribuicao")),
                "cnab_integracao_bancaria_json": json_objeto(conta.get("cnab_integracao_bancaria")),
                "info_json": json_objeto(conta.get("info")),
                "dados_json": json.dumps(conta, ensure_ascii=False),
                "dados_flat_json": json.dumps(achatar_json(conta), ensure_ascii=False),
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
        if coluna != "codigo_lancamento_omie"
    )
    sql = f"""
        INSERT INTO raw_omie_contas_pagar ({lista_colunas})
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
            logging.info("Pagina %s sem contas a pagar. Fim da consulta.", pagina)
            break

        contas = dados.get("conta_pagar_cadastro", [])
        total_paginas_api = dados.get("total_de_paginas")
        if total_paginas_api is not None:
            total_paginas = int(total_paginas_api)

        if not isinstance(contas, list):
            raise RuntimeError("Resposta inesperada: campo 'conta_pagar_cadastro' nao e uma lista.")

        if not contas:
            logging.info("Pagina %s sem contas a pagar. Fim da consulta.", pagina)
            break

        salvos = salvar_contas_pagar_no_banco(dados)
        total += salvos
        if pagina_final is None:
            salvar_checkpoint(pagina, total)
        logging.info(
            "Pagina %s/%s processada: %s contas a pagar gravadas no banco.",
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
    parser = argparse.ArgumentParser(description="Extrai contas a pagar da API Omie para o MySQL.")
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
        "Extracao finalizada: %s contas a pagar gravadas no banco %s.",
        total,
        MYSQL_DATABASE,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
