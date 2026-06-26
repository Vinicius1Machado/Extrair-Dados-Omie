# Ferramentas para Instalar

## Essenciais

- Python 3.12 ou superior
- Git
- Visual Studio Code
- Microsoft Excel
- DBeaver Community

## Banco de Dados

Opcao recomendada:

- Docker Desktop
- WSL 2

Servicos que serao executados via Docker:

- MySQL 8.4
- phpMyAdmin 5.2

Opcao sem Docker:

- MySQL Server 8 ou MariaDB
- Apache ou XAMPP/Laragon para phpMyAdmin
- phpMyAdmin

## Drivers e Conectores

- MySQL Connector/ODBC
- MySQL Connector/Python
- Microsoft ODBC Driver 18 for SQL Server
- OLE DB Driver for SQL Server
- Firebird ODBC Driver, se o SCI Unico usar Firebird
- PostgreSQL ODBC Driver, se alguma base usar PostgreSQL

## Python

Instalar dependencias com:

```powershell
pip install -r requirements.txt
```

Pacotes previstos:

- requests
- pandas
- openpyxl
- SQLAlchemy
- PyMySQL
- python-dotenv
- pyodbc
- XlsxWriter
- Unidecode

## SAP

Validar acessos e ferramentas:

- SAP S/4HANA Migration Cockpit
- Fiori Launchpad
- SAP GUI, se aplicavel
- template oficial atualizado de Fornecedor
- permissoes para carga/teste no ambiente de qualidade

## SCI Unico

Validar com o fornecedor/ADM:

- se existe exportacao CSV/XLSX oficial;
- se existe API;
- se ha acesso direto ao banco;
- qual banco e usado por baixo;
- qual driver ODBC e necessario;
- se ha restricao de leitura em producao.
