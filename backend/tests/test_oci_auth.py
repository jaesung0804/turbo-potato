import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from research_backend.objects import OCIObjects


@pytest.fixture
def fake_oci(monkeypatch):
    sdk = SimpleNamespace(
        config=SimpleNamespace(from_file=Mock(return_value={"region": "us-phoenix-1"})),
        signer=SimpleNamespace(load_private_key_from_file=Mock()),
        auth=SimpleNamespace(signers=SimpleNamespace(
            InstancePrincipalsSecurityTokenSigner=Mock(), SecurityTokenSigner=Mock())),
        object_storage=SimpleNamespace(ObjectStorageClient=Mock(), UploadManager=Mock()),
    )
    monkeypatch.setitem(sys.modules, "oci", sdk)
    monkeypatch.setenv("OCI_NAMESPACE", "test-namespace")
    monkeypatch.setenv("OCI_BUCKET", "test-private-bucket")
    monkeypatch.setenv("OCI_CONFIG_FILE", "test-config")
    monkeypatch.delenv("OCI_AUTH", raising=False)
    monkeypatch.delenv("OCI_PROFILE", raising=False)
    monkeypatch.delenv("OCI_USE_INSTANCE_PRINCIPAL", raising=False)
    return sdk


def test_existing_api_key_and_instance_principal_authentication_remain_available(fake_oci, monkeypatch):
    sdk = fake_oci
    OCIObjects()
    sdk.config.from_file.assert_called_once_with("test-config", "DEFAULT")
    sdk.object_storage.ObjectStorageClient.assert_called_once_with(sdk.config.from_file.return_value)
    sdk.auth.signers.SecurityTokenSigner.assert_not_called()

    sdk.config.from_file.reset_mock()
    sdk.object_storage.ObjectStorageClient.reset_mock()
    monkeypatch.setenv("OCI_USE_INSTANCE_PRINCIPAL", "true")
    OCIObjects()
    sdk.config.from_file.assert_not_called()
    sdk.object_storage.ObjectStorageClient.assert_called_once_with(
        {}, signer=sdk.auth.signers.InstancePrincipalsSecurityTokenSigner.return_value)
    sdk.signer.load_private_key_from_file.assert_not_called()


def test_explicit_security_token_uses_session_key_and_sanitizes_request_logging(fake_oci, monkeypatch, tmp_path, capsys):
    sdk = fake_oci
    token_file = tmp_path / "session-token"
    token_file.write_text("  synthetic-test-token\n", encoding="utf-8")
    config = {"region": "us-phoenix-1", "security_token_file": str(token_file),
              "key_file": "synthetic-private-key-file", "pass_phrase": "synthetic-test-passphrase",
              "log_requests": True}
    sdk.config.from_file.return_value = config
    monkeypatch.setenv("OCI_AUTH", "security_token")
    monkeypatch.setenv("OCI_PROFILE", "temporary-session")
    # An explicit mode wins over an older deployment's legacy flag.
    monkeypatch.setenv("OCI_USE_INSTANCE_PRINCIPAL", "true")
    store = OCIObjects()
    sdk.config.from_file.assert_called_once_with("test-config", "temporary-session")
    sdk.signer.load_private_key_from_file.assert_called_once_with(
        "synthetic-private-key-file", "synthetic-test-passphrase")
    sdk.auth.signers.SecurityTokenSigner.assert_called_once_with(
        "synthetic-test-token", sdk.signer.load_private_key_from_file.return_value)
    sdk.object_storage.ObjectStorageClient.assert_called_once_with(
        config, signer=sdk.auth.signers.SecurityTokenSigner.return_value)
    sdk.object_storage.UploadManager.assert_called_once_with(store.client, allow_parallel_uploads=False)
    sdk.auth.signers.InstancePrincipalsSecurityTokenSigner.assert_not_called()
    assert config["log_requests"] is False
    assert capsys.readouterr() == ("", "")


def test_security_token_loading_errors_are_redacted_without_api_key_fallback(fake_oci, monkeypatch, tmp_path, capsys):
    sdk = fake_oci
    token_file = tmp_path / "session-token"
    token_file.write_text("synthetic-test-token", encoding="utf-8")
    sdk.config.from_file.return_value = {
        "security_token_file": str(token_file), "key_file": "synthetic-private-key-file"}
    sdk.signer.load_private_key_from_file.side_effect = ValueError("synthetic-private-key-material")
    monkeypatch.setenv("OCI_AUTH", "security_token")
    with pytest.raises(ValueError, match="credentials could not be loaded") as error:
        OCIObjects()
    assert "synthetic-private-key-material" not in str(error.value)
    assert error.value.__suppress_context__ is True
    sdk.auth.signers.SecurityTokenSigner.assert_not_called()
    sdk.object_storage.ObjectStorageClient.assert_not_called()
    assert capsys.readouterr() == ("", "")
