import json
import sys

import pytest
from sqlalchemy import inspect

from research_backend import cli
from research_backend.config import Settings
from research_backend.database import Databases


def test_scoped_oracle_setup_does_not_require_other_projects_credentials(tmp_path, monkeypatch):
    # Engine construction is lazy: no cloud connection or real credential is used.
    for name in ("USER", "PASSWORD", "DSN", "WALLET_DIR", "WALLET_PASSWORD"):
        monkeypatch.delenv("INVESTMENT_ORACLE_" + name, raising=False)
        monkeypatch.delenv("ESTATE_ORACLE_" + name, raising=False)
    monkeypatch.setenv("ESTATE_ORACLE_USER", "test_user")
    monkeypatch.setenv("ESTATE_ORACLE_PASSWORD", "test_only_placeholder")
    monkeypatch.setenv("ESTATE_ORACLE_DSN", "tcps://example.invalid:1522/test")
    databases = Databases(Settings(tmp_path, database="oracle"), projects=("estate",))
    try:
        assert databases.engine("estate").dialect.name == "oracle"
        with pytest.raises(ValueError, match="not configured"):
            databases.engine("investment")
    finally:
        databases.close()
    with pytest.raises(ValueError, match="INVESTMENT_ORACLE_USER"):
        Databases(Settings(tmp_path, database="oracle"))


def test_default_context_initializes_both_project_schemas(tmp_path):
    databases = Databases(Settings(tmp_path))
    try:
        databases.initialize()
        for project in ("estate", "investment"):
            assert "research_records" in inspect(databases.engine(project)).get_table_names()
    finally:
        databases.close()


@pytest.mark.parametrize("projects", [(), ("estate", "unknown")])
def test_invalid_scope_has_no_engine_side_effects(tmp_path, monkeypatch, projects):
    def unexpected_engine(*args):
        pytest.fail("Invalid scope must be rejected before creating an engine")
    monkeypatch.setattr(Databases, "_engine", unexpected_engine)
    with pytest.raises(ValueError):
        Databases(Settings(tmp_path), projects=projects)


def test_scoped_cli_init_and_check_need_no_object_store_credentials(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BACKEND_DATABASE", "sqlite")
    monkeypatch.setenv("BACKEND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BACKEND_BLOB_STORE", "oci")
    monkeypatch.delenv("OCI_NAMESPACE", raising=False)
    monkeypatch.delenv("OCI_CONFIG_FILE", raising=False)
    for command in ("init-db", "check"):
        monkeypatch.setattr(sys, "argv", ["research-backend", command, "--project", "estate"])
        cli.main()
    assert "estate" in capsys.readouterr().out
    assert (tmp_path / "estate.sqlite3").is_file()
    assert not (tmp_path / "investment.sqlite3").exists()


def test_import_export_and_migration_use_only_selected_database(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("BACKEND_DATABASE", "sqlite")
    monkeypatch.setenv("BACKEND_DATA_DIR", str(runtime))
    monkeypatch.setenv("BACKEND_BLOB_STORE", "local")
    original_engine = Databases._engine

    def investment_only(self, project):
        if project != "investment":
            pytest.fail("A single-project operation attempted to configure another DB")
        return original_engine(self, project)

    monkeypatch.setattr(Databases, "_engine", investment_only)
    source = tmp_path / "prices.csv"
    source.write_text("date,ticker,close\n2026-09-01,TEST,123\n", encoding="utf-8")
    references = tmp_path / "repo" / "data" / "reference"
    references.mkdir(parents=True)
    (references / "investment_operations_policy.json").write_text(
        json.dumps({"mode": "bounded"}), encoding="utf-8")
    exported = tmp_path / "metadata.jsonl"
    commands = [
        ["init-db", "--project", "investment"],
        ["import-data", "--project", "investment", "--dataset", "test-prices", "--path", str(source)],
        ["migrate-investment", "--root", str(tmp_path / "repo")],
        ["export-records", "--project", "investment", "--output", str(exported)],
    ]
    for command in commands:
        monkeypatch.setattr(sys, "argv", ["research-backend", *command])
        cli.main()
    records = [json.loads(line) for line in exported.read_text(encoding="utf-8").splitlines()]
    assert any(item["table"] == "dataset_heads" and item["row"]["row_count"] == 1 for item in records)
    assert any(item["table"] == "research_records" and item["row"]["kind"] == "policies" for item in records)
    assert not (runtime / "estate.sqlite3").exists()
