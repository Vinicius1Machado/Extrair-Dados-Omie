# Ambiente de Banco de Dados

Este ambiente usa MySQL com phpMyAdmin.

## Servicos

- MySQL: `localhost:3307`
- phpMyAdmin: <http://localhost:8080>
- Banco: `omie_db`
- Usuario de aplicacao: `omie_user`
- Senha de aplicacao: `omie_123`
- Usuario administrador: `root`
- Senha administrador: `omie_root_123`

## Como iniciar

Na pasta `banco`, execute:

```powershell
docker compose up -d
```

Depois acesse:

```text
http://localhost:8080
```

## O que sera criado

Na primeira inicializacao, o MySQL cria:

- tabela `raw_omie_clientes`

Os dados da Omie sao gravados diretamente no banco pelo script Python.

## Recriar Banco Local

Os scripts de inicializacao do MySQL rodam apenas quando o volume do banco e criado pela primeira vez.
Para recriar o banco local:

```powershell
docker compose down -v
docker compose up -d
```

Esse comando remove o volume local do MySQL deste ambiente.

Para atualizar os dados da Omie sem recriar o banco, execute na raiz do projeto:

```powershell
python scripts\listar_clientes_omie.py
```

## Regra de tabelas

Cada API do Omie possui um script de extracao e uma unica tabela correspondente.
As proximas tabelas poderao referenciar `raw_omie_clientes.codigo_cliente_omie`.
