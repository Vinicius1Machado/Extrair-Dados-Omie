# Scripts

## `listar_clientes_omie.py`

Extrai clientes/fornecedores da API Omie e gera:

```text
registros no MySQL:
- `raw_omie_clientes`

O script cria e atualiza somente a tabela correspondente a API de clientes.
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

## `setup_ambiente_python.ps1`

Cria o ambiente virtual `.venv` e instala as dependencias do `requirements.txt`.

Execucao:

```powershell
.\scripts\setup_ambiente_python.ps1
```
