from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECTS = ("estate", "investment")


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database: str = "sqlite"
    blob_store: str = "local"
    max_upload_bytes: int = 2 * 1024**3
    max_response_bytes: int = 256 * 1024

    @classmethod
    def from_env(cls):
        return cls(
            data_dir=Path(os.getenv("BACKEND_DATA_DIR", "runtime")).resolve(),
            database=os.getenv("BACKEND_DATABASE", "sqlite"),
            blob_store=os.getenv("BACKEND_BLOB_STORE", "local"),
            max_upload_bytes=int(os.getenv("MAX_UPLOAD_BYTES", str(2 * 1024**3))),
            max_response_bytes=int(os.getenv("MAX_RESPONSE_BYTES", "262144")),
        )

    def __post_init__(self):
        if self.database not in {"sqlite", "oracle"}:
            raise ValueError("BACKEND_DATABASE must be sqlite or oracle")
        if self.blob_store not in {"local", "oci"}:
            raise ValueError("BACKEND_BLOB_STORE must be local or oci")
        if self.max_upload_bytes < 1 or self.max_response_bytes < 8192:
            raise ValueError("Invalid storage/response limits")


def project_name(project: str) -> str:
    if project not in PROJECTS:
        raise ValueError("Unknown project")
    return project
