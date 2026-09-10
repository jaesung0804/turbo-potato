"""Content-addressed private artifacts; uploads never create public URLs."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from .config import project_name
from .util import sha_value


class LocalObjects:
    name = "local"

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, project, sha):
        return self.root / project_name(project) / sha_value(sha)[:2] / sha

    def put(self, project, sha, source):
        target = self.path(project, sha)
        if target.exists():
            with target.open("rb") as f:
                if hashlib.file_digest(f, "sha256").hexdigest() != sha:
                    raise ValueError("Existing artifact failed integrity check")
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as out, Path(source).open("rb") as inp:
                shutil.copyfileobj(inp, out, 1024 * 1024)
                out.flush()
                os.fsync(out.fileno())
            os.replace(name, target)
        finally:
            Path(name).unlink(missing_ok=True)

    def chunks(self, project, sha):
        with self.path(project, sha).open("rb") as f:
            while body := f.read(256 * 1024):
                yield body


class OCIObjects:
    name = "oci"

    def __init__(self):
        import oci
        self.namespace = os.environ["OCI_NAMESPACE"]
        self.bucket = os.environ["OCI_BUCKET"]
        default_auth = ("instance_principal" if
                        os.getenv("OCI_USE_INSTANCE_PRINCIPAL", "false").lower() == "true" else "api_key")
        auth = os.getenv("OCI_AUTH", default_auth).strip().lower()
        if auth not in {"api_key", "instance_principal", "security_token"}:
            raise ValueError("OCI_AUTH must be api_key, instance_principal, or security_token")
        if auth == "instance_principal":
            signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
            self.client = oci.object_storage.ObjectStorageClient({}, signer=signer)
        else:
            config = oci.config.from_file(os.environ["OCI_CONFIG_FILE"], os.getenv("OCI_PROFILE", "DEFAULT"))
            if auth == "security_token":
                if not config.get("security_token_file") or not config.get("key_file"):
                    raise ValueError("OCI security_token authentication requires security_token_file and key_file")
                try:
                    token = Path(config["security_token_file"]).expanduser().read_text(encoding="utf-8").strip()
                    if not token:
                        raise ValueError("Empty security token")
                    private_key = oci.signer.load_private_key_from_file(
                        config["key_file"], config.get("pass_phrase"))
                    signer = oci.auth.signers.SecurityTokenSigner(token, private_key)
                except Exception:
                    # Parsing errors can include credential contents. Keep both
                    # the exception message and chained traceback secret-free.
                    raise ValueError("OCI security token credentials could not be loaded; check the session files") from None
                config["log_requests"] = False
                self.client = oci.object_storage.ObjectStorageClient(config, signer=signer)
            else:
                self.client = oci.object_storage.ObjectStorageClient(config)
        self.uploader = oci.object_storage.UploadManager(self.client, allow_parallel_uploads=False)

    def key(self, project, sha):
        return f"{project_name(project)}/objects/{sha_value(sha)[:2]}/{sha}"

    def put(self, project, sha, source):
        self.uploader.upload_file(self.namespace, self.bucket, self.key(project, sha), str(source),
                                  content_type="application/octet-stream", opc_meta={"sha256": sha})

    def chunks(self, project, sha):
        response = self.client.get_object(self.namespace, self.bucket, self.key(project, sha))
        try:
            yield from response.data.raw.stream(256 * 1024, decode_content=False)
        finally:
            response.data.close()
