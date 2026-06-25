# Manual de Uso

## Objetivo

Este projeto apoia a implantacao do SAP S/4HANA por meio da extracao, armazenamento, tratamento e preparacao de dados vindos do Omie e do SCI Unico.

O fluxo proposto e:

1. Extrair dados dos sistemas legados.
2. Armazenar os dados em um banco local.
3. Tratar, padronizar e validar os dados em SQL.
4. Gerar arquivos no layout oficial de migracao SAP.

## Estrutura do Projeto

```text
Omie/
├─ arquivos/              # Arquivos extraidos e saidas geradas
├─ banco/                 # Ambiente MySQL + phpMyAdmin
├─ docs/                  # Documentacao do projeto
├─ scripts/               # Scripts Python de extracao e carga
├─ agentes/               # Arquivos e instrucoes de agentes
├─ .env.example           # Modelo de variaveis de ambiente
├─ .gitignore             # Arquivos que nao devem entrar no Git
└─ requirements.txt       # Bibliotecas Python do projeto
```

## Configuracao Inicial

### 1. Criar o arquivo `.env`

Copie `.env.example` para `.env` e preencha as credenciais reais:

```powershell
Copy-Item .env.example .env
```

Campos principais:

```text
OMIE_APP_KEY=
OMIE_APP_SECRET=
MYSQL_HOST=localhost
MYSQL_PORT=3307
MYSQL_DATABASE=omie_db
MYSQL_USER=omie_user
MYSQL_PASSWORD=omie_123
```

### 2. Criar ambiente virtual Python

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Se o PowerShell bloquear a ativacao do ambiente virtual:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Se houver erro de permissao durante a criacao do ambiente virtual, execute o mesmo passo em um PowerShell aberto normalmente pelo usuario, fora do terminal controlado pela IDE.

## Extracao do Omie

Execute:

```powershell
python scripts\listar_clientes_omie.py
```

Saida esperada:

```text
Pagina 1 processada: clientes gravados no banco
Extracao finalizada
```

O script pagina automaticamente ate a API informar que nao existem mais registros e grava cada pagina somente na tabela `raw_omie_clientes`.

## Banco Local

O ambiente de banco preparado usa MySQL + phpMyAdmin via Docker.

Para iniciar, depois que o Docker Desktop estiver instalado:

```powershell
cd banco
docker compose up -d
```

Acesse:

```text
http://localhost:8080
```

Credenciais:

```text
Servidor: mysql
Usuario: root
Senha: omie_root_123
Banco: omie_db
```

## Carga Inicial no Banco

Na primeira subida do Docker, o MySQL executa os scripts em:

```text
banco/mysql/init/
```

Eles criam:

- tabela `raw_omie_clientes`

Cada nova API do Omie deve possuir um script e uma unica tabela correspondente.

## Reimportar Dados

Os scripts de inicializacao rodam apenas quando o volume do MySQL e criado pela primeira vez.

Para recriar o banco local:

```powershell
cd banco
docker compose down -v
docker compose up -d
```

Atencao: `down -v` remove os dados locais do banco deste ambiente.

## Fluxo Recomendado de Migracao

1. Extrair Omie diretamente para o MySQL com `scripts\listar_clientes_omie.py`.
2. Extrair SCI Unico para CSV/XLSX e salvar em `arquivos/`.
3. Carregar dados brutos em tabelas `raw_*`.
4. Padronizar em tabelas `stg_*`.
5. Criar queries finais para cada aba do layout SAP.
6. Validar obrigatorios, duplicidades, CPF/CNPJ, tamanho dos campos e codigos SAP.
7. Gerar o Excel final no template SAP.

## Controles Minimos de Qualidade

Antes de enviar dados para migracao SAP, validar:

- fornecedores duplicados por CNPJ/CPF;
- campos obrigatorios vazios;
- codigo de pais e estado;
- tamanho maximo dos campos SAP;
- conta de reconciliacao;
- empresa;
- organizacao de compras;
- dados bancarios;
- categoria fiscal;
- consistencia entre Omie e SCI Unico.
