"""
autorizar_professor.py - Autoriza alunos na aba Professor do Portal Acadêmico do ITA

Uso:
  python autorizar_professor.py [--headless] [--dry-run]
"""

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

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
SEL_PARECER_DEFERIDO = "#ctl00_corpoContentPlaceHolder_parecerRadioButtonList_0"
SEL_SAVE_BUTTON = "#ctl00_corpoContentPlaceHolder_salvarButton"
SEL_CANCEL_BUTTON = "#ctl00_corpoContentPlaceHolder_cancelarButton"

WAIT = 2
MAX_RETRIES = 3


@dataclass
class Student:
    codigo: str
    nome: str
    especial: str
    parecer: str
    alterar_link_id: str


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
        # Check if we already have the student tables
        if page.query_selector(SEL_STUDENT_TABLE):
            # Verify we're on Professor tab (not Orientador)
            prof_li = page.query_selector("#ctl00_ctl00_menuContentPlaceHolder_liProfessorGraduacao")
            if prof_li and "selecionado" in (prof_li.get_attribute("class") or ""):
                return True

        log(f"  Recuperando sessão (tentativa {attempt + 1}/{MAX_RETRIES})...", "WARN")

        # Try clicking Professor link first
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


def get_students(page):
    students = []
    tables = page.query_selector_all(SEL_STUDENT_TABLE)
    if not tables:
        return students
    for table in tables:
        rows = table.query_selector_all("tr")
        for row in rows[1:]:
            cells = row.query_selector_all("td")
            if len(cells) < 4:
                continue
            alterar = row.query_selector("a[id*='alterarLinkButton']")
            students.append(Student(
                codigo=cells[0].inner_text().strip(),
                nome=cells[1].inner_text().strip(),
                especial=cells[2].inner_text().strip(),
                parecer=cells[3].inner_text().strip(),
                alterar_link_id=alterar.get_attribute("id") if alterar else "",
            ))
    return students


def authorize_one(page, student):
    """Authorize a single student. Returns True on success."""
    log(f"Autorizando: [{student.codigo}] {student.nome}")

    alterar = page.query_selector(f"#{student.alterar_link_id}")
    if not alterar:
        alterar = page.query_selector(f"a[id='{student.alterar_link_id}']")
    if not alterar:
        log(f"  Link 'Alterar' não encontrado", "ERROR")
        return False

    alterar.click()
    safe_wait(page, 3)

    save_btn = page.query_selector(SEL_SAVE_BUTTON)
    if not save_btn:
        log(f"  Página de edição não carregou", "ERROR")
        return False

    deferido_radio = page.query_selector(SEL_PARECER_DEFERIDO)
    if not deferido_radio:
        log(f"  Radio 'Deferido' não encontrado!", "ERROR")
        cancel = page.query_selector(SEL_CANCEL_BUTTON)
        if cancel:
            cancel.click()
            safe_wait(page)
        return False

    if not deferido_radio.is_checked():
        deferido_radio.check()
        time.sleep(0.5)

    save_btn.click()
    safe_wait(page, 3)

    log(f"  [{student.codigo}] {student.nome} — salvo!", "OK")
    sys.stdout.flush()
    return True


def main():
    parser = argparse.ArgumentParser(description="Autoriza alunos na aba Professor")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not PORTAL_URL or not CPF or not SENHA:
        log("PORTAL_URL, CPF e SENHA devem ser definidos no .secrets.env", "ERROR")
        sys.exit(1)

    log("=" * 60)
    log("Autorização de Matrícula — Aba Professor")
    log("=" * 60)

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
            # Initial login + navigate to Professor
            log(f"Navegando para {PORTAL_URL}")
            page.goto(PORTAL_URL, wait_until="networkidle")
            page.fill(SEL_CPF_INPUT, CPF)
            page.fill(SEL_SENHA_INPUT, SENHA)
            page.click(SEL_LOGIN_BUTTON)
            page.wait_for_url("**/Orientador**", timeout=15000)
            safe_wait(page)
            log("Login OK!", "OK")

            if not ensure_professor_tab(page):
                sys.exit(1)

            # Initial scan
            students = get_students(page)
            needs_auth = [s for s in students if s.parecer.lower() != "deferido"]
            already_auth = len(students) - len(needs_auth)

            log(f"Total: {len(students)} aluno(s) — {len(needs_auth)} pendente(s), {already_auth} já deferido(s)")

            if not needs_auth:
                log("Todos já autorizados!", "OK")
                browser.close()
                return

            if args.dry_run:
                for s in needs_auth:
                    print(f"  [{s.codigo}] {s.nome}")
                log(f"[DRY-RUN] {len(needs_auth)} aluno(s) seriam autorizados.", "WARN")
                browser.close()
                return

            total_pending = len(needs_auth)
            success = 0
            fail = 0
            done = 0

            while True:
                # Always ensure we're on Professor tab before scanning
                if not ensure_professor_tab(page):
                    log("Não foi possível voltar à aba Professor. Abortando.", "ERROR")
                    break

                current = get_students(page)
                pending_now = [s for s in current if s.parecer.lower() != "deferido"]
                if not pending_now:
                    break

                student = pending_now[0]
                done += 1
                pct = int(done / total_pending * 100)
                bar = "█" * (pct // 2) + "░" * (50 - pct // 2)
                print(f"\r  [{bar}] {pct}% ({done}/{total_pending})", end="", flush=True)
                print()
                log(f"[{done}/{total_pending}]")

                try:
                    if authorize_one(page, student):
                        success += 1
                    else:
                        fail += 1
                except Exception as e:
                    log(f"  Erro: {e}", "ERROR")
                    fail += 1

            print()
            log(f"Resumo: {success} autorizado(s), {fail} erro(s), {already_auth} já deferido(s) inicialmente")

            # Final verification
            log("Verificando estado final...")
            if ensure_professor_tab(page):
                final = get_students(page)
                pending = [s for s in final if s.parecer.lower() != "deferido"]
                if not pending:
                    log(f"Todos os {len(final)} alunos estão 'Deferido'!", "OK")
                else:
                    log(f"{len(pending)} aluno(s) ainda pendente(s):", "WARN")
                    for s in pending:
                        log(f"  [{s.codigo}] {s.nome} — {s.parecer}", "WARN")

        except KeyboardInterrupt:
            log("Interrompido pelo usuário.", "WARN")
        except Exception as e:
            log(f"Erro inesperado: {e}", "ERROR")
            import traceback
            traceback.print_exc()
        finally:
            browser.close()

    log("Finalizado.", "OK")


if __name__ == "__main__":
    main()
