"""
launcher.py
Inicializador do Painel de Jurimetria PGE.
Empacotado com PyInstaller — não executar diretamente.
"""
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path


PORT = 8501


def _abrir_browser() -> None:
    """Aguarda o servidor subir e abre o navegador automaticamente."""
    time.sleep(4)
    webbrowser.open(f"http://localhost:{PORT}")


def main() -> None:
    # Quando empacotado pelo PyInstaller, os arquivos ficam junto ao .exe
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).parent

    dashboard = base / "dashboard_jurimetria.py"

    if not dashboard.exists():
        print(f"[ERRO] Arquivo não encontrado: {dashboard}")
        input("Pressione ENTER para sair...")
        sys.exit(1)

    # Abre o navegador em paralelo
    threading.Thread(target=_abrir_browser, daemon=True).start()

    print("Iniciando Painel de Jurimetria PGE...")
    print(f"Acesse: http://localhost:{PORT}")
    print("Para encerrar: feche esta janela.\n")

    subprocess.run(
        [
            sys.executable, "-m", "streamlit", "run",
            str(dashboard),
            f"--server.port={PORT}",
            "--server.headless=true",
            "--browser.gatherUsageStats=false",
        ],
        check=False,
    )


if __name__ == "__main__":
    main()
