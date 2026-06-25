CREATE DATABASE IF NOT EXISTS omie_db
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE omie_db;

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
COLLATE utf8mb4_unicode_ci;
