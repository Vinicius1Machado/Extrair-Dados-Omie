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

CAMPOS_ADICIONAIS = {
    "bloquear_faturamento": "CHAR(1) NULL",
    "cidade_ibge": "VARCHAR(20) NULL",
    "codigo_pais": "VARCHAR(20) NULL",
    "complemento": "VARCHAR(160) NULL",
    "contato": "VARCHAR(160) NULL",
    "enviar_anexos": "CHAR(1) NULL",
    "exterior": "CHAR(1) NULL",
    "homepage": "VARCHAR(255) NULL",
    "inscricao_estadual": "VARCHAR(30) NULL",
    "inscricao_municipal": "VARCHAR(30) NULL",
    "optante_simples_nacional": "CHAR(1) NULL",
    "pessoa_fisica": "CHAR(1) NULL",
    "banco_codigo": "VARCHAR(20) NULL",
    "banco_agencia": "VARCHAR(30) NULL",
    "banco_conta_corrente": "VARCHAR(40) NULL",
    "banco_chave_pix": "VARCHAR(255) NULL",
    "banco_documento_titular": "VARCHAR(30) NULL",
    "banco_nome_titular": "VARCHAR(200) NULL",
    "banco_transferencia_padrao": "CHAR(1) NULL",
    "dados_bancarios_json": "JSON NULL",
    "endereco_entrega_json": "JSON NULL",
    "recomendacoes_gerar_boletos": "CHAR(1) NULL",
    "recomendacoes_json": "JSON NULL",
    "omie_importado_api": "CHAR(1) NULL",
    "omie_data_inclusao": "VARCHAR(10) NULL",
    "omie_hora_inclusao": "VARCHAR(8) NULL",
    "omie_usuario_inclusao": "VARCHAR(80) NULL",
    "omie_data_alteracao": "VARCHAR(10) NULL",
    "omie_hora_alteracao": "VARCHAR(8) NULL",
    "omie_usuario_alteracao": "VARCHAR(80) NULL",
    "info_json": "JSON NULL",
}


class PaginaSemRegistros(Exception):
    pass


def somente_digitos(valor: Any) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def texto(valor: Any) -> str | None:
    if valor is None:
        return None
    valor = str(valor).strip()
    return valor or None


def extrair_tags(cliente: dict[str, Any]) -> tuple[str | None, str]:
    tags_origem = cliente.get("tags")
    if not isinstance(tags_origem, list):
        return None, "[]"

    nomes = []
    for item in tags_origem:
        nome = texto(item.get("tag")) if isinstance(item, dict) else texto(item)
        if nome and nome not in nomes:
            nomes.append(nome)

    return "; ".join(nomes) or None, json.dumps(tags_origem, ensure_ascii=False)


def objeto(valor: Any) -> dict[str, Any]:
    return valor if isinstance(valor, dict) else {}


