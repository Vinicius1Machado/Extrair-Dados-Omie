import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import pymysql
import requests
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
API_URL = "https://app.omie.com.br/api/v1/geral/bancos/"
REGISTROS_POR_PAGINA = 100
MAX_TENTATIVAS = 5
INTERVALO_ENTRE_PAGINAS = 0.3
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "listar_bancos_omie.log"
CHECKPOINT_FILE = LOG_DIR / "listar_bancos_omie.checkpoint.json"

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
        "call": "ListarBancos",
        "param": [
            {
                "pagina": pagina,
                "registros_por_pagina": REGISTROS_POR_PAGINA,
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
                CREATE TABLE IF NOT EXISTS raw_omie_bancos (
                    codigo VARCHAR(20) NOT NULL,
                    nome VARCHAR(200) NULL,
                    tipo VARCHAR(20) NULL,
                    pagina_api INT NULL,
                    total_de_paginas_api INT NULL,
                    registros_api INT NULL,
                    total_de_registros_api INT NULL,
                    dados_json JSON NOT NULL,
                    dados_flat_json JSON NOT NULL,
                    extraido_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    PRIMARY KEY (codigo),
                    KEY idx_raw_omie_bancos_nome (nome),
                    KEY idx_raw_omie_bancos_tipo (tipo)
                )
                CHARACTER SET utf8mb4
                COLLATE utf8mb4_unicode_ci
                """
            )
        conn.commit()


def salvar_bancos_no_banco(dados: dict[str, Any]) -> int:
    bancos = dados.get("fin_banco_cadastro", [])
    if not isinstance(bancos, list):
        raise RuntimeError("Resposta inesperada: campo 'fin_banco_cadastro' nao e uma lista.")

    registros = []
    for banco in bancos:
        if not isinstance(banco, dict):
            continue

        codigo = texto(banco.get("codigo"))
        if not codigo:
            continue

        registros.append(
            {
                "codigo": codigo,
                "nome": texto(banco.get("nome")),
                "tipo": texto(banco.get("tipo")),
                "pagina_api": inteiro(dados.get("pagina")),
                "total_de_paginas_api": inteiro(dados.get("total_de_paginas")),
                "registros_api": inteiro(dados.get("registros")),
                "total_de_registros_api": inteiro(dados.get("total_de_registros")),
                "dados_json": json.dumps(banco, ensure_ascii=False),
                "dados_flat_json": json.dumps(achatar_json(banco), ensure_ascii=False),
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
        if coluna != "codigo"
    )
    sql = f"""
        INSERT INTO raw_omie_bancos ({lista_colunas})
        VALUES ({placeholders})
        ON DUPLICATE KEY UPDATE {atualizacoes}
    """

    with conectar_mysql() as conn:
        with conn.cursor() as cursor:
            cursor.executemany(sql, registros)
        conn.commit()

    return len(registros)


def extrair_e_salvar(pagina_inicial: int | None = None) -> int:
    ultima_pagina = carregar_checkpoint()
    pagina = pagina_inicial or (ultima_pagina + 1 if ultima_pagina else 1)
    total = 0
    total_paginas: int | None = None

    if pagina > 1:
        logging.info("Retomando a extracao a partir da pagina %s.", pagina)

    while True:
        try:
            dados = chamar_api(pagina)
        except PaginaSemRegistros:
            logging.info("Pagina %s sem bancos. Fim da consulta.", pagina)
            break

        bancos = dados.get("fin_banco_cadastro", [])
        total_paginas_api = dados.get("total_de_paginas")
        if total_paginas_api is not None:
            total_paginas = int(total_paginas_api)

        if not isinstance(bancos, list):
            raise RuntimeError("Resposta inesperada: campo 'fin_banco_cadastro' nao e uma lista.")

        if not bancos:
            logging.info("Pagina %s sem bancos. Fim da consulta.", pagina)
            break

        salvos = salvar_bancos_no_banco(dados)
        total += salvos
        salvar_checkpoint(pagina, total)
        logging.info(
            "Pagina %s/%s processada: %s bancos gravados no banco.",
            pagina,
            total_paginas or "?",
            salvos,
        )

        if total_paginas is not None and pagina >= total_paginas:
            logging.info("Ultima pagina informada pela API processada.")
            break

        pagina += 1
        time.sleep(INTERVALO_ENTRE_PAGINAS)

    remover_checkpoint()
    return total


def argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extrai bancos da API Omie para o MySQL.")
    parser.add_argument(
        "--pagina-inicial",
        type=int,
        help="Pagina inicial da extracao. Sobrescreve um checkpoint existente.",
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
        total = extrair_e_salvar(args.pagina_inicial)
    except (pymysql.MySQLError, requests.RequestException, RuntimeError, OSError) as exc:
        logging.exception("Erro ao executar extracao: %s", exc)
        return 1

    logging.info(
        "Extracao finalizada: %s bancos gravados no banco %s.",
        total,
        MYSQL_DATABASE,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
