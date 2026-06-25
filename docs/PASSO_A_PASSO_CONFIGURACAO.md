# Passo a Passo do que foi Configurado

## 1. Ambiente de Banco

Foi criada a pasta:

```text
banco/
```

Dentro dela, foi configurado um ambiente com:

- MySQL 8.4
- phpMyAdmin 5.2
- banco `omie_db`
- usuario `omie_user`
- porta local MySQL `3307`
- porta local phpMyAdmin `8080`

Arquivo principal:

```text
banco/docker-compose.yml
```

## 2. Script de Inicializacao do Banco

Foi criado:

```text
banco/mysql/init/01_schema_omie.sql
```

Esse script:

- cria a tabela `raw_omie_clientes`;
- deixa o banco pronto para receber os dados diretamente pela API.

## 3. Regra de Estrutura das APIs

Cada API do Omie possui um script de extracao e cria somente uma tabela.
Quando a API possuir um cliente associado, a tabela sera vinculada a
`raw_omie_clientes.codigo_cliente_omie`.

## 4. Arquivo de Dependencias Python

Foi criado:

```text
requirements.txt
```

Ele lista as bibliotecas recomendadas para:

- consumir APIs;
- ler e gerar Excel;
- tratar dados;
- conectar em banco;
- carregar variaveis de ambiente.

## 5. Arquivo de Ambiente

Foi criado:

```text
.env.example
```

Ele serve como modelo para criar o arquivo `.env`, onde ficam credenciais e parametros locais.

O arquivo `.env` deve ser criado manualmente e nao deve ser enviado ao Git.

## 6. Protecao de Arquivos

Foi criado:

```text
.gitignore
```

Ele evita versionar:

- credenciais;
- ambiente virtual Python;
- caches;
- arquivos CSV/XLSX/JSON gerados;
- dados locais de banco.

## 7. Manual

Foi criado:

```text
docs/MANUAL_USO.md
```

Ele explica:

- objetivo do projeto;
- estrutura de pastas;
- configuracao inicial;
- execucao do script Omie;
- uso do banco local;
- controles minimos de qualidade.

## 8. O que Ainda Depende do ADM

Instalacoes de sistema ainda necessarias:

- Docker Desktop ou alternativa com MySQL/phpMyAdmin;
- MySQL/MariaDB, se a empresa nao permitir Docker;
- DBeaver;
- Git;
- drivers ODBC conforme origem do SCI Unico;
- acesso/driver especifico do SCI Unico, caso exista.
