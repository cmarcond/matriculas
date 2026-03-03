# Matriculas

Automation scripts for student enrollment authorization on an academic portal built with ASP.NET WebForms. Uses [Playwright](https://playwright.dev/python/) for browser automation.

## Scripts

| Script | Description |
|---|---|
| `listar_alunos.py` | Read-only listing of students from Orientador and/or Professor tabs, with class grouping |
| `autorizar_alunos.py` | Authorizes (defers) pending students on the Orientador tab |
| `autorizar_professor.py` | Authorizes (defers) pending students on the Professor tab |
| `buscar_emails.py` | Fetches student emails from the Histórico interno report (Professor tab) |
| `buscar_programas.py` | Fetches student programs/courses from the Histórico interno report (Professor tab) |
| `buscar_historico.py` | Combined: fetches both email and program in a single pass (Professor tab) |

## Setup

```bash
# Create venv, install dependencies, and download Chromium
make install
```

Create a `.secrets.env` file with your credentials:

```
PORTAL_URL=https://your-portal-url.example.com/path
CPF=your-cpf
SENHA=your-password
```

## Usage

```bash
make help                # Show all available targets
make list                # List all students (both tabs)
make list-orientador     # List Orientador tab students
make list-professor      # List Professor tab students (grouped by class)
make autorizar-orientador  # Authorize Orientador students
make autorizar-professor   # Authorize Professor students
make autorizar-todos       # Run both authorizations
make dry-run-orientador    # Dry-run Orientador
make dry-run-professor     # Dry-run Professor

# Data collection (emails, programs)
make emails-professor      # Fetch emails → emails_professor.csv
make programas-professor   # Fetch programs → programas_professor.csv
make historico-professor   # Fetch both → historico_professor.csv
```

All data collection scripts support resume — if interrupted, re-running skips already-fetched students. Use `--limit N` to test with a subset.

## Requirements

- Python 3.11+
- Playwright
- python-dotenv
