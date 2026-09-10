import zipfile
import pytest
from dotenv import dotenv_values
from research_backend.oracle_bootstrap import SetupError, extract_wallet, safe_error, write_private_env


def wallet(path, extra=None, project="estate"):
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("tnsnames.ora", project + "_low = (description=(address=(host=adb.us-phoenix-1.oraclecloud.com)))")
        bundle.writestr("ewallet.pem", "test fixture; not a credential")
        if extra:
            bundle.writestr(extra, "unsafe")


def test_wallet_project_and_path_boundaries(tmp_path):
    source = tmp_path / "wallet.zip"
    wallet(source, project="investment")
    with pytest.raises(SetupError, match="does not contain estate"):
        extract_wallet(source, tmp_path / "private", "estate")
    wallet(source, extra="../escaped")
    with pytest.raises(SetupError, match="unsafe member"):
        extract_wallet(source, tmp_path / "private", "estate")
    assert not (tmp_path / "escaped").exists()
    wallet(source)
    folder = extract_wallet(source, tmp_path / "private", "estate")
    assert (folder / "tnsnames.ora").is_file()


def test_private_env_roundtrip_and_error_redaction(tmp_path):
    path = tmp_path / "test.env"
    values = {"EXAMPLE": "a'b\\c$literal"}
    write_private_env(path, values)
    assert dict(dotenv_values(path)) == values
    assert safe_error(RuntimeError("failed SQL with secret-password")) == "RuntimeError"
