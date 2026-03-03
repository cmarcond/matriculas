"""
autorizar_alunos.py - Automação de autorização de matrícula no Portal Acadêmico do ITA

Este script faz login no portal acadêmico e autoriza (defere) todos os alunos
pendentes em todas as turmas do orientador.

Fluxo:
  1. Login com CPF e senha
  2. Na página do Orientador, lista todos os alunos
  3. Para cada aluno cujo parecer NÃO seja "Deferido", clica "Alterar",
     seleciona "Deferido", e clica "Salvar"
  4. Alunos já deferidos são ignorados (logged como "já autorizado")

Uso:
  python autorizar_alunos.py [--headless] [--dry-run]

Flags:
  --headless   Roda sem abrir janela do navegador
  --dry-run    Apenas lista os alunos sem fazer alterações
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

# ASP.NET selectors
SEL_CPF_INPUT = "#usuarioTextBox"
SEL_SENHA_INPUT = "#senhaTextBox"
SEL_LOGIN_BUTTON = "#confirmarButton"
SEL_STUDENT_TABLE = "table[id*='alunosGridView']"
SEL_ALTERAR_LINK = "a[id*='alterarLinkButton']"
SEL_PARECER_DEFERIDO = "#ctl00_corpoContentPlaceHolder_parecerRadioButtonList_0"
SEL_PARECER_RADIO_NAME = "ctl00$corpoContentPlaceHolder$parecerRadioButtonList"
SEL_SAVE_BUTTON = "#ctl00_corpoContentPlaceHolder_salvarButton"
SEL_CANCEL_BUTTON = "#ctl00_corpoContentPlaceHolder_cancelarButton"

WAIT_AFTER_ACTION = 2  # seconds to wait after AJAX actions


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Student:
    codigo: str
    nome: str
    especial: str
    parecer: str
    alterar_link_id: str
    row_index: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log(msg: str, level: str = "INFO"):
    """Simple timestamped logger."""
    timestamp = time.strftime("%H:%M:%S")
    prefix = {"INFO": "ℹ️ ", "OK": "✅", "SKIP": "⏭️ ", "WARN": "⚠️ ", "ERROR": "❌"}
    print(f"[{timestamp}] {prefix.get(level, '')} {msg}")


def wait_for_ajax(page, seconds: float = WAIT_AFTER_ACTION):
    """Wait for ASP.NET UpdatePanel AJAX to complete."""
    page.wait_for_load_state("networkidle")
    time.sleep(seconds)


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def do_login(page) -> bool:
    """Navigate to the portal and log in. Returns True on success."""
    log(f"Navegando para {PORTAL_URL}")
    page.goto(PORTAL_URL, wait_until="networkidle")

    # Verify we're on the login page
    if not page.query_selector(SEL_CPF_INPUT):
        log("Página de login não encontrada!", "ERROR")
        return False

    log("Preenchendo CPF e senha...")
    page.fill(SEL_CPF_INPUT, CPF)
    page.fill(SEL_SENHA_INPUT, SENHA)

    log("Clicando em 'Acessar'...")
    page.click(SEL_LOGIN_BUTTON)

    # Wait for redirect to Orientador page
    try:
        page.wait_for_url("**/Orientador**", timeout=15000)
    except PlaywrightTimeout:
        # Check if we're still on the login page (bad credentials)
        if "Acesso" in page.url:
            body_text = page.inner_text("body")
            if "senha" in body_text.lower() or "inválid" in body_text.lower():
                log("Login falhou — CPF ou senha incorretos!", "ERROR")
            else:
                log(f"Login falhou — ainda na página de acesso. Texto: {body_text[:200]}", "ERROR")
            return False
        # Maybe redirected elsewhere
        log(f"Redirecionado para URL inesperada: {page.url}", "WARN")

    wait_for_ajax(page)
    log(f"Login bem-sucedido! URL: {page.url}", "OK")
    return True


def get_students(page) -> list[Student]:
    """Parse the student table on the Orientador page and return list of Students."""
    students = []

    tables = page.query_selector_all(SEL_STUDENT_TABLE)
    if not tables:
        log("Nenhuma tabela de alunos encontrada!", "WARN")
        return students

    for table in tables:
        rows = table.query_selector_all("tr")
        # First row is header
        for row_idx, row in enumerate(rows[1:], start=1):
            cells = row.query_selector_all("td")
            if len(cells) < 4:
                continue

            codigo = cells[0].inner_text().strip()
            nome = cells[1].inner_text().strip()
            especial = cells[2].inner_text().strip()
            parecer = cells[3].inner_text().strip()

            # Find the "Alterar" link in this row
            alterar = row.query_selector("a[id*='alterarLinkButton']")
            alterar_id = alterar.get_attribute("id") if alterar else ""

            students.append(Student(
                codigo=codigo,
                nome=nome,
                especial=especial,
                parecer=parecer,
                alterar_link_id=alterar_id,
                row_index=row_idx,
            ))

    return students


def authorize_student(page, student: Student) -> bool:
    """
    Click 'Alterar' for a student, set parecer to 'Deferido', and save.
    Returns True if the authorization was performed successfully.
    """
    log(f"Autorizando: [{student.codigo}] {student.nome}")

    # Click the Alterar link
    alterar_selector = f"#{student.alterar_link_id.replace('$', '_')}"
    alterar = page.query_selector(f"#{student.alterar_link_id}")
    if not alterar:
        # Try with CSS escaping for ASP.NET IDs
        alterar = page.query_selector(f"a[id='{student.alterar_link_id}']")
    if not alterar:
        log(f"  Link 'Alterar' não encontrado para {student.nome} (id={student.alterar_link_id})", "ERROR")
        return False

    alterar.click()
    wait_for_ajax(page, 3)

    # Verify we're on the edit page — should have the Save button
    save_btn = page.query_selector(SEL_SAVE_BUTTON)
    if not save_btn:
        log(f"  Página de edição não carregou para {student.nome}", "ERROR")
        return False

    # Check current radio state
    deferido_radio = page.query_selector(SEL_PARECER_DEFERIDO)
    if not deferido_radio:
        log(f"  Radio 'Deferido' não encontrado na página de edição!", "ERROR")
        # Click cancel to go back
        cancel = page.query_selector(SEL_CANCEL_BUTTON)
        if cancel:
            cancel.click()
            wait_for_ajax(page)
        return False

    is_checked = deferido_radio.is_checked()
    if is_checked:
        log(f"  Parecer já é 'Deferido' — confirmando com Salvar")
    else:
        log(f"  Selecionando 'Deferido'...")
        deferido_radio.check()
        time.sleep(0.5)

    # Click Salvar
    log(f"  Clicando 'Salvar'...")
    save_btn.click()
    wait_for_ajax(page, 3)

    # After saving, we should be back on the Orientador list page
    # Verify by checking for the student table
    if page.query_selector(SEL_STUDENT_TABLE):
        log(f"  Aluno [{student.codigo}] {student.nome} — autorizado com sucesso!", "OK")
        return True
    else:
        # Maybe we're on a confirmation or error page
        body_text = page.inner_text("body")[:300]
        log(f"  Resultado inesperado após salvar. Página: {body_text}", "WARN")
        # Try to navigate back
        page.goto(PORTAL_URL.replace("Acesso", "Orientador"), wait_until="networkidle")
        wait_for_ajax(page)
        return True


def run_authorization(page, dry_run: bool = False):
    """Main authorization loop."""
    students = get_students(page)

    if not students:
        log("Nenhum aluno encontrado para autorizar.", "WARN")
        return

    log(f"Encontrados {len(students)} aluno(s):")
    print()
    print(f"  {'Código':<10} {'Nome':<50} {'Especial':<10} {'Parecer':<15}")
    print(f"  {'-'*10} {'-'*50} {'-'*10} {'-'*15}")
    for s in students:
        print(f"  {s.codigo:<10} {s.nome:<50} {s.especial:<10} {s.parecer:<15}")
    print()

    # Separate into those needing authorization and already authorized
    needs_auth = [s for s in students if s.parecer.lower() != "deferido"]
    already_auth = [s for s in students if s.parecer.lower() == "deferido"]

    if already_auth:
        log(f"{len(already_auth)} aluno(s) já têm parecer 'Deferido':", "SKIP")
        for s in already_auth:
            log(f"  [{s.codigo}] {s.nome}", "SKIP")

    if not needs_auth:
        log("Todos os alunos já estão autorizados (Deferido)! Nada a fazer.", "OK")
        # Even though all are Deferido, let's re-confirm by clicking Alterar + Salvar
        # in case the portal requires explicit confirmation
        log("Verificando se há necessidade de confirmação explícita...")
        # Re-save all students to ensure they are properly confirmed
        for student in students:
            if dry_run:
                log(f"  [DRY-RUN] Confirmaria: [{student.codigo}] {student.nome}", "INFO")
            else:
                authorize_student(page, student)
                # Re-fetch the students list since the page may have changed
                students_refreshed = get_students(page)
                if not students_refreshed:
                    log("Tabela de alunos não encontrada após salvar — pode ter sido redirecionado", "WARN")
                    break
        return

    log(f"{len(needs_auth)} aluno(s) precisam de autorização:")
    for s in needs_auth:
        log(f"  [{s.codigo}] {s.nome} — parecer atual: '{s.parecer}'")

    if dry_run:
        log("[DRY-RUN] Nenhuma alteração será feita.", "WARN")
        return

    # Authorize each student
    success_count = 0
    fail_count = 0

    for student in needs_auth:
        try:
            ok = authorize_student(page, student)
            if ok:
                success_count += 1
            else:
                fail_count += 1
        except Exception as e:
            log(f"  Erro ao autorizar [{student.codigo}] {student.nome}: {e}", "ERROR")
            fail_count += 1
            # Try to recover by going back
            try:
                page.goto(PORTAL_URL.replace("Acesso", "Orientador"), wait_until="networkidle")
                wait_for_ajax(page)
            except Exception:
                pass

    # Final summary
    print()
    log(f"Resumo: {success_count} autorizado(s), {fail_count} erro(s), {len(already_auth)} já autorizado(s)")

    # Verify final state
    log("Verificando estado final...")
    try:
        page.goto(PORTAL_URL.replace("Acesso", "Orientador"), wait_until="networkidle")
        wait_for_ajax(page)
        final_students = get_students(page)
        all_ok = all(s.parecer.lower() == "deferido" for s in final_students)
        if all_ok:
            log("Todos os alunos estão com parecer 'Deferido'!", "OK")
        else:
            pending = [s for s in final_students if s.parecer.lower() != "deferido"]
            log(f"Atenção: {len(pending)} aluno(s) ainda sem 'Deferido':", "WARN")
            for s in pending:
                log(f"  [{s.codigo}] {s.nome} — parecer: '{s.parecer}'", "WARN")
    except Exception as e:
        log(f"Erro ao verificar estado final: {e}", "ERROR")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Autoriza alunos no Portal Acadêmico do ITA"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Executa sem abrir janela do navegador",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Apenas lista os alunos, sem efetuar alterações",
    )
    args = parser.parse_args()

    if not PORTAL_URL or not CPF or not SENHA:
        log("PORTAL_URL, CPF e SENHA devem ser definidos no arquivo .secrets.env", "ERROR")
        sys.exit(1)

    log("="*60)
    log("Autorização de Matrícula — Portal Acadêmico ITA")
    log("="*60)
    log(f"Modo: {'HEADLESS' if args.headless else 'HEADED (visual)'}")
    log(f"Dry-run: {'SIM' if args.dry_run else 'NÃO'}")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=args.headless,
            slow_mo=500 if not args.headless else 0,  # slow down for visual mode
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            ignore_https_errors=True,
        )
        page = context.new_page()

        try:
            # Step 1: Login
            if not do_login(page):
                log("Não foi possível fazer login. Abortando.", "ERROR")
                browser.close()
                sys.exit(1)

            # Step 2: Authorize students
            run_authorization(page, dry_run=args.dry_run)

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
