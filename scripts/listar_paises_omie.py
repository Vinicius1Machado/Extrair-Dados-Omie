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
API_URL = "https://app.omie.com.br/api/v1/geral/paises/"
MAX_TENTATIVAS = 5
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "listar_paises_omie.log"

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


def texto(valor: Any) -> str | None:
    if valor is None:
        return None
    valor = str(valor).strip()
    return valor or None


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


def chamar_api() -> dict[str, Any]:
    payload = {
        "call": "ListarPaises",
        "param": [
            {
                "filtrar_por_codigo": "",
                "filtrar_por_descricao": "",
            }
        ],
        "app_key": APP_KEY,
        "app_secret": APP_SECRET,
    }

    for tentativa in range(1, MAX_TENTATIVAS + 1):
        try:
            response = requests.post(API_URL, json=payload, timeout=(15, 90))
            response.raise_for_status()
            dados = response.json()
            if not isinstance(dados, dict):
                raise RuntimeError("Resposta inesperada: JSON raiz nao e um objeto.")
            return dados
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            if tentativa == MAX_TENTATIVAS:
                raise RuntimeError(
                    f"Falha ao consultar paises apos {MAX_TENTATIVAS} tentativas: {exc}"
                ) from exc

            espera = min(2 ** (tentativa - 1), 30)
            logging.warning(
                "Consulta de paises falhou na tentativa %s/%s: %s. Nova tentativa em %ss.",
                tentativa,
                MAX_TENTATIVAS,
                exc,
                espera,
            )
            time.sleep(espera)

    raise RuntimeError("Nao foi possivel consultar paises.")


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
                CREATE TABLE IF NOT EXISTS raw_omie_paises (
                    codigo_pais VARCHAR(20) NOT NULL,
                    codigo_iso CHAR(2) NULL,
                    descricao VARCHAR(120) NULL,
                    dados_json JSON NOT NULL,
                    dados_flat_json JSON NOT NULL,
                    extraido_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    PRIMARY KEY (codigo_pais),
                    KEY idx_raw_omie_paises_codigo_iso (codigo_iso),
                    KEY idx_raw_omie_paises_descricao (descricao)
                )
                CHARACTER SET utf8mb4
                COLLATE utf8mb4_unicode_ci
                """
            )
        conn.commit()


def salvar_paises_no_banco(dados: dict[str, Any]) -> int:
    paises = dados.get("lista_paises", [])
    if not isinstance(paises, list):
        raise RuntimeError("Resposta inesperada: campo 'lista_paises' nao e uma lista.")

    registros = []
    for pais in paises:
        if not isinstance(pais, dict):
            continue

        codigo_pais = texto(pais.get("cCodigo"))
        if not codigo_pais:
            continue

        registros.append(
            {
                "codigo_pais": codigo_pais,
                "codigo_iso": texto(pais.get("cCodigoISO")),
                "descricao": texto(pais.get("cDescricao")),
                "dados_json": json.dumps(pais, ensure_ascii=False),
                "dados_flat_json": json.dumps(achatar_json(pais), ensure_ascii=False),
            }
        )

    if not registros:
        return 0

    sql = """
        INSERT INTO raw_omie_paises (
            codigo_pais,
            codigo_iso,
            descricao,
            dados_json,
            dados_flat_json
        )
        VALUES (
            %(codigo_pais)s,
            %(codigo_iso)s,
            %(descricao)s,
            %(dados_json)s,
            %(dados_flat_json)s
        )
        ON DUPLICATE KEY UPDATE
            codigo_iso = VALUES(codigo_iso),
            descricao = VALUES(descricao),
            dados_json = VALUES(dados_json),
            dados_flat_json = VALUES(dados_flat_json)
    """

    with conectar_mysql() as conn:
        with conn.cursor() as cursor:
            cursor.executemany(sql, registros)
        conn.commit()

    return len(registros)


def indice_existe(cursor: pymysql.cursors.DictCursor, tabela: str, indice: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = %s
          AND INDEX_NAME = %s
        LIMIT 1
        """,
        (MYSQL_DATABASE, tabela, indice),
    )
    return cursor.fetchone() is not None


def constraint_existe(cursor: pymysql.cursors.DictCursor, constraint: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = 'raw_omie_clientes'
          AND CONSTRAINT_NAME = %s
        LIMIT 1
        """,
        (MYSQL_DATABASE, constraint),
    )
    return cursor.fetchone() is not None


def criar_relacionamento_clientes_paises() -> bool:
    with conectar_mysql() as conn:
        with conn.cursor() as cursor:
            if not indice_existe(cursor, "raw_omie_clientes", "idx_raw_omie_clientes_codigo_pais"):
                cursor.execute(
                    """
                    ALTER TABLE raw_omie_clientes
                    ADD INDEX idx_raw_omie_clientes_codigo_pais (codigo_pais)
                    """
                )

            cursor.execute(
                """
                SELECT c.codigo_pais, COUNT(*) AS total
                FROM raw_omie_clientes c
                LEFT JOIN raw_omie_paises p
                  ON p.codigo_pais = c.codigo_pais
                WHERE c.codigo_pais IS NOT NULL
                  AND c.codigo_pais <> ''
                  AND p.codigo_pais IS NULL
                GROUP BY c.codigo_pais
                ORDER BY total DESC
                LIMIT 10
                """
            )
            codigos_sem_pais = cursor.fetchall()
            if codigos_sem_pais:
                logging.warning(
                    "Relacionamento FK nao criado: existem codigos de pais em clientes "
                    "sem correspondente em raw_omie_paises: %s",
                    codigos_sem_pais,
                )
                conn.commit()
                return False

            if not constraint_existe(cursor, "fk_raw_omie_clientes_pais"):
                cursor.execute(
                    """
                    ALTER TABLE raw_omie_clientes
                    ADD CONSTRAINT fk_raw_omie_clientes_pais
                    FOREIGN KEY (codigo_pais)
                    REFERENCES raw_omie_paises (codigo_pais)
                    ON UPDATE CASCADE
                    ON DELETE RESTRICT
                    """
                )
        conn.commit()

    return True


def extrair_e_salvar() -> tuple[int, bool]:
    dados = chamar_api()
    total = salvar_paises_no_banco(dados)
    relacionamento_criado = criar_relacionamento_clientes_paises()
    return total, relacionamento_criado


def argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extrai paises da API Omie para o MySQL.")
    return parser.parse_args()


def main() -> int:
    configurar_logging()
    argumentos()

    if not APP_KEY or not APP_SECRET:
        logging.error("Defina OMIE_APP_KEY e OMIE_APP_SECRET no arquivo .env.")
        return 1

    try:
        preparar_banco()
        total, relacionamento_criado = extrair_e_salvar()
    except (pymysql.MySQLError, requests.RequestException, RuntimeError, OSError) as exc:
        logging.exception("Erro ao executar extracao: %s", exc)
        return 1

    logging.info("Extracao finalizada: %s paises gravados no banco %s.", total, MYSQL_DATABASE)
    if relacionamento_criado:
        logging.info("Relacionamento raw_omie_clientes.codigo_pais -> raw_omie_paises.codigo_pais ativo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
