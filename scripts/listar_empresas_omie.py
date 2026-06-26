import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import pymysql
import requests
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
API_URL = "https://app.omie.com.br/api/v1/geral/empresas/"
REGISTROS_POR_PAGINA = 100
MAX_TENTATIVAS = 5
INTERVALO_ENTRE_PAGINAS = 0.3
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "listar_empresas_omie.log"
CHECKPOINT_FILE = LOG_DIR / "listar_empresas_omie.checkpoint.json"

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


def somente_digitos(valor: Any) -> str:
    return re.sub(r"\D", "", str(valor or ""))


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
        "call": "ListarEmpresas",
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
                CREATE TABLE IF NOT EXISTS raw_omie_empresas (
                    codigo_empresa BIGINT NOT NULL,
                    cnpj VARCHAR(30) NULL,
                    razao_social VARCHAR(200) NULL,
                    nome_fantasia VARCHAR(200) NULL,
                    email VARCHAR(241) NULL,
                    telefone1_ddd VARCHAR(10) NULL,
                    telefone1_numero VARCHAR(30) NULL,
                    telefone2_ddd VARCHAR(10) NULL,
                    telefone2_numero VARCHAR(30) NULL,
                    fax_ddd VARCHAR(10) NULL,
                    fax_numero VARCHAR(30) NULL,
                    website VARCHAR(255) NULL,
                    cidade VARCHAR(100) NULL,
                    estado VARCHAR(80) NULL,
                    cep VARCHAR(20) NULL,
                    endereco VARCHAR(160) NULL,
                    endereco_numero VARCHAR(20) NULL,
                    bairro VARCHAR(80) NULL,
                    complemento VARCHAR(160) NULL,
                    logradouro VARCHAR(160) NULL,
                    codigo_pais VARCHAR(20) NULL,
                    cnae VARCHAR(20) NULL,
                    cnae_municipal VARCHAR(30) NULL,
                    inscricao_estadual VARCHAR(30) NULL,
                    inscricao_municipal VARCHAR(30) NULL,
                    inscricao_suframa VARCHAR(30) NULL,
                    regime_tributario VARCHAR(20) NULL,
                    optante_simples_nacional CHAR(1) NULL,
                    gera_nfse CHAR(1) NULL,
                    inativa CHAR(1) NULL,
                    pdv_sincr_analitica CHAR(1) NULL,
                    csc_homologacao VARCHAR(255) NULL,
                    csc_id_homologacao VARCHAR(80) NULL,
                    csc_producao VARCHAR(255) NULL,
                    csc_id_producao VARCHAR(80) NULL,
                    ecd_codigo_cadastral VARCHAR(80) NULL,
                    ecd_codigo_instituicao_responsavel VARCHAR(80) NULL,
                    efd_atividade_industrial VARCHAR(80) NULL,
                    efd_perfil_arquivo_fiscal VARCHAR(80) NULL,
                    sped_codigo_criterio_escrituracao VARCHAR(80) NULL,
                    sped_codigo_incidencia_tributaria VARCHAR(80) NULL,
                    sped_codigo_indicador_apropriacao_credito VARCHAR(80) NULL,
                    sped_codigo_tipo_atividade VARCHAR(80) NULL,
                    sped_codigo_tipo_contribuicao VARCHAR(80) NULL,
                    sped_cpf_contador VARCHAR(30) NULL,
                    sped_crc_contador VARCHAR(80) NULL,
                    sped_email_contador VARCHAR(241) NULL,
                    sped_junta_comercial VARCHAR(80) NULL,
                    sped_matriz VARCHAR(80) NULL,
                    sped_natureza_pessoa_juridica VARCHAR(80) NULL,
                    sped_nome_contador VARCHAR(200) NULL,
                    sped_registro_junta_comercial VARCHAR(80) NULL,
                    sped_usa_contabilidade_terceirizada VARCHAR(80) NULL,
                    pagina_api INT NULL,
                    total_de_paginas_api INT NULL,
                    registros_api INT NULL,
                    total_de_registros_api INT NULL,
                    produto_servico_resumido_json JSON NOT NULL,
                    dados_json JSON NOT NULL,
                    dados_flat_json JSON NOT NULL,
                    extraido_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    PRIMARY KEY (codigo_empresa),
                    KEY idx_raw_omie_empresas_cnpj (cnpj),
                    KEY idx_raw_omie_empresas_razao_social (razao_social)
                )
                CHARACTER SET utf8mb4
                COLLATE utf8mb4_unicode_ci
                """
            )
        conn.commit()


def salvar_empresas_no_banco(dados: dict[str, Any]) -> int:
    empresas = dados.get("empresas_cadastro", [])
    if not isinstance(empresas, list):
        raise RuntimeError("Resposta inesperada: campo 'empresas_cadastro' nao e uma lista.")

    produto_servico_resumido_json = json.dumps(
        dados.get("produto_servico_resumido", []),
        ensure_ascii=False,
    )

    registros = []
    for empresa in empresas:
        if not isinstance(empresa, dict):
            continue

        codigo_empresa = inteiro(empresa.get("codigo_empresa"))
        if not codigo_empresa:
            continue

        registros.append(
            {
                "codigo_empresa": codigo_empresa,
                "cnpj": somente_digitos(empresa.get("cnpj")),
                "razao_social": texto(empresa.get("razao_social")),
                "nome_fantasia": texto(empresa.get("nome_fantasia")),
                "email": texto(empresa.get("email")),
                "telefone1_ddd": texto(empresa.get("telefone1_ddd")),
                "telefone1_numero": texto(empresa.get("telefone1_numero")),
                "telefone2_ddd": texto(empresa.get("telefone2_ddd")),
                "telefone2_numero": texto(empresa.get("telefone2_numero")),
                "fax_ddd": texto(empresa.get("fax_ddd")),
                "fax_numero": texto(empresa.get("fax_numero")),
                "website": texto(empresa.get("website")),
                "cidade": texto(empresa.get("cidade")),
                "estado": texto(empresa.get("estado")),
                "cep": somente_digitos(empresa.get("cep")),
                "endereco": texto(empresa.get("endereco")),
                "endereco_numero": texto(empresa.get("endereco_numero")),
                "bairro": texto(empresa.get("bairro")),
                "complemento": texto(empresa.get("complemento")),
                "logradouro": texto(empresa.get("logradouro")),
                "codigo_pais": texto(empresa.get("codigo_pais")),
                "cnae": texto(empresa.get("cnae")),
                "cnae_municipal": texto(empresa.get("cnae_municipal")),
                "inscricao_estadual": texto(empresa.get("inscricao_estadual")),
                "inscricao_municipal": texto(empresa.get("inscricao_municipal")),
                "inscricao_suframa": texto(empresa.get("inscricao_suframa")),
                "regime_tributario": texto(empresa.get("regime_tributario")),
                "optante_simples_nacional": texto(empresa.get("optante_simples_nacional")),
                "gera_nfse": texto(empresa.get("gera_nfse")),
                "inativa": texto(empresa.get("inativa")),
                "pdv_sincr_analitica": texto(empresa.get("pdv_sincr_analitica")),
                "csc_homologacao": texto(empresa.get("csc_homologacao")),
                "csc_id_homologacao": texto(empresa.get("csc_id_homologacao")),
                "csc_producao": texto(empresa.get("csc_producao")),
                "csc_id_producao": texto(empresa.get("csc_id_producao")),
                "ecd_codigo_cadastral": texto(empresa.get("ecd_codigo_cadastral")),
                "ecd_codigo_instituicao_responsavel": texto(
                    empresa.get("ecd_codigo_instituicao_responsavel")
                ),
                "efd_atividade_industrial": texto(empresa.get("efd_atividade_industrial")),
                "efd_perfil_arquivo_fiscal": texto(empresa.get("efd_perfil_arquivo_fiscal")),
                "sped_codigo_criterio_escrituracao": texto(
                    empresa.get("sped_codigo_criterio_escrituracao")
                ),
                "sped_codigo_incidencia_tributaria": texto(
                    empresa.get("sped_codigo_incidencia_tributaria")
                ),
                "sped_codigo_indicador_apropriacao_credito": texto(
                    empresa.get("sped_codigo_indicador_apropriacao_credito")
                ),
                "sped_codigo_tipo_atividade": texto(empresa.get("sped_codigo_tipo_atividade")),
                "sped_codigo_tipo_contribuicao": texto(
                    empresa.get("sped_codigo_tipo_contribuicao")
                ),
                "sped_cpf_contador": somente_digitos(empresa.get("sped_cpf_contador")) or None,
                "sped_crc_contador": texto(empresa.get("sped_crc_contador")),
                "sped_email_contador": texto(empresa.get("sped_email_contador")),
                "sped_junta_comercial": texto(empresa.get("sped_junta_comercial")),
                "sped_matriz": texto(empresa.get("sped_matriz")),
                "sped_natureza_pessoa_juridica": texto(
                    empresa.get("sped_natureza_pessoa_juridica")
                ),
                "sped_nome_contador": texto(empresa.get("sped_nome_contador")),
                "sped_registro_junta_comercial": texto(
                    empresa.get("sped_registro_junta_comercial")
                ),
                "sped_usa_contabilidade_terceirizada": texto(
                    empresa.get("sped_usa_contabilidade_terceirizada")
                ),
                "pagina_api": inteiro(dados.get("pagina")),
                "total_de_paginas_api": inteiro(dados.get("total_de_paginas")),
                "registros_api": inteiro(dados.get("registros")),
                "total_de_registros_api": inteiro(dados.get("total_de_registros")),
                "produto_servico_resumido_json": produto_servico_resumido_json,
                "dados_json": json.dumps(empresa, ensure_ascii=False),
                "dados_flat_json": json.dumps(achatar_json(empresa), ensure_ascii=False),
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
        if coluna != "codigo_empresa"
    )
    sql = f"""
        INSERT INTO raw_omie_empresas ({lista_colunas})
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
            logging.info("Pagina %s sem empresas. Fim da consulta.", pagina)
            break

        empresas = dados.get("empresas_cadastro", [])
        total_paginas_api = dados.get("total_de_paginas")
        if total_paginas_api is not None:
            total_paginas = int(total_paginas_api)

        if not isinstance(empresas, list):
            raise RuntimeError("Resposta inesperada: campo 'empresas_cadastro' nao e uma lista.")

        if not empresas:
            logging.info("Pagina %s sem empresas. Fim da consulta.", pagina)
            break

        salvos = salvar_empresas_no_banco(dados)
        total += salvos
        salvar_checkpoint(pagina, total)
        logging.info(
            "Pagina %s/%s processada: %s empresas gravadas no banco.",
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
    parser = argparse.ArgumentParser(description="Extrai empresas da API Omie para o MySQL.")
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
        "Extracao finalizada: %s empresas gravadas no banco %s.",
        total,
        MYSQL_DATABASE,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