def json_objeto(valor: Any) -> str:
    return json.dumps(objeto(valor), ensure_ascii=False)


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
                    bloquear_faturamento CHAR(1) NULL,
                    cidade_ibge VARCHAR(20) NULL,
                    codigo_pais VARCHAR(20) NULL,
                    complemento VARCHAR(160) NULL,
                    contato VARCHAR(160) NULL,
                    enviar_anexos CHAR(1) NULL,
                    exterior CHAR(1) NULL,
                    homepage VARCHAR(255) NULL,
                    inscricao_estadual VARCHAR(30) NULL,
                    inscricao_municipal VARCHAR(30) NULL,
                    optante_simples_nacional CHAR(1) NULL,
                    pessoa_fisica CHAR(1) NULL,
                    banco_codigo VARCHAR(20) NULL,
                    banco_agencia VARCHAR(30) NULL,
                    banco_conta_corrente VARCHAR(40) NULL,
                    banco_chave_pix VARCHAR(255) NULL,
                    banco_documento_titular VARCHAR(30) NULL,
                    banco_nome_titular VARCHAR(200) NULL,
                    banco_transferencia_padrao CHAR(1) NULL,
                    dados_bancarios_json JSON NULL,
                    endereco_entrega_json JSON NULL,
                    recomendacoes_gerar_boletos CHAR(1) NULL,
                    recomendacoes_json JSON NULL,
                    omie_importado_api CHAR(1) NULL,
                    omie_data_inclusao VARCHAR(10) NULL,
                    omie_hora_inclusao VARCHAR(8) NULL,
                    omie_usuario_inclusao VARCHAR(80) NULL,
                    omie_data_alteracao VARCHAR(10) NULL,
                    omie_hora_alteracao VARCHAR(8) NULL,
                    omie_usuario_alteracao VARCHAR(80) NULL,
                    info_json JSON NULL,
                    tags TEXT NULL,
                    tags_json JSON NOT NULL,
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
            cursor.execute(
                """
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = %s
                  AND TABLE_NAME = 'raw_omie_clientes'
                """,
                (MYSQL_DATABASE,),
            )
            colunas_existentes = {linha["COLUMN_NAME"] for linha in cursor.fetchall()}
            campos_migracao = {
                **CAMPOS_ADICIONAIS,
                "tags": "TEXT NULL",
                "tags_json": "JSON NULL",
            }

            for nome, definicao in campos_migracao.items():
                if nome not in colunas_existentes:
                    cursor.execute(
                        f"ALTER TABLE raw_omie_clientes "
                        f"ADD COLUMN `{nome}` {definicao}"
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
            bloquear_faturamento,
            cidade_ibge,
            codigo_pais,
            complemento,
            contato,
            enviar_anexos,
            exterior,
            homepage,
            inscricao_estadual,
            inscricao_municipal,
            optante_simples_nacional,
            pessoa_fisica,
            banco_codigo,
            banco_agencia,
            banco_conta_corrente,
            banco_chave_pix,
            banco_documento_titular,
            banco_nome_titular,
            banco_transferencia_padrao,
            dados_bancarios_json,
            endereco_entrega_json,
            recomendacoes_gerar_boletos,
            recomendacoes_json,
            omie_importado_api,
            omie_data_inclusao,
            omie_hora_inclusao,
            omie_usuario_inclusao,
            omie_data_alteracao,
            omie_hora_alteracao,
            omie_usuario_alteracao,
            info_json,
            tags,
            tags_json,
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
            %(bloquear_faturamento)s,
            %(cidade_ibge)s,
            %(codigo_pais)s,
            %(complemento)s,
            %(contato)s,
            %(enviar_anexos)s,
            %(exterior)s,
            %(homepage)s,
            %(inscricao_estadual)s,
            %(inscricao_municipal)s,
            %(optante_simples_nacional)s,
            %(pessoa_fisica)s,
            %(banco_codigo)s,
            %(banco_agencia)s,
            %(banco_conta_corrente)s,
            %(banco_chave_pix)s,
            %(banco_documento_titular)s,
            %(banco_nome_titular)s,
            %(banco_transferencia_padrao)s,
            %(dados_bancarios_json)s,
            %(endereco_entrega_json)s,
            %(recomendacoes_gerar_boletos)s,
            %(recomendacoes_json)s,
            %(omie_importado_api)s,
            %(omie_data_inclusao)s,
            %(omie_hora_inclusao)s,
            %(omie_usuario_inclusao)s,
            %(omie_data_alteracao)s,
            %(omie_hora_alteracao)s,
            %(omie_usuario_alteracao)s,
            %(info_json)s,
            %(tags)s,
            %(tags_json)s,
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
            bloquear_faturamento = VALUES(bloquear_faturamento),
            cidade_ibge = VALUES(cidade_ibge),
            codigo_pais = VALUES(codigo_pais),
            complemento = VALUES(complemento),
            contato = VALUES(contato),
            enviar_anexos = VALUES(enviar_anexos),
            exterior = VALUES(exterior),
            homepage = VALUES(homepage),
            inscricao_estadual = VALUES(inscricao_estadual),
            inscricao_municipal = VALUES(inscricao_municipal),
            optante_simples_nacional = VALUES(optante_simples_nacional),
            pessoa_fisica = VALUES(pessoa_fisica),
            banco_codigo = VALUES(banco_codigo),
            banco_agencia = VALUES(banco_agencia),
            banco_conta_corrente = VALUES(banco_conta_corrente),
            banco_chave_pix = VALUES(banco_chave_pix),
            banco_documento_titular = VALUES(banco_documento_titular),
            banco_nome_titular = VALUES(banco_nome_titular),
            banco_transferencia_padrao = VALUES(banco_transferencia_padrao),
            dados_bancarios_json = VALUES(dados_bancarios_json),
            endereco_entrega_json = VALUES(endereco_entrega_json),
            recomendacoes_gerar_boletos = VALUES(recomendacoes_gerar_boletos),
            recomendacoes_json = VALUES(recomendacoes_json),
            omie_importado_api = VALUES(omie_importado_api),
            omie_data_inclusao = VALUES(omie_data_inclusao),
            omie_hora_inclusao = VALUES(omie_hora_inclusao),
            omie_usuario_inclusao = VALUES(omie_usuario_inclusao),
            omie_data_alteracao = VALUES(omie_data_alteracao),
            omie_hora_alteracao = VALUES(omie_hora_alteracao),
            omie_usuario_alteracao = VALUES(omie_usuario_alteracao),
            info_json = VALUES(info_json),
            tags = VALUES(tags),
            tags_json = VALUES(tags_json),
            dados_json = VALUES(dados_json),
            dados_flat_json = VALUES(dados_flat_json)
    """

    registros_raw = []

    for cliente in clientes:
        flat = achatar_json(cliente)
        codigo_omie = cliente.get("codigo_cliente_omie")
        if not codigo_omie:
            continue

        tags, tags_json = extrair_tags(cliente)
        dados_bancarios = objeto(cliente.get("dadosBancarios"))
        recomendacoes = objeto(cliente.get("recomendacoes"))
        info = objeto(cliente.get("info"))
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
                "bloquear_faturamento": texto(cliente.get("bloquear_faturamento")),
                "cidade_ibge": texto(cliente.get("cidade_ibge")),
                "codigo_pais": texto(cliente.get("codigo_pais")),
                "complemento": texto(cliente.get("complemento")),
                "contato": texto(cliente.get("contato")),
                "enviar_anexos": texto(cliente.get("enviar_anexos")),
                "exterior": texto(cliente.get("exterior")),
                "homepage": texto(cliente.get("homepage")),
                "inscricao_estadual": texto(cliente.get("inscricao_estadual")),
                "inscricao_municipal": texto(cliente.get("inscricao_municipal")),
                "optante_simples_nacional": texto(cliente.get("optante_simples_nacional")),
                "pessoa_fisica": texto(cliente.get("pessoa_fisica")),
                "banco_codigo": texto(dados_bancarios.get("codigo_banco")),
                "banco_agencia": texto(dados_bancarios.get("agencia")),
                "banco_conta_corrente": texto(dados_bancarios.get("conta_corrente")),
                "banco_chave_pix": texto(dados_bancarios.get("cChavePix")),
                "banco_documento_titular": somente_digitos(dados_bancarios.get("doc_titular")) or None,
                "banco_nome_titular": texto(dados_bancarios.get("nome_titular")),
                "banco_transferencia_padrao": texto(dados_bancarios.get("transf_padrao")),
                "dados_bancarios_json": json_objeto(cliente.get("dadosBancarios")),
                "endereco_entrega_json": json_objeto(cliente.get("enderecoEntrega")),
                "recomendacoes_gerar_boletos": texto(recomendacoes.get("gerar_boletos")),
                "recomendacoes_json": json_objeto(cliente.get("recomendacoes")),
                "omie_importado_api": texto(info.get("cImpAPI")),
                "omie_data_inclusao": texto(info.get("dInc")),
                "omie_hora_inclusao": texto(info.get("hInc")),
                "omie_usuario_inclusao": texto(info.get("uInc")),
                "omie_data_alteracao": texto(info.get("dAlt")),
                "omie_hora_alteracao": texto(info.get("hAlt")),
                "omie_usuario_alteracao": texto(info.get("uAlt")),
                "info_json": json_objeto(cliente.get("info")),
                "tags": tags,
                "tags_json": tags_json,
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
