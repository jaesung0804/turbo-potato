# Web Run - official entry point:
#   cd C:\code
#   python run_real_estate_dashboard.py
# This rebuilds the dashboard from data\capital_area_apt_trade_transactions.csv,
# trains web\data\house_match_recommendations.json, serves C:\code\web, and
# opens http://127.0.0.1:8000 in your browser.
#
# Fast web-only run after data and AI recommendations are already built:
#   python run_real_estate_dashboard.py --skip-build --skip-ai
#
# Rebuild data but skip AI training:
#   python run_real_estate_dashboard.py --skip-ai
#
# If port 8000 is already in use, this script stops with an error instead of
# opening another port, so you do not accidentally see an old dashboard.

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


# 이 파일의 의도:
# - 사용자가 헷갈리지 않도록 로컬 웹 실행의 공식 진입점을 하나로 고정합니다.
# - 기본 실행 시 데이터 요약 JSON과 AI 추천 JSON을 새로 만든 뒤 웹 서버를 엽니다.
# - 이미 데이터가 만들어진 경우 --skip-build --skip-ai로 빠르게 화면만 확인할 수 있습니다.
# - 포트가 이미 사용 중이면 오래된 화면을 보는 실수를 막기 위해 실행을 중단합니다.
ROOT_DIR = Path(__file__).resolve().parent
WEB_DIR = ROOT_DIR / "web"
BUILD_SCRIPT = ROOT_DIR / "build_real_estate_dashboard_data.py"
TRAIN_SCRIPT = ROOT_DIR / "train_house_match_model.py"
DEFAULT_INPUT = ROOT_DIR / "data" / "capital_area_apt_trade_transactions.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and run the real estate dashboard.")
    parser.add_argument("--port", type=int, default=8000, help="Local web server port. Default: 8000.")
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT),
        help="CSV path to convert into the dashboard JSON.",
    )
    parser.add_argument("--skip-build", action="store_true", help="Skip dashboard data rebuild.")
    parser.add_argument("--skip-ai", action="store_true", help="Skip AI recommendation JSON rebuild.")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically.")
    return parser.parse_args()


def ensure_port_available(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(
                f"Port {port} is already in use. Stop the old server and run again.\n"
                f"PowerShell helper commands:\n"
                f"  Get-NetTCPConnection -LocalPort {port}\n"
                f"  Stop-Process -Id <OwningProcess>"
            )


def build_dashboard_data(input_path: str | None) -> None:
    if not BUILD_SCRIPT.exists():
        raise FileNotFoundError(f"Cannot find dashboard build script: {BUILD_SCRIPT}")

    print("Building dashboard JSON...")
    command = [sys.executable, str(BUILD_SCRIPT)]
    if input_path:
        command.extend(["--input", input_path])
    subprocess.run(command, cwd=ROOT_DIR, check=True)
    print("Dashboard JSON build complete.")


def train_ai_recommendations() -> None:
    if not TRAIN_SCRIPT.exists():
        raise FileNotFoundError(f"Cannot find AI recommendation script: {TRAIN_SCRIPT}")

    print("Training AI recommendation JSON...")
    subprocess.run([sys.executable, str(TRAIN_SCRIPT)], cwd=ROOT_DIR, check=True)
    print("AI recommendation JSON build complete.")


def serve_web(port: int, open_browser: bool) -> None:
    if not WEB_DIR.exists():
        raise FileNotFoundError(f"Cannot find web directory: {WEB_DIR}")

    handler = lambda *args, **kwargs: SimpleHTTPRequestHandler(*args, directory=WEB_DIR, **kwargs)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{port}"

    print(f"Dashboard running at {url}")
    print(f"Serving directory: {WEB_DIR}")
    print("Press Ctrl+C to stop.")

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)

    if open_browser:
        webbrowser.open(url)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping web server.")
        server.shutdown()
        server.server_close()


def main() -> None:
    args = parse_args()
    ensure_port_available(args.port)

    if not args.skip_build:
        build_dashboard_data(args.input)

    if not args.skip_ai:
        train_ai_recommendations()

    serve_web(args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
