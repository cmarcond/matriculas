"""
buscar_emails.py - Busca emails dos alunos na aba Professor do Portal Acadêmico do ITA

Navega ao "Histórico interno" de cada aluno, aceita o alert de confirmação,
extrai o campo E-MAIL e salva incrementalmente em CSV.

Uso:
  python buscar_emails.py [--headless/--no-headless] [--limit N] [--output FILE]
"""

import argparse
import csv
import os
import re
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
MAX_RETRIES = 3
DEFAULT_OUTPUT = "emails_professor.csv"


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class Student:
    codigo: str
    nome: str
    turma: str
    table_index: int    # which table (0-based)
    row_index: int      # which data row within that table (0-based, excludes header)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S")
    prefix = {"INFO": "ℹ️ ", "OK": "✅", "SKIP": "⏭️ ", "WARN": "⚠️ ", "ERROR": "❌"}
    print(f"[{ts}] {prefix.get(level, '')} {msg}")


def safe_wait(page, seconds=WAIT):
    """Wait for page to settle, tolerating errors."""
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    time.sleep(seconds)


def ensure_professor_tab(page):
    """Make sure we're on the Professor tab with the student tables visible.
    Re-logs in and re-navigates if needed. Returns True on success."""
    for attempt in range(MAX_RETRIES):
        if page.query_selector(SEL_STUDENT_TABLE):
            prof_li = page.query_selector("#ctl00_ctl00_menuContentPlaceHolder_liProfessorGraduacao")
            if prof_li and "selecionado" in (prof_li.get_attribute("class") or ""):
                return True

        log(f"  Recuperando sessão (tentativa {attempt + 1}/{MAX_RETRIES})...", "WARN")

        prof_link = page.query_selector(SEL_PROFESSOR_LINK)
        if prof_link:
            try:
                prof_link.click()
                safe_wait(page, 3)
                if page.query_selector(SEL_STUDENT_TABLE):
                    return True
            except Exception:
                pass

        # Full re-login
        try:
            page.goto(PORTAL_URL, wait_until="networkidle", timeout=30000)
            safe_wait(page)

            login_field = page.query_selector(SEL_CPF_INPUT)
            if login_field:
                page.fill(SEL_CPF_INPUT, CPF)
                page.fill(SEL_SENHA_INPUT, SENHA)
                page.click(SEL_LOGIN_BUTTON)
                try:
                    page.wait_for_url("**/Orientador**", timeout=15000)
                except PlaywrightTimeout:
                    pass
                safe_wait(page)

            prof_link = page.query_selector(SEL_PROFESSOR_LINK)
            if prof_link:
                prof_link.click()
                safe_wait(page, 3)
                if page.query_selector(SEL_STUDENT_TABLE):
                    log("  Sessão recuperada!", "OK")
                    return True
        except Exception as e:
            log(f"  Erro na recuperação: {e}", "ERROR")
            time.sleep(3)

    log("Não foi possível recuperar a sessão!", "ERROR")
    return False


# ---------------------------------------------------------------------------
# Student parsing
# ---------------------------------------------------------------------------

def _get_group_names(page, tables):
    """Resolve turma/group name for each table, matching listar_alunos.py logic."""
    labels = [
        el for el in page.query_selector_all("span[id*='descricaoGrupoLabel']")
        if el.inner_text().strip()
    ]
    if len(labels) == len(tables):
        return [l.inner_text().strip() for l in labels]

    # Fallback: walk up from each table to its UpdatePanel ancestor
    names = []
    for i, table in enumerate(tables):
        name = table.evaluate("""el => {
            const panel = el.closest('[id$=_alunoUpdatePanel]');
            if (panel) {
                const span = panel.querySelector("span[id*='descricaoGrupoLabel']");
                if (span && span.textContent.trim()) return span.textContent.trim();
            }
            return '';
        }""") or f"Turma {i + 1}"
        names.append(name)
    return names


