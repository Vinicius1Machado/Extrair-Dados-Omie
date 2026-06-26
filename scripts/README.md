
## `listar_clientes_omie.py`

Extrai clientes/fornecedores da API Omie e gera:

```text
registros no MySQL:
- `raw_omie_clientes`

O script cria e atualiza somente a tabela correspondente a API de clientes.
As tags dos clientes sao gravadas nas colunas `tags` e `tags_json`.
Todos os campos retornados pela API sao gravados em colunas proprias ou em
colunas JSON para estruturas aninhadas. O script adiciona automaticamente as
colunas conhecidas que ainda nao existirem no banco.
```

Antes de executar, crie o arquivo `.env` na raiz do projeto com:

```text
OMIE_APP_KEY=
OMIE_APP_SECRET=
```

Execucao:

```powershell
python scripts\listar_clientes_omie.py
```

## `listar_clientes_caracteristicas_omie.py`

Consulta a API `Clientes - Caracteristicas` para os clientes/fornecedores ja
gravados em `raw_omie_clientes` e gera:

```text
registros no MySQL:
- `raw_omie_clientes_caracteristicas`
```

Cada linha representa uma caracteristica do cliente/fornecedor, vinculada por
`codigo_cliente_omie`.

Execucao:

```powershell
python scripts\listar_clientes_caracteristicas_omie.py
```

Teste com poucos clientes:

```powershell
python scripts\listar_clientes_caracteristicas_omie.py --limite 10 --ignorar-checkpoint
```

## `setup_ambiente_python.ps1`

Cria o ambiente virtual `.venv` e instala as dependencias do `requirements.txt`.

Execucao:

```powershell
.\scripts\setup_ambiente_python.ps1
```
