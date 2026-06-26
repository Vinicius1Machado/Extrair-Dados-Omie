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
COLLATE utf8mb4_unicode_ci;

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
COLLATE utf8mb4_unicode_ci;
