"""
Script para testar a integração ponta a ponta do Passo 6.4:
Inicia o FastAPI (porta 8000), o Next.js (porta 3008) e usa Playwright
para disparar a busca intermodal e capturar o streaming dos cards QuoteCard e HackerFareCard.
"""
import os
import sys
import time
import subprocess
import urllib.request
from playwright.sync_api import sync_playwright

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(WORKSPACE_DIR, "frontend")
ARTIFACT_DIR = r"C:\Users\Gabriel\.gemini\antigravity\brain\706ec30a-403e-4b53-8431-b5e25272966c"
FASTAPI_PORT = 8000
FRONTEND_PORT = 3008

def wait_for_url(url, timeout=30):
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(1)
    return False

def main():
    fastapi_proc = None
    next_proc = None

    try:
        # 1. Inicia FastAPI se não estiver ativo
        if not wait_for_url(f"http://localhost:{FASTAPI_PORT}/health", timeout=2):
            print(f"Iniciando FastAPI na porta {FASTAPI_PORT}...")
            fastapi_proc = subprocess.Popen(
                [
                    os.path.join(WORKSPACE_DIR, ".venv", "Scripts", "python.exe"),
                    "-m", "uvicorn", "app.api.main:app",
                    "--host", "127.0.0.1",
                    "--port", str(FASTAPI_PORT),
                ],
                cwd=WORKSPACE_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if not wait_for_url(f"http://localhost:{FASTAPI_PORT}/health", timeout=15):
                print("Erro ao inicializar FastAPI.")
                sys.exit(1)
            print("FastAPI ativo e saudável!")
        else:
            print("FastAPI já está em execução.")

        # 2. Inicia Next.js na porta 3008
        print(f"Iniciando Next.js na porta {FRONTEND_PORT}...")
        next_proc = subprocess.Popen(
            f"npx.cmd next start -p {FRONTEND_PORT}",
            cwd=FRONTEND_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True
        )
        if not wait_for_url(f"http://localhost:{FRONTEND_PORT}", timeout=30):
            print("Erro ao inicializar Next.js.")
            sys.exit(1)
        print("Next.js ativo!")

        # 3. Playwright automação
        out_workspace = os.path.join(WORKSPACE_DIR, "frontend_step64_cards.png")
        out_artifact = os.path.join(ARTIFACT_DIR, "frontend_step64_cards.png")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1.5)
            
            url = f"http://localhost:{FRONTEND_PORT}"
            print(f"Navegando para {url}...")
            page.goto(url, wait_until="networkidle")
            page.wait_for_timeout(1000)

            # Clica no botão 'Comparar' para disparar busca via WebSocket
            print("Clicando no botão 'Comparar' para iniciar streaming intermodal...")
            search_button = page.locator("button[type='submit']")
            search_button.click()

            # Aguarda streaming de cards ou status bar
            print("Aguardando streaming de cotações...")
            page.wait_for_timeout(4500)

            # Rola suavemente para visualizar os cards de cotação
            page.evaluate("window.scrollTo({ top: 600, behavior: 'instant' })")
            page.wait_for_timeout(1000)

            # Captura screenshot da página com os cards
            print(f"Capturando screenshot em {out_workspace}...")
            page.screenshot(path=out_workspace, full_page=True)

            import shutil
            shutil.copyfile(out_workspace, out_artifact)
            print(f"Screenshot salvo em artefato: {out_artifact}")

            browser.close()
            print("Validação visual concluída!")

    finally:
        if next_proc:
            print("Encerrando Next.js...")
            subprocess.run(f"taskkill /F /T /PID {next_proc.pid}", shell=True, capture_output=True)
        if fastapi_proc:
            print("Encerrando FastAPI...")
            subprocess.run(f"taskkill /F /T /PID {fastapi_proc.pid}", shell=True, capture_output=True)

if __name__ == "__main__":
    main()