def get_students(page):
    """Parse students from Professor tab tables, recording table/row indices and turma."""
    students = []
    tables = page.query_selector_all(SEL_STUDENT_TABLE)
    if not tables:
        return students
    group_names = _get_group_names(page, tables)
    for t_idx, table in enumerate(tables):
        turma = group_names[t_idx] if t_idx < len(group_names) else f"Turma {t_idx + 1}"
        rows = table.query_selector_all("tr")
        for r_idx, row in enumerate(rows[1:]):
            cells = row.query_selector_all("td")
            if len(cells) < 4:
                continue
            students.append(Student(
                codigo=cells[0].inner_text().strip(),
                nome=cells[1].inner_text().strip(),
                turma=turma,
                table_index=t_idx,
                row_index=r_idx,
            ))
    return students


def find_historico_link(page, student):
    """Find the 'Histórico interno' link in the student's row (fresh DOM lookup)."""
    tables = page.query_selector_all(SEL_STUDENT_TABLE)
    if student.table_index >= len(tables):
        return None
    rows = tables[student.table_index].query_selector_all("tr")
    # row_index is 0-based among data rows; rows[0] is header
    actual_row_idx = student.row_index + 1
    if actual_row_idx >= len(rows):
        return None
    row = rows[actual_row_idx]
    # The link has no id — find by text content
    for link in row.query_selector_all("a"):
        if "istórico" in (link.inner_text() or ""):
            return link
    return None


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def load_existing_emails(csv_path):
    """Load already-fetched emails from CSV. Returns dict {codigo: email}."""
    existing = {}
    if not csv_path.exists():
        return existing
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            existing[row["codigo"]] = row["email"]
    return existing


def append_to_csv(csv_path, turma, codigo, nome, email):
    """Append a single row to the CSV file, creating it with headers if needed."""
    write_header = not csv_path.exists() or csv_path.stat().st_size == 0
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["turma", "codigo", "nome", "email"])
        writer.writerow([turma, codigo, nome, email])


# ---------------------------------------------------------------------------
# Email extraction
# ---------------------------------------------------------------------------

