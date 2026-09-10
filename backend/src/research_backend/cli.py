from __future__ import annotations

import argparse
import json
import os
import secrets
from pathlib import Path
from sqlalchemy import select
from . import schema as s
from .config import PROJECTS, Settings
from .database import Databases
from .objects import LocalObjects, OCIObjects
from .service import Service
from .util import Missing, digest, json_text
from .client import REFERENCE_KINDS, split_investment


def context(projects=None, *, needs_objects=True):
    config = Settings.from_env()
    databases = Databases(config, projects=projects)
    try:
        objects = None
        if needs_objects:
            objects = LocalObjects(config.data_dir / "objects") if config.blob_store == "local" else OCIObjects()
    except Exception:
        databases.close()
        raise
    return config, databases, objects


def migrate_investment(service, root):
    """Known source mapping; no chat messages, private notes or arbitrary recursive upload."""
    for filename, kind in REFERENCE_KINDS.items():
        path = root / "data/reference" / filename
        if not path.exists():
            continue
        body = json.loads(path.read_text(encoding="utf-8-sig"))
        # A byte-exact original is preserved independently of queryable records.
        with path.open("rb") as source:
            import hashlib
            sha = hashlib.file_digest(source, "sha256").hexdigest()
        service.register_file(sha, path)
        for key, payload, summary in split_investment(filename, body):
            try:
                current = service.get_record(kind, key)
                version = current["version"]
            except Missing:
                version = 0
            result = service.put_record(kind, key, payload, version, summary=summary[:500],
                source_uri=f"github:jaesung0804/st_dashboard/data/reference/{filename}#{sha}")
            yield {"file": filename, **result}
        relative_path = "data/reference/" + filename
        doc_key = digest(relative_path.encode())
        try:
            version = service.get_record("documents", doc_key)["version"]
        except Missing:
            version = 0
        service.put_record("documents", doc_key, {"artifact_sha256": sha, "format": "json", "relative_path": relative_path}, version,
                           summary=relative_path)


def main():
    p = argparse.ArgumentParser(description="Project research backend: explicit init/import, read-only HTTP queries")
    p.add_argument("--env-file", type=Path)
    sub = p.add_subparsers(dest="command", required=True)
    for command in ("init-db", "check"):
        database_command = sub.add_parser(command)
        database_command.add_argument("--project", choices=PROJECTS,
                                      help="Operate on only this project (default: both)")
    dev = sub.add_parser("init-local")
    dev.add_argument("--output", type=Path, default=Path(".env"))
    dev.add_argument("--data-dir", type=Path, default=Path("runtime"))
    ddl = sub.add_parser("export-oracle-ddl")
    ddl.add_argument("--output", type=Path, required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    ingest = sub.add_parser("import-data")
    ingest.add_argument("--project", choices=PROJECTS, required=True)
    ingest.add_argument("--dataset", required=True)
    ingest.add_argument("--path", type=Path, required=True)
    ingest.add_argument("--format", choices=["csv", "jsonl", "json"], default="csv")
    ingest.add_argument("--prefix", default="item")
    ingest.add_argument("--encoding", default="utf-8-sig")
    ingest.add_argument("--monthly", action="store_true")
    ingest.add_argument("--allow-empty", action="store_true")
    migrate = sub.add_parser("migrate-investment")
    migrate.add_argument("--root", type=Path, required=True)
    export = sub.add_parser("export-records")
    export.add_argument("--project", choices=PROJECTS, required=True)
    export.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.env_file:
        from dotenv import load_dotenv
        if not args.env_file.is_file():
            p.error("Environment file does not exist")
        load_dotenv(args.env_file, override=False)
    if args.command == "init-local":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as f:
            f.write(f"BACKEND_DATABASE=sqlite\nBACKEND_DATA_DIR={args.data_dir.resolve().as_posix()}\nBACKEND_BLOB_STORE=local\n")
            for project in PROJECTS:
                f.write(f"{project.upper()}_API_TOKEN={secrets.token_urlsafe(48)}\n")
        if os.name != "nt":
            args.output.chmod(0o600)
        print("Local configuration created; tokens were not printed. Keep this file private.")
        return
    if args.command == "export-oracle-ddl":
        from sqlalchemy.dialects.oracle import dialect
        from sqlalchemy.schema import CreateTable, CreateIndex
        args.output.parent.mkdir(parents=True, exist_ok=True)
        lines = ["-- Generated for Oracle 19c+. Run as the project application schema, once per DB."]
        for table in s.metadata.sorted_tables:
            lines.append(str(CreateTable(table).compile(dialect=dialect())) + ";")
            for index in table.indexes:
                lines.append(str(CreateIndex(index).compile(dialect=dialect())) + ";")
        args.output.write_text("\n\n".join(lines), encoding="utf-8")
        print("Oracle DDL exported")
        return
    if args.command == "serve":
        from .api import create_app
        import uvicorn
        uvicorn.run(create_app(), host=args.host, port=args.port, access_log=False)
        return
    project = "investment" if args.command == "migrate-investment" else getattr(args, "project", None)
    projects = (project,) if project else PROJECTS
    config, databases, objects = context(projects, needs_objects=args.command in {"migrate-investment", "import-data"})
    try:
        if args.command == "init-db":
            databases.initialize()
            print("Project schemas initialized: " + ", ".join(projects))
        elif args.command == "check":
            for project in projects:
                with databases.engine(project).connect() as db:
                    db.execute(select(s.datasets.c.dataset_key).limit(1))
                print(json.dumps({"project": project, "database": config.database, "schema": "ready", "blob_store": config.blob_store}))
        elif args.command == "migrate-investment":
            service = Service(databases.engine("investment"), objects, "investment")
            for item in migrate_investment(service, args.root.resolve()):
                print(json.dumps(item, ensure_ascii=False))
        elif args.command == "import-data":
            from .ingest import import_rows, import_monthly_csv
            service = Service(databases.engine(args.project), objects, args.project)
            if args.monthly:
                if args.format != "csv":
                    p.error("--monthly currently requires --format csv")
                for result in import_monthly_csv(service, args.dataset, args.path, args.encoding):
                    print(json.dumps(result))
            else:
                print(json.dumps(import_rows(service, args.dataset, args.path, args.format,
                    args.prefix, args.encoding, args.allow_empty)))
        elif args.command == "export-records":
            args.output.parent.mkdir(parents=True, exist_ok=True)
            # Writes a standalone backup; it never overwrites an existing backup.
            with args.output.open("x", encoding="utf-8") as out, databases.engine(args.project).connect() as db:
                for table in (s.records, s.revisions, s.heads, s.snapshots, s.snapshot_files,
                              s.files, s.datasets, s.dataset_versions, s.jobs):
                    for row in db.execute(select(table)).mappings().yield_per(200):
                        out.write(json_text({"table": table.name, "row": dict(row)}, 1024 * 1024) + "\n")
            print("Metadata backup exported. Source artifact bytes must be backed up separately.")
    finally:
        databases.close()


if __name__ == "__main__":
    main()
