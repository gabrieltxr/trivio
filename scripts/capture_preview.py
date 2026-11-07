"""
Script para inicializar o Next.js e capturar um screenshot estático de alta fidelidade
do frontend white clean usando Playwright Chromium com nome de arquivo v2 (sem cache).
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
PORT = 3007
URL = f"http://localhost:{PORT}"

def wait_for_server(url, timeout=30):
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
    print(f"Iniciando Next.js em porta dedicada {PORT}...")
    server_process = subprocess.Popen(
        f"npx.cmd next start -p {PORT}",
        cwd=FRONTEND_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=True
    )
    
    print(f"Aguardando servidor inicializar em {URL}...")
    if not wait_for_server(URL, timeout=35):
        print("Erro: Servidor não respondeu a tempo.")
        server_process.kill()
        sys.exit(1)
    print("Servidor Next.js pronto e respondendo!")

    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    out_workspace = os.path.join(WORKSPACE_DIR, "frontend_white_clean_preview_v2.png")
    out_artifact = os.path.join(ARTIFACT_DIR, "frontend_white_clean_preview_v2.png")

    try:
        print("Iniciando Playwright Chromium headless...")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1.5)
            
            print(f"Navegando para {URL}...")
            page.goto(URL, wait_until="networkidle")
            page.wait_for_timeout(2000)
            
            print(f"Salvando screenshot em {out_workspace}...")
            page.screenshot(path=out_workspace, full_page=True)
            
            import shutil
            shutil.copyfile(out_workspace, out_artifact)
            print(f"Screenshot salvo no diretório de artefatos: {out_artifact}")
            
            browser.close()
            print("Captura concluída com sucesso!")
    finally:
        print("Encerrando processo do servidor Next.js...")
        try:
            subprocess.run(f"taskkill /F /T /PID {server_process.pid}", shell=True, capture_output=True)
        except Exception as e:
            print("Erro ao matar processo:", e)
        print("Servidor finalizado.")

if __name__ == "__main__":
    main()