def fetch_email(page, student):
    """Navigate to student's Histórico interno, extract email. Returns email or ''."""
    link = find_historico_link(page, student)
    if not link:
        log(f"  Link 'Histórico interno' não encontrado", "ERROR")
        return ""

    # Register dialog handler to accept the confirm alert
    page.once("dialog", lambda d: d.accept())

    link.click()

    # Wait for the Relatorios page to load (URL changes from Professor)
    try:
        page.wait_for_url("**/Relatorios**", timeout=15000)
    except PlaywrightTimeout:
        pass
    safe_wait(page, 3)

    # Dismiss the "Informação" modal if present (print settings dialog)
    try:
        confirmar_btn = page.query_selector("input[value='Confirmar'], button:has-text('Confirmar')")
        if confirmar_btn:
            confirmar_btn.click()
            time.sleep(1)
    except Exception:
        pass

    # Poll the RelatorioFrame until E-MAIL is found (frame may take time to load)
    email = ""
    for wait_attempt in range(10):
        frame = page.frame("RelatorioFrame")
        if frame:
            try:
                body_text = frame.inner_text("body")
                match = re.search(r"E-MAIL:\s*(\S+@\S+)", body_text, re.IGNORECASE)
                if match:
                    email = match.group(1).strip().rstrip(".,:;")
                    break
            except Exception:
                pass
        # Also try main page body as fallback
        if not email:
            try:
                body_text = page.inner_text("body")
                match = re.search(r"E-MAIL:\s*(\S+@\S+)", body_text, re.IGNORECASE)
                if match:
                    email = match.group(1).strip().rstrip(".,:;")
                    break
            except Exception:
                pass
        time.sleep(2)

    # Navigate back to Professor tab
    page.go_back()
    safe_wait(page, 2)

    return email


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Busca emails dos alunos na aba Professor"
    )
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Executar sem janela do navegador (default: --headless)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limitar a N alunos (0 = todos)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=DEFAULT_OUTPUT,
        help=f"Arquivo CSV de saída (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    csv_path = Path(args.output)

    if not PORTAL_URL or not CPF or not SENHA:
        log("PORTAL_URL, CPF e SENHA devem ser definidos no .secrets.env", "ERROR")
        sys.exit(1)

    log("=" * 60)
    log("Busca de Emails — Aba Professor")
    log("=" * 60)
    log(f"Modo: {'HEADLESS' if args.headless else 'HEADED (visual)'}")
    if args.limit:
        log(f"Limite: {args.limit} aluno(s)")
    log(f"Saída: {csv_path}")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=args.headless,
            slow_mo=500 if not args.headless else 0,
        )
        page = browser.new_context(
            viewport={"width": 1280, "height": 900},
            ignore_https_errors=True,
        ).new_page()

        try:
            # Login
            log(f"Navegando para {PORTAL_URL}")
            page.goto(PORTAL_URL, wait_until="networkidle")
            page.fill(SEL_CPF_INPUT, CPF)
            page.fill(SEL_SENHA_INPUT, SENHA)
            page.click(SEL_LOGIN_BUTTON)
            try:
                page.wait_for_url("**/Orientador**", timeout=15000)
            except PlaywrightTimeout:
                pass
            safe_wait(page)
            log("Login OK!", "OK")

            # Navigate to Professor tab
            if not ensure_professor_tab(page):
                sys.exit(1)

            # Parse students
            students = get_students(page)
            log(f"Total de alunos encontrados: {len(students)}")

            # Load existing emails for resume support
            existing = load_existing_emails(csv_path)
            if existing:
                log(f"Emails já coletados (CSV existente): {len(existing)}", "SKIP")

            # Filter to pending students
            pending = [s for s in students if s.codigo not in existing]
            if args.limit:
                pending = pending[:args.limit]

            skipped = len(students) - len(pending) - (len(existing) - len([s for s in students if s.codigo in existing]))
            already = sum(1 for s in students if s.codigo in existing)
            log(f"Pendentes: {len(pending)} | Já coletados: {already}")

            if not pending:
                log("Todos os emails já foram coletados!", "OK")
                browser.close()
                return

            total = len(pending)
            success = 0
            fail = 0

            for i, student in enumerate(pending, 1):
                pct = int(i / total * 100)
                bar = "█" * (pct // 2) + "░" * (50 - pct // 2)
                print(f"\r  [{bar}] {pct}% ({i}/{total})", end="", flush=True)
                print()

                # Retry logic per student
                email = ""
                for attempt in range(MAX_RETRIES):
                    try:
                        if not ensure_professor_tab(page):
                            log(f"  Não foi possível voltar à aba Professor", "ERROR")
                            continue

                        # Re-parse to get fresh element references and find student
                        fresh_students = get_students(page)
                        fresh = next((s for s in fresh_students if s.codigo == student.codigo), None)
                        if not fresh:
                            log(f"  Aluno {student.codigo} não encontrado na re-leitura", "ERROR")
                            break

                        email = fetch_email(page, fresh)
                        if email:
                            break
                        else:
                            log(f"  Email não encontrado (tentativa {attempt + 1}/{MAX_RETRIES})", "WARN")
                    except Exception as e:
                        log(f"  Erro (tentativa {attempt + 1}/{MAX_RETRIES}): {e}", "ERROR")
                        time.sleep(2)

                if email:
                    append_to_csv(csv_path, student.turma, student.codigo, student.nome, email)
                    log(f"[{i}/{total}] [{student.codigo}] {student.nome} ({student.turma}) — {email}", "OK")
                    success += 1
                else:
                    append_to_csv(csv_path, student.turma, student.codigo, student.nome, "")
                    log(f"[{i}/{total}] [{student.codigo}] {student.nome} ({student.turma}) — email não encontrado", "ERROR")
                    fail += 1

                sys.stdout.flush()

            print()
            log("=" * 60)
            log(f"Resumo: {success} email(s) encontrado(s), {fail} falha(s), {already} já coletado(s)")
            log(f"Arquivo salvo: {csv_path}")
            log("=" * 60)

        except KeyboardInterrupt:
            log("\nInterrompido pelo usuário.", "WARN")
        except Exception as e:
            log(f"Erro inesperado: {e}", "ERROR")
            import traceback
            traceback.print_exc()
        finally:
            browser.close()

    log("Finalizado.", "OK")


if __name__ == "__main__":
    main()
