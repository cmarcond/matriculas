"""
listar_alunos.py - Lista alunos das abas Orientador e/ou Professor (somente leitura)

Uso:
  python listar_alunos.py [--tab orientador|professor|ambos] [--headless/--no-headless]

Flags:
  --tab        Qual aba consultar (default: ambos)
  --headless   Roda sem abrir janela do navegador (default)
  --no-headless  Abre janela do navegador
"""

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ENV_FILE = Path(__file__).parent / ".secrets.env"
load_dotenv(ENV_FILE)

PORTAL_URL = os.getenv("PORTAL_URL", "")
CPF = os.getenv("CPF", "")
SENHA = os.getenv("SENHA", "")

SEL_CPF_INPUT = "#usuarioTextBox"
SEL_SENHA_INPUT = "#senhaTextBox"
SEL_LOGIN_BUTTON = "#confirmarButton"
SEL_PROFESSOR_LINK = "#ctl00_ctl00_menuContentPlaceHolder_professorGraduacaoLinkButton"
SEL_STUDENT_TABLE = "table[id*='alunosGridView']"

WAIT = 2


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Student:
    codigo: str
    nome: str
    especial: str
    parecer: str


@dataclass
class ClassGroup:
    name: str
    students: list


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log(msg: str, level: str = "INFO"):
    ts = time.strftime("%H:%M:%S")
    prefix = {"INFO": "  ", "OK": "  ", "SKIP": "   ", "WARN": "  ", "ERROR": "  "}
    print(f"[{ts}] {prefix.get(level, '')} {msg}")


def safe_wait(page, seconds=WAIT):
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    time.sleep(seconds)


# ---------------------------------------------------------------------------
# Login & navigation
# ---------------------------------------------------------------------------

def do_login(page) -> bool:
    log(f"Navegando para {PORTAL_URL}")
    page.goto(PORTAL_URL, wait_until="networkidle")

    if not page.query_selector(SEL_CPF_INPUT):
        log("Pagina de login nao encontrada!", "ERROR")
        return False

    log("Preenchendo CPF e senha...")
    page.fill(SEL_CPF_INPUT, CPF)
    page.fill(SEL_SENHA_INPUT, SENHA)

    log("Clicando em 'Acessar'...")
    page.click(SEL_LOGIN_BUTTON)

    try:
        page.wait_for_url("**/Orientador**", timeout=15000)
    except PlaywrightTimeout:
        if "Acesso" in page.url:
            log("Login falhou — CPF ou senha incorretos!", "ERROR")
            return False
        log(f"Redirecionado para URL inesperada: {page.url}", "WARN")

    safe_wait(page)
    log(f"Login bem-sucedido! URL: {page.url}", "OK")
    return True


def navigate_to_professor(page) -> bool:
    prof_link = page.query_selector(SEL_PROFESSOR_LINK)
    if not prof_link:
        log("Link da aba Professor nao encontrado!", "ERROR")
        return False
    prof_link.click()
    safe_wait(page, 3)
    if not page.query_selector(SEL_STUDENT_TABLE):
        log("Tabela de alunos nao encontrada na aba Professor!", "ERROR")
        return False
    return True


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def get_students_flat(page) -> list[Student]:
    """Parse students from all visible tables (Orientador tab - flat list)."""
    students = []
    tables = page.query_selector_all(SEL_STUDENT_TABLE)
    for table in tables:
        rows = table.query_selector_all("tr")
        for row in rows[1:]:
            cells = row.query_selector_all("td")
            if len(cells) < 4:
                continue
            students.append(Student(
                codigo=cells[0].inner_text().strip(),
                nome=cells[1].inner_text().strip(),
                especial=cells[2].inner_text().strip(),
                parecer=cells[3].inner_text().strip(),
            ))
    return students


def get_class_groups(page) -> list[ClassGroup]:
    """Parse students from the Professor tab, grouped by class.

    The Professor page has multiple 'listaAlunosControl{N}' containers, each
    containing a span[id*='descricaoGrupoLabel'] with the class name and a
    table[id*='alunosGridView'] with that class's students.
    """
    groups = []
    tables = page.query_selector_all(SEL_STUDENT_TABLE)
    if not tables:
        return groups

    # Filter labels to only those with actual text (skip the empty template span)
    labels = [
        el for el in page.query_selector_all("span[id*='descricaoGrupoLabel']")
        if el.inner_text().strip()
    ]

    if len(labels) == len(tables):
        # Happy path: one label per table
        for label, table in zip(labels, tables):
            group_name = label.inner_text().strip()
            students = _parse_table(table)
            groups.append(ClassGroup(name=group_name, students=students))
    else:
        # Fallback: walk up from each table to its UpdatePanel ancestor and
        # find the sibling descricaoGrupoLabel inside the same panel.
        for i, table in enumerate(tables):
            group_name = table.evaluate("""el => {
                const panel = el.closest('[id$=_alunoUpdatePanel]');
                if (panel) {
                    const span = panel.querySelector("span[id*='descricaoGrupoLabel']");
                    if (span && span.textContent.trim()) return span.textContent.trim();
                }
                return '';
            }""") or f"Turma {i + 1}"
            students = _parse_table(table)
            groups.append(ClassGroup(name=group_name, students=students))

    return groups


