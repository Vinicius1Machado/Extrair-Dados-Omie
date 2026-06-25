import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import pymysql
import requests
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
API_URL = "https://app.omie.com.br/api/v1/geral/clientes/"
REGISTROS_POR_PAGINA = 50

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


def somente_digitos(valor: Any) -> str:
    return re.sub(r"\D", "", str(valor or ""))


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


def chamar_api(pagina: int) -> dict[str, Any]:
    payload = {
        "call": "ListarClientes",
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

    response = requests.post(API_URL, json=payload, timeout=60)

    if response.status_code == 500:
        try:
            erro = response.json()
        except ValueError:
            erro = {}

        if erro.get("faultcode") == "SOAP-ENV:Client-5113":
            raise PaginaSemRegistros

    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise RuntimeError(f"Erro HTTP {response.status_code} ao chamar a API: {response.text}") from exc

    return response.json()


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
                CREATE TABLE IF NOT EXISTS raw_omie_clientes (
                    codigo_cliente_omie BIGINT NOT NULL,
                    codigo_cliente_integracao VARCHAR(120) NULL,
                    cnpj_cpf VARCHAR(30) NULL,
                    razao_social VARCHAR(200) NULL,
                    nome_fantasia VARCHAR(200) NULL,
                    email VARCHAR(241) NULL,
                    telefone1_ddd VARCHAR(10) NULL,
                    telefone1_numero VARCHAR(30) NULL,
                    cidade VARCHAR(80) NULL,
                    estado VARCHAR(80) NULL,
                    cep VARCHAR(20) NULL,
                    endereco VARCHAR(160) NULL,
                    endereco_numero VARCHAR(20) NULL,
                    bairro VARCHAR(80) NULL,
                    inativo CHAR(1) NULL,
                    dados_json JSON NOT NULL,
                    dados_flat_json JSON NOT NULL,
                    extraido_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    PRIMARY KEY (codigo_cliente_omie),
                    KEY idx_raw_omie_clientes_cnpj_cpf (cnpj_cpf),
                    KEY idx_raw_omie_clientes_integracao (codigo_cliente_integracao)
                )
                CHARACTER SET utf8mb4
                COLLATE utf8mb4_unicode_ci
                """
            )
        conn.commit()


def salvar_clientes_no_banco(clientes: list[dict[str, Any]]) -> int:
    if not clientes:
        return 0

    sql_raw = """
        INSERT INTO raw_omie_clientes (
            codigo_cliente_omie,
            codigo_cliente_integracao,
            cnpj_cpf,
            razao_social,
            nome_fantasia,
            email,
            telefone1_ddd,
            telefone1_numero,
            cidade,
            estado,
            cep,
            endereco,
            endereco_numero,
            bairro,
            inativo,
            dados_json,
            dados_flat_json
        )
        VALUES (
            %(codigo_cliente_omie)s,
            %(codigo_cliente_integracao)s,
            %(cnpj_cpf)s,
            %(razao_social)s,
            %(nome_fantasia)s,
            %(email)s,
            %(telefone1_ddd)s,
            %(telefone1_numero)s,
            %(cidade)s,
            %(estado)s,
            %(cep)s,
            %(endereco)s,
            %(endereco_numero)s,
            %(bairro)s,
            %(inativo)s,
            %(dados_json)s,
            %(dados_flat_json)s
        )
        ON DUPLICATE KEY UPDATE
            codigo_cliente_integracao = VALUES(codigo_cliente_integracao),
            cnpj_cpf = VALUES(cnpj_cpf),
            razao_social = VALUES(razao_social),
            nome_fantasia = VALUES(nome_fantasia),
            email = VALUES(email),
            telefone1_ddd = VALUES(telefone1_ddd),
            telefone1_numero = VALUES(telefone1_numero),
            cidade = VALUES(cidade),
            estado = VALUES(estado),
            cep = VALUES(cep),
            endereco = VALUES(endereco),
            endereco_numero = VALUES(endereco_numero),
            bairro = VALUES(bairro),
            inativo = VALUES(inativo),
            dados_json = VALUES(dados_json),
            dados_flat_json = VALUES(dados_flat_json)
    """

    registros_raw = []

    for cliente in clientes:
        flat = achatar_json(cliente)
        codigo_omie = cliente.get("codigo_cliente_omie")
        if not codigo_omie:
            continue

        registros_raw.append(
            {
                "codigo_cliente_omie": int(codigo_omie),
                "codigo_cliente_integracao": texto(cliente.get("codigo_cliente_integracao")),
                "cnpj_cpf": somente_digitos(cliente.get("cnpj_cpf")),
                "razao_social": texto(cliente.get("razao_social")),
                "nome_fantasia": texto(cliente.get("nome_fantasia")),
                "email": texto(cliente.get("email")),
                "telefone1_ddd": texto(cliente.get("telefone1_ddd")),
                "telefone1_numero": texto(cliente.get("telefone1_numero")),
                "cidade": texto(cliente.get("cidade")),
                "estado": texto(cliente.get("estado")),
                "cep": somente_digitos(cliente.get("cep")),
                "endereco": texto(cliente.get("endereco")),
                "endereco_numero": texto(cliente.get("endereco_numero")),
                "bairro": texto(cliente.get("bairro")),
                "inativo": texto(cliente.get("inativo")),
                "dados_json": json.dumps(cliente, ensure_ascii=False),
                "dados_flat_json": json.dumps(flat, ensure_ascii=False),
            }
        )

    with conectar_mysql() as conn:
        with conn.cursor() as cursor:
            cursor.executemany(sql_raw, registros_raw)
        conn.commit()

    return len(registros_raw)


def extrair_e_salvar() -> int:
    pagina = 1
    total = 0

    while True:
        try:
            dados = chamar_api(pagina)
        except PaginaSemRegistros:
            print(f"Pagina {pagina} sem clientes. Fim da consulta.")
            break

        clientes_pagina = dados.get("clientes_cadastro", [])

        if not isinstance(clientes_pagina, list):
            raise RuntimeError("Resposta inesperada: campo 'clientes_cadastro' nao e uma lista.")

        if not clientes_pagina:
            print(f"Pagina {pagina} sem clientes. Fim da consulta.")
            break

        salvos = salvar_clientes_no_banco(clientes_pagina)
        total += salvos
        print(f"Pagina {pagina} processada: {salvos} clientes gravados no banco")
        pagina += 1

    return total


def main() -> int:
    if not APP_KEY or not APP_SECRET:
        print("Defina OMIE_APP_KEY e OMIE_APP_SECRET no arquivo .env.", file=sys.stderr)
        return 1

    try:
        preparar_banco()
        total = extrair_e_salvar()
    except (pymysql.MySQLError, requests.RequestException, RuntimeError) as exc:
        print(f"Erro ao executar extracao: {exc}", file=sys.stderr)
        return 1

    print(f"Extracao finalizada: {total} clientes gravados no banco {MYSQL_DATABASE}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
