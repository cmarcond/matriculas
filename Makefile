PYTHON := venv/bin/python
PIP    := venv/bin/pip

.DEFAULT_GOAL := help

.PHONY: help install \
        list list-orientador list-professor \
        autorizar-orientador autorizar-professor autorizar-todos \
        dry-run-orientador dry-run-professor \
        emails-professor programas-professor historico-professor

## -----------------------------------------------------------------------
##  help — Show available targets (default)
## -----------------------------------------------------------------------

help: ## Show this help message
	@echo ""
	@echo "Matriculas ITA — Comandos disponíveis"
	@echo "======================================"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | \
		awk -F ':.*## ' '{printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@echo ""

## -----------------------------------------------------------------------
##  Setup
## -----------------------------------------------------------------------

install: ## Create venv + install deps + playwright browsers
	python3 -m venv venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(PYTHON) -m playwright install chromium

## -----------------------------------------------------------------------
##  Listing (read-only)
## -----------------------------------------------------------------------

list: ## List all students (both tabs)
	$(PYTHON) listar_alunos.py --tab ambos

list-orientador: ## List Orientador tab students
	$(PYTHON) listar_alunos.py --tab orientador

list-professor: ## List Professor tab students (grouped by class)
	$(PYTHON) listar_alunos.py --tab professor

## -----------------------------------------------------------------------
##  Authorization
## -----------------------------------------------------------------------

autorizar-orientador: ## Authorize Orientador students (headless)
	$(PYTHON) autorizar_alunos.py --headless

autorizar-professor: ## Authorize Professor students (headless)
	$(PYTHON) autorizar_professor.py --headless

autorizar-todos: autorizar-orientador autorizar-professor ## Run both authorizations sequentially

## -----------------------------------------------------------------------
##  Dry-run
## -----------------------------------------------------------------------

dry-run-orientador: ## Dry-run Orientador (list without changes)
	$(PYTHON) autorizar_alunos.py --headless --dry-run

dry-run-professor: ## Dry-run Professor (list without changes)
	$(PYTHON) autorizar_professor.py --headless --dry-run

## -----------------------------------------------------------------------
##  Email collection
## -----------------------------------------------------------------------

emails-professor: ## Fetch emails from Professor tab students
	$(PYTHON) buscar_emails.py --headless

programas-professor: ## Fetch programs/courses from Professor tab students
	$(PYTHON) buscar_programas.py --headless

historico-professor: ## Fetch emails + programs combined from Professor tab
	$(PYTHON) buscar_historico.py --headless
