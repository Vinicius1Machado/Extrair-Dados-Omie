
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

## `listar_clientes_tags_omie.py`

Consulta a API `Clientes - Tags` para os clientes/fornecedores ja gravados em
`raw_omie_clientes` e gera:

```text
registros no MySQL:
- `raw_omie_clientes_tags`
```

Cada linha representa uma tag do cliente/fornecedor, vinculada por
`codigo_cliente_omie`.

Execucao:

```powershell
python scripts\listar_clientes_tags_omie.py
```

Teste com poucos clientes:

```powershell
python scripts\listar_clientes_tags_omie.py --limite 10 --ignorar-checkpoint
```

## `listar_empresas_omie.py`

Extrai empresas da API Omie e gera:

```text
registros no MySQL:
- `raw_omie_empresas`
```

O script pagina automaticamente ate a ultima pagina informada pela API e grava
os campos cadastrais/fiscais da empresa, alem do JSON original.

Execucao:

```powershell
python scripts\listar_empresas_omie.py
```

## `listar_bancos_omie.py`

Extrai bancos da API Omie e gera:

```text
registros no MySQL:
- `raw_omie_bancos`
```

O script pagina automaticamente ate a ultima pagina informada pela API.

Execucao:

```powershell
python scripts\listar_bancos_omie.py
```

## `listar_cidades_omie.py`

Extrai cidades da API Omie e gera:

```text
registros no MySQL:
- `raw_omie_cidades`
```

O script pagina automaticamente ate a ultima pagina informada pela API.

Execucao:

```powershell
python scripts\listar_cidades_omie.py
```

## `listar_paises_omie.py`

Extrai paises da API Omie e gera:

```text
registros no MySQL:
- `raw_omie_paises`
```

O script tambem cria o relacionamento:

```text
raw_omie_clientes.codigo_pais -> raw_omie_paises.codigo_pais
```

Execucao:

```powershell
python scripts\listar_paises_omie.py
```

## `listar_contas_pagar_omie.py`

Extrai contas a pagar da API Omie e gera:

```text
registros no MySQL:
- `raw_omie_contas_pagar`
```

Relacionamento principal:

```text
raw_omie_contas_pagar.codigo_cliente_fornecedor -> raw_omie_clientes.codigo_cliente_omie
```

O script tambem cria indices para futuras vinculacoes financeiras:
`codigo_categoria`, `id_conta_corrente`, `codigo_projeto` e `codigo_tipo_documento`.

Execucao completa:

```powershell
python scripts\listar_contas_pagar_omie.py
```

Teste de pagina unica:

```powershell
python scripts\listar_contas_pagar_omie.py --pagina-inicial 1 --pagina-final 1
```

## `listar_contas_receber_omie.py`

Extrai contas a receber da API Omie e gera:

```text
registros no MySQL:
- `raw_omie_contas_receber`
```

Relacionamento principal:

```text
raw_omie_contas_receber.codigo_cliente_fornecedor -> raw_omie_clientes.codigo_cliente_omie
```

O script tambem cria indices para futuras vinculacoes financeiras:
`codigo_categoria`, `id_conta_corrente`, `codigo_projeto` e `codigo_tipo_documento`.

Execucao completa:

```powershell
python scripts\listar_contas_receber_omie.py
```

Teste de pagina unica:

```powershell
python scripts\listar_contas_receber_omie.py --pagina-inicial 1 --pagina-final 1
```

## `listar_titulos_lancados_omie.py`

Extrai titulos lancados da API `PesquisarLancamentos` e gera:

```text
registros no MySQL:
- `raw_omie_titulos_lancados`
```

Relacionamento principal:

```text
raw_omie_titulos_lancados.codigo_cliente -> raw_omie_clientes.codigo_cliente_omie
```

O script guarda o titulo consolidado, os lancamentos, os departamentos e o resumo
em JSON, alem dos principais campos em colunas consultaveis.

Execucao completa:

```powershell
python scripts\listar_titulos_lancados_omie.py
```

Teste de pagina unica:

```powershell
python scripts\listar_titulos_lancados_omie.py --pagina-inicial 1 --pagina-final 1
```

## `setup_ambiente_python.ps1`

Cria o ambiente virtual `.venv` e instala as dependencias do `requirements.txt`.

Execucao:

```powershell
.\scripts\setup_ambiente_python.ps1
```
