"""Initialize two Oracle application schemas in parallel, without retaining ADMIN.

Run with --gui to enter existing credentials locally, or supply a JSON object on
stdin with --stdin-json. Passwords never appear in command-line arguments/logs.
Wallets must already have been downloaded by the account owner.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import getpass
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import zipfile

from dotenv import dotenv_values
from sqlalchemy import create_engine, inspect

from .config import PROJECTS
from .schema import metadata
from .service import Service
from .util import Missing


class SetupError(Exception):
    pass


def private_directory(path):
    path = Path(path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        result = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"],
                                capture_output=True, text=True, check=True)
        sid = next(csv.reader(io.StringIO(result.stdout)))[1]
        if not re.fullmatch(r"S-1-[0-9-]+", sid):
            raise SetupError("Unable to determine local user for private file permissions")
        result = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r",
                                 f"*{sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"],
                                capture_output=True)
        if result.returncode:
            raise SetupError("Unable to restrict private directory permissions")
    else:
        path.chmod(0o700)
    return path


def write_private_env(path, values):
    # A private parent protects the temporary file on Windows as well.
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    def quote(value):
        return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as output:
            for key, value in values.items():
                if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or "\n" in str(value) or "\r" in str(value):
                    raise SetupError("Invalid private configuration field")
                output.write(key + "=" + quote(value) + "\n")
            output.flush()
            os.fsync(output.fileno())
        if os.name != "nt":
            temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def extract_wallet(archive, private_root, project):
    archive = Path(archive)
    if not archive.is_file() or archive.stat().st_size > 8 * 1024**2:
        raise SetupError(f"{project}: wallet ZIP missing or too large")
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    target = private_directory(private_root / (project + "-wallet-" + sha[:16]))
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)) or len(names) > 30:
            raise SetupError("Invalid wallet ZIP members")
        if sum(entry.file_size for entry in entries) > 16 * 1024**2:
            raise SetupError("Wallet ZIP expands beyond limit")
        for entry in entries:
            if (not re.fullmatch(r"[A-Za-z0-9_.-]+", entry.filename)
                    or entry.filename in {".", ".."} or entry.is_dir()
                    or stat.S_ISLNK(entry.external_attr >> 16)):
                raise SetupError("Wallet ZIP contains unsafe member")
        if not {"tnsnames.ora", "ewallet.pem"}.issubset(names):
            raise SetupError("Wallet needs tnsnames.ora and ewallet.pem for the Thin driver")
        tns = bundle.read("tnsnames.ora").decode("utf-8-sig")
        if not re.search(r"(?im)^\s*" + project + r"_low\s*=", tns):
            raise SetupError(f"Wallet does not contain {project}_low; choose the matching instance wallet")
        hosts = re.findall(r"(?i)\(\s*host\s*=\s*([^\s)]+)", tns)
        if not hosts or any(not host.lower().endswith(".oraclecloud.com") for host in hosts):
            raise SetupError("Wallet connection host is not an Oracle Cloud endpoint")
        for entry in entries:
            destination = target / entry.filename
            if destination.is_symlink():
                raise SetupError("Wallet destination must not be a symlink")
            destination.write_bytes(bundle.read(entry))
            if os.name != "nt":
                destination.chmod(0o600)
    return target


def safe_error(error):
    if isinstance(error, SetupError):
        return str(error)
    original = getattr(error, "orig", error)
    value = original.args[0] if getattr(original, "args", ()) else None
    code = getattr(value, "full_code", None)
    if code and re.fullmatch(r"(?:ORA|DPY|DPI)-[0-9]+", code):
        return code
    # Never format a DB exception: failed DDL can include the application password.
    return type(original).__name__


def bootstrap_project(project, credentials, private_root):
    import oracledb
    wallet = extract_wallet(credentials["wallet_zip"], private_root, project)
    prefix = project.upper() + "_ORACLE_"
    config_path = private_root / (project + ".env")
    saved = dict(dotenv_values(config_path)) if config_path.exists() else {}
    app_user = project.upper() + "_APP"
    if saved and saved.get(prefix + "USER") != app_user:
        raise SetupError(f"{project}: existing configuration belongs to a different user")
    app_password = saved.get(prefix + "PASSWORD") or ("Rb9_" + secrets.token_hex(12))
    options = dict(dsn=project + "_low", config_dir=str(wallet), wallet_location=str(wallet),
                   wallet_password=credentials["wallet_password"], retry_count=1,
                   retry_delay=1, tcp_connect_timeout=15, ssl_server_dn_match=True)
    # ADMIN is retained only in memory for this connection and never persisted.
    with oracledb.connect(user="ADMIN", password=credentials["admin_password"], **options) as admin:
        admin.call_timeout = 30000
        with admin.cursor() as cursor:
            cursor.execute("SELECT username FROM all_users WHERE username = :name", name=app_user)
            exists = cursor.fetchone() is not None
            if exists and not saved.get(prefix + "PASSWORD"):
                raise SetupError(f"{project}: application user already exists without saved credentials; no password was changed")
            values = {prefix + "USER": app_user, prefix + "PASSWORD": app_password,
                      prefix + "DSN": project + "_low", prefix + "WALLET_DIR": wallet.as_posix(),
                      prefix + "WALLET_PASSWORD": credentials["wallet_password"],
                      project.upper() + "_API_TOKEN": saved.get(project.upper() + "_API_TOKEN") or secrets.token_urlsafe(48)}
            # Preserve generated credentials before CREATE USER, which commits implicitly.
            write_private_env(config_path, values)
            if not exists:
                cursor.execute(f'CREATE USER {app_user} IDENTIFIED BY "{app_password}"')
            cursor.execute(f"GRANT CREATE SESSION, CREATE TABLE TO {app_user}")
            cursor.execute(f"ALTER USER {app_user} QUOTA 15G ON DATA")
    def connect():
        connection = oracledb.connect(user=app_user, password=app_password, **options)
        connection.call_timeout = 30000
        return connection
    engine = create_engine("oracle+oracledb://", creator=connect, pool_size=1, max_overflow=0,
                           hide_parameters=True, pool_pre_ping=True)
    try:
        metadata.create_all(engine)
        service = Service(engine, None, project)
        payload = {"project": project, "schema": "ready", "tables": len(metadata.tables), "encoding_check": "연결 확인"}
        try:
            current = service.get_record("checkpoints", "backend-bootstrap")["version"]
        except Missing:
            current = 0
        service.put_record("checkpoints", "backend-bootstrap", payload, current, summary="Oracle backend schema ready")
        assert service.get_record("checkpoints", "backend-bootstrap")["payload"] == payload
        assert service.put_record("checkpoints", "backend-bootstrap", payload, current,
                                  summary="Oracle backend schema ready", source_uri="")["changed"] is False
        assert "backend-bootstrap" in [row["record_key"] for row in service.list_records("checkpoints")["items"]]
        tables = inspect(engine).get_table_names()
        if not set(metadata.tables).issubset(tables):
            raise SetupError("Schema verification did not find every required table")
        return {"project": project, "database": "oracle", "schema": "ready", "tables": len(metadata.tables),
                "record_roundtrip": "passed", "idempotence": "passed", "application_user": app_user}
    finally:
        engine.dispose()


def run(credentials, destination):
    private_root = private_directory(destination)
    outcomes = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(bootstrap_project, project, values, private_root): project
                   for project, values in credentials.items()}
        for future in as_completed(futures):
            project = futures[future]
            try:
                result = future.result()
            except Exception as error:
                result = {"project": project, "schema": "not_ready", "error": safe_error(error)}
            outcomes.append(result)
    ready = {item["project"] for item in outcomes if item["schema"] == "ready"}
    if ready == set(PROJECTS):
        combined = {"BACKEND_DATABASE": "oracle", "BACKEND_BLOB_STORE": "local",
                    "BACKEND_DATA_DIR": (private_root / "runtime").as_posix(), "ORACLE_POOL_SIZE": "3"}
        if (private_root / "oracle.env").is_file():
            combined.update(dotenv_values(private_root / "oracle.env"))
        combined["BACKEND_DATABASE"] = "oracle"
        for project in PROJECTS:
            combined.update(dotenv_values(private_root / (project + ".env")))
        write_private_env(private_root / "oracle.env", combined)
    (private_root / "connection-status.json").write_text(json.dumps(outcomes, ensure_ascii=False, indent=2), encoding="utf-8")
    return outcomes


def gui(destination, downloads):
    import tkinter as tk
    from tkinter import filedialog, ttk
    from threading import Thread
    window = tk.Tk()
    window.title("Oracle 두 DB 연결 — 비밀번호는 이 PC에서만 입력")
    window.geometry("790x580")
    ttk.Label(window, text="다운로드한 Wallet과 기존 ADMIN 비밀번호로 앱 전용 계정·테이블을 생성합니다.").pack(pady=12)
    ttk.Label(window, text="ADMIN 비밀번호는 저장하지 않습니다. 앱 접속정보와 Wallet은 비공개 폴더에 보관합니다.").pack()
    fields = {}
    for project in PROJECTS:
        box = ttk.LabelFrame(window, text=project.upper(), padding=12)
        box.pack(fill="x", padx=18, pady=8)
        fields[project] = {}
        for index, (name, label) in enumerate((("wallet_zip", "Wallet ZIP"), ("admin_password", "기존 DB ADMIN 비밀번호"),
                                                ("wallet_password", "다운로드할 때 정한 Wallet 비밀번호"))):
            ttk.Label(box, text=label).grid(row=index, column=0, sticky="w", pady=5)
            value = tk.StringVar()
            fields[project][name] = value
            entry = ttk.Entry(box, textvariable=value, width=55, show="*" if "password" in name else "")
            entry.grid(row=index, column=1, padx=10, pady=5)
            if name == "wallet_zip":
                candidates = sorted(downloads.glob("Wallet_" + project.upper() + "*.zip"), key=lambda path: path.stat().st_mtime, reverse=True)
                if candidates:
                    value.set(str(candidates[0]))
                ttk.Button(box, text="선택", command=lambda v=value: v.set(filedialog.askopenfilename(
                    initialdir=downloads, filetypes=[("Oracle Wallet ZIP", "*.zip")]))).grid(row=index, column=2)
    status = tk.StringVar(value="두 DB 연결은 병렬로 진행합니다. 오류가 나도 비밀번호를 표시하지 않습니다.")
    ttk.Label(window, textvariable=status, wraplength=750).pack(pady=8)
    def submit():
        credentials = {project: {key: value.get() for key, value in values.items()} for project, values in fields.items()}
        if any(not value for values in credentials.values() for value in values.values()):
            status.set("두 DB의 Wallet ZIP과 기존 비밀번호를 모두 입력해 주세요.")
            return
        button.configure(state="disabled")
        status.set("두 Oracle DB에 연결하고 앱 계정·테이블·한글 저장/조회를 검증하고 있습니다…")
        def worker():
            try:
                result = run(credentials, destination)
                message = " / ".join(item["project"].upper() + ": " + ("연결 완료" if item["schema"] == "ready" else item["error"]) for item in result)
            except Exception as error:
                message = safe_error(error)
            finally:
                credentials.clear()
            window.after(0, lambda: finish(message))
        Thread(target=worker, daemon=True).start()
    def finish(message):
        status.set(message)
        button.configure(state="normal")
        for values in fields.values():
            for key in ("admin_password", "wallet_password"):
                values[key].set("")
    button = ttk.Button(window, text="두 DB 연결 및 앱 스키마 생성", command=submit)
    button.pack(pady=8)
    window.mainloop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-dir", type=Path, required=True)
    parser.add_argument("--downloads", type=Path, default=Path.home() / "Downloads")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--gui", action="store_true")
    mode.add_argument("--stdin-json", action="store_true")
    args = parser.parse_args()
    if args.gui:
        gui(args.private_dir, args.downloads)
        return
    if args.stdin_json:
        credentials = json.loads(sys.stdin.readline())
    else:
        credentials = {}
        for project in PROJECTS:
            credentials[project] = {"wallet_zip": input(project.upper() + " Wallet ZIP path: "),
                                    "admin_password": getpass.getpass(project.upper() + " existing ADMIN password: "),
                                    "wallet_password": getpass.getpass(project.upper() + " existing Wallet password: ")}
    if not credentials or set(credentials) - set(PROJECTS):
        parser.error("Choose estate and/or investment")
    try:
        outcomes = run(credentials, args.private_dir)
        print(json.dumps(outcomes, ensure_ascii=False))
        if any(item["schema"] != "ready" for item in outcomes):
            sys.exit(1)
    except Exception as error:
        print(json.dumps({"error": safe_error(error)}))
        sys.exit(1)
    finally:
        credentials.clear()


if __name__ == "__main__":
    main()
