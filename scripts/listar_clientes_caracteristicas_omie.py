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
API_URL = "https://app.omie.com.br/api/v1/geral/clientescaract/"
MAX_TENTATIVAS = 5
INTERVALO_ENTRE_CLIENTES = 0.3
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "listar_clientes_caracteristicas_omie.log"
CHECKPOINT_FILE = LOG_DIR / "listar_clientes_caracteristicas_omie.checkpoint.json"

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


def carregar_checkpoint() -> int:
    if not CHECKPOINT_FILE.exists():
        return 0

    try:
        dados = json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
        return max(int(dados.get("clientes_processados", 0)), 0)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        logging.warning("Checkpoint invalido. A extracao sera iniciada do comeco.")
        return 0


def salvar_checkpoint(clientes_processados: int, ultimo_codigo_cliente_omie: int) -> None:
    CHECKPOINT_FILE.write_text(
        json.dumps(
            {
                "clientes_processados": clientes_processados,
                "ultimo_codigo_cliente_omie": ultimo_codigo_cliente_omie,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def remover_checkpoint() -> None:
    CHECKPOINT_FILE.unlink(missing_ok=True)


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
                CREATE TABLE IF NOT EXISTS raw_omie_clientes_caracteristicas (
                    id BIGINT NOT NULL AUTO_INCREMENT,
                    codigo_cliente_omie BIGINT NOT NULL,
                    codigo_cliente_integracao VARCHAR(60) NULL,
                    numero_sequencia INT NOT NULL,
                    campo VARCHAR(30) NULL,
                    conteudo VARCHAR(60) NULL,
                    caracteristica_json JSON NOT NULL,
                    resposta_json JSON NOT NULL,
                    dados_flat_json JSON NOT NULL,
                    extraido_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    PRIMARY KEY (id),
                    KEY idx_raw_omie_cli_caract_cliente (codigo_cliente_omie),
                    KEY idx_raw_omie_cli_caract_campo (campo),
                    CONSTRAINT fk_raw_omie_cli_caract_cliente
                        FOREIGN KEY (codigo_cliente_omie)
                        REFERENCES raw_omie_clientes (codigo_cliente_omie)
                        ON UPDATE CASCADE
                        ON DELETE CASCADE
                )
                CHARACTER SET utf8mb4
                COLLATE utf8mb4_unicode_ci
                """
            )
        conn.commit()


def buscar_clientes(codigo_cliente_omie: int | None = None) -> list[dict[str, Any]]:
    sql = """
        SELECT codigo_cliente_omie, codigo_cliente_integracao
        FROM raw_omie_clientes
    """
    parametros: tuple[Any, ...] = ()

    if codigo_cliente_omie is not None:
        sql += " WHERE codigo_cliente_omie = %s"
        parametros = (codigo_cliente_omie,)

    sql += " ORDER BY codigo_cliente_omie"

    with conectar_mysql() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, parametros)
            return list(cursor.fetchall())


def chamar_api(cliente: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "call": "ConsultarCaractCliente",
        "param": [
            {
                "codigo_cliente_omie": int(cliente["codigo_cliente_omie"]),
                "codigo_cliente_integracao": cliente.get("codigo_cliente_integracao") or "",
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
                    "Falha ao consultar caracteristicas do cliente "
                    f"{cliente['codigo_cliente_omie']} apos {MAX_TENTATIVAS} tentativas: {exc}"
                ) from exc

            espera = min(2 ** (tentativa - 1), 30)
            logging.warning(
                "Cliente %s falhou na tentativa %s/%s: %s. Nova tentativa em %ss.",
                cliente["codigo_cliente_omie"],
                tentativa,
                MAX_TENTATIVAS,
                exc,
                espera,
            )
            time.sleep(espera)

    raise RuntimeError(f"Nao foi possivel consultar o cliente {cliente['codigo_cliente_omie']}.")


def salvar_caracteristicas_no_banco(dados: dict[str, Any]) -> int:
    codigo_cliente_omie = int(dados["codigo_cliente_omie"])
    codigo_cliente_integracao = texto(dados.get("codigo_cliente_integracao"))
    caracteristicas = dados.get("caracteristicas", [])

    if not isinstance(caracteristicas, list):
        raise RuntimeError(
            "Resposta inesperada: campo 'caracteristicas' nao e uma lista "
            f"para o cliente {codigo_cliente_omie}."
        )

    resposta_json = json.dumps(dados, ensure_ascii=False)
    dados_flat_json = json.dumps(achatar_json(dados), ensure_ascii=False)
    registros = []

    for indice, caracteristica in enumerate(caracteristicas, start=1):
        if not isinstance(caracteristica, dict):
            caracteristica = {"valor": caracteristica}

        registros.append(
            {
                "codigo_cliente_omie": codigo_cliente_omie,
                "codigo_cliente_integracao": codigo_cliente_integracao,
                "numero_sequencia": indice,
                "campo": texto(caracteristica.get("campo")),
                "conteudo": texto(caracteristica.get("conteudo")),
                "caracteristica_json": json.dumps(caracteristica, ensure_ascii=False),
                "resposta_json": resposta_json,
                "dados_flat_json": dados_flat_json,
            }
        )

    sql_insert = """
        INSERT INTO raw_omie_clientes_caracteristicas (
            codigo_cliente_omie,
            codigo_cliente_integracao,
            numero_sequencia,
            campo,
            conteudo,
            caracteristica_json,
            resposta_json,
            dados_flat_json
        )
        VALUES (
            %(codigo_cliente_omie)s,
            %(codigo_cliente_integracao)s,
            %(numero_sequencia)s,
            %(campo)s,
            %(conteudo)s,
            %(caracteristica_json)s,
            %(resposta_json)s,
            %(dados_flat_json)s
        )
    """

    with conectar_mysql() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM raw_omie_clientes_caracteristicas WHERE codigo_cliente_omie = %s",
                (codigo_cliente_omie,),
            )
            if registros:
                cursor.executemany(sql_insert, registros)
        conn.commit()

    return len(registros)


def extrair_e_salvar(
    codigo_cliente_omie: int | None = None,
    limite: int | None = None,
    ignorar_checkpoint: bool = False,
) -> tuple[int, int]:
    clientes = buscar_clientes(codigo_cliente_omie)
    if not clientes:
        logging.warning("Nenhum cliente encontrado em raw_omie_clientes para consultar.")
        return 0, 0

    usar_checkpoint = codigo_cliente_omie is None and limite is None
    inicio = 0 if ignorar_checkpoint or not usar_checkpoint else carregar_checkpoint()
    if limite is not None:
        clientes = clientes[inicio : inicio + limite]
    else:
        clientes = clientes[inicio:]

    if inicio:
        logging.info("Retomando a extracao a partir do cliente de indice %s.", inicio + 1)

    total_clientes = 0
    total_caracteristicas = 0

    for deslocamento, cliente in enumerate(clientes, start=inicio + 1):
        dados = chamar_api(cliente)
        salvos = salvar_caracteristicas_no_banco(dados)
        total_clientes += 1
        total_caracteristicas += salvos
        if usar_checkpoint:
            salvar_checkpoint(deslocamento, int(cliente["codigo_cliente_omie"]))
        logging.info(
            "Cliente %s processado: %s caracteristicas gravadas.",
            cliente["codigo_cliente_omie"],
            salvos,
        )
        time.sleep(INTERVALO_ENTRE_CLIENTES)

    if usar_checkpoint:
        remover_checkpoint()

    return total_clientes, total_caracteristicas


def argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extrai caracteristicas de clientes/fornecedores da API Omie para o MySQL."
    )
    parser.add_argument(
        "--codigo-cliente-omie",
        type=int,
        help="Consulta somente um cliente/fornecedor especifico.",
    )
    parser.add_argument(
        "--limite",
        type=int,
        help="Quantidade maxima de clientes a consultar nesta execucao.",
    )
    parser.add_argument(
        "--ignorar-checkpoint",
        action="store_true",
        help="Inicia do primeiro cliente, ignorando checkpoint existente.",
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
        total_clientes, total_caracteristicas = extrair_e_salvar(
            codigo_cliente_omie=args.codigo_cliente_omie,
            limite=args.limite,
            ignorar_checkpoint=args.ignorar_checkpoint,
        )
    except (pymysql.MySQLError, requests.RequestException, RuntimeError, OSError) as exc:
        logging.exception("Erro ao executar extracao: %s", exc)
        return 1

    logging.info(
        "Extracao finalizada: %s clientes consultados e %s caracteristicas gravadas no banco %s.",
        total_clientes,
        total_caracteristicas,
        MYSQL_DATABASE,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