def _parse_table(table) -> list[Student]:
    """Parse a single student table element into Student objects."""
    students = []
    rows = table.query_selector_all("tr")
    for row in rows[1:]:
        cells = row.query_selector_all("td")
        if len(cells) < 4:
            continue
        students.append(Student(
            codigo=cells[0].inner_text().strip(),
            nome=cells[1].inner_text().strip(),
            especial=cells[2].inner_text().strip(),
            parecer=cells[3].inner_text().strip(),
        ))
    return students


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_header(title: str):
    width = 70
    print()
    print("=" * width)
    print(f"  {title}")
    print("=" * width)


def print_student_table(students: list[Student]):
    if not students:
        print("  (nenhum aluno encontrado)")
        return

    # Compute column widths from actual data
    w_cod = max(len("Codigo"), *(len(s.codigo) for s in students))
    w_nom = max(len("Nome"), *(len(s.nome) for s in students))
    w_esp = max(len("Especial"), *(len(s.especial) for s in students))
    w_par = max(len("Parecer"), *(len(s.parecer) for s in students))

    fmt = f"  {{:<{w_cod}}}  {{:<{w_nom}}}  {{:<{w_esp}}}  {{:<{w_par}}}"
    print(fmt.format("Codigo", "Nome", "Especial", "Parecer"))
    print(fmt.format("-" * w_cod, "-" * w_nom, "-" * w_esp, "-" * w_par))
    for s in students:
        print(fmt.format(s.codigo, s.nome, s.especial, s.parecer))


def print_summary(students: list[Student], label: str = ""):
    total = len(students)
    deferido = sum(1 for s in students if s.parecer.lower() == "deferido")
    pendente = total - deferido
    prefix = f"[{label}] " if label else ""
    print(f"\n  {prefix}Total: {total}  |  Deferido: {deferido}  |  Pendente: {pendente}")


def display_orientador(page):
    print_header("Aba Orientador")
    students = get_students_flat(page)
    print_student_table(students)
    print_summary(students, "Orientador")
    return students


def display_professor(page):
    print_header("Aba Professor")

    if not navigate_to_professor(page):
        return []

    groups = get_class_groups(page)
    all_students = []

    if not groups:
        log("Nenhuma turma encontrada na aba Professor.", "WARN")
        return []

    for group in groups:
        print(f"\n  --- {group.name} ({len(group.students)} aluno(s)) ---")
        print_student_table(group.students)
        print_summary(group.students, group.name)
        all_students.extend(group.students)

    if len(groups) > 1:
        print_summary(all_students, "Professor (total)")

    return all_students


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Lista alunos do Portal Academico do ITA (somente leitura)"
    )
    parser.add_argument(
        "--tab",
        choices=["orientador", "professor", "ambos"],
        default="ambos",
        help="Qual aba consultar (default: ambos)",
    )
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Executar sem janela do navegador (default: --headless)",
    )
    args = parser.parse_args()

    if not PORTAL_URL or not CPF or not SENHA:
        log("PORTAL_URL, CPF e SENHA devem ser definidos no arquivo .secrets.env", "ERROR")
        sys.exit(1)

    log("=" * 60)
    log("Listagem de Alunos — Portal Academico ITA")
    log("=" * 60)
    log(f"Aba: {args.tab.upper()}")
    log(f"Modo: {'HEADLESS' if args.headless else 'HEADED (visual)'}")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=args.headless,
            slow_mo=500 if not args.headless else 0,
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            ignore_https_errors=True,
        )
        page = context.new_page()

        try:
            if not do_login(page):
                log("Nao foi possivel fazer login. Abortando.", "ERROR")
                browser.close()
                sys.exit(1)

            all_students = []

            if args.tab in ("orientador", "ambos"):
                all_students.extend(display_orientador(page))

            if args.tab in ("professor", "ambos"):
                all_students.extend(display_professor(page))

            if args.tab == "ambos" and all_students:
                print_summary(all_students, "GERAL")

        except KeyboardInterrupt:
            log("\nInterrompido pelo usuario.", "WARN")
        except Exception as e:
            log(f"Erro inesperado: {e}", "ERROR")
            import traceback
            traceback.print_exc()
        finally:
            browser.close()

    print()
    log("Finalizado.", "OK")


if __name__ == "__main__":
    main()
