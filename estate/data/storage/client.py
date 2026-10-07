"""Standard-library HTTP client. Safe to vendor into either existing repository."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

REFERENCE_MAX_BYTES = 1024 * 1024
REFERENCE_KINDS = {
    "investment_organization.json": "agents", "investment_operations_queue.json": "tasks",
    "investment_operations_policy.json": "policies", "investment_meeting_rooms.json": "meetings",
    "investment_communications.json": "decisions", "investment_rounds.json": "rounds",
    "investment_research_library.json": "research", "investment_next_round_proposal.json": "policies",
    "investment_replay_policy.json": "policies", "investment_replay_v2_policy.json": "policies",
}


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()


def split_investment(filename, body):
    stem = filename.removesuffix(".json")
    if filename == "investment_organization.json":
        for company_id, company in body.get("companies", {}).items():
            for team_id, team in company.get("teams", {}).items():
                for member in team.get("employees", []):
                    key = str(member["id"])
                    yield key, dict(member, company=company_id, team=team_id), str(member.get("display_name") or member.get("name") or key)
        return
    groups = {"investment_rounds.json": ["rounds"], "investment_operations_queue.json": ["tasks"],
              "investment_research_library.json": ["companies", "pilot_companies", "requests", "sources"]}
    if filename in groups:
        for group in groups[filename]:
            for index, item in enumerate(body.get(group, [])):
                key = str(item.get("id") or item.get("ticker") or item.get("hypothesis_id") or index)
                yield f"{stem}:{group}:{key}", item, str(item.get("title") or item.get("name") or key)
        return
    if len(json.dumps(body, ensure_ascii=False).encode()) > 120000:
        yield stem, {"note": "Read the documents artifact for full source; this summary is intentionally bounded"}, filename
    else:
        yield stem, body, filename


def reference_projection(relative_path, body):
    filename = PurePosixPath(relative_path).name
    if relative_path != "data/reference/" + filename or filename not in REFERENCE_KINDS or not isinstance(body, dict):
        raise ValueError("Unknown investment reference document")
    result = {}
    try:
        for key, payload, summary in split_investment(filename, body):
            if len(canonical_json(payload)) > 131072 or key in result:
                raise ValueError("Reference record too large or duplicated")
            result[key] = {"relative_path": relative_path, "kind": REFERENCE_KINDS[filename],
                           "key": key, "payload": payload, "summary": summary[:500]}
    except (AttributeError, KeyError, TypeError):
        raise ValueError("Invalid investment reference structure") from None
    return result


def reference_delta(relative_path, previous, current):
    old = reference_projection(relative_path, previous) if previous is not None else {}
    new = reference_projection(relative_path, current)
    if not old.keys() <= new.keys():
        raise ValueError("Reference removal requires an explicit archive operation")
    return [value | {"before_sha256": hashlib.sha256(canonical_json(old[key]["payload"])).hexdigest() if key in old else None}
            for key, value in new.items() if key not in old or value != old[key]]


class BackendError(RuntimeError):
    def __init__(self, status, detail):
        self.status = status
        super().__init__(detail)


def safe_path(root, value):
    rel = PurePosixPath(value)
    if (not value or rel.is_absolute() or str(rel) != value or "\\" in value or ":" in value
            or any(p in {".", ".."} or p.lower() == ".git" or p.lower().startswith(".env") for p in rel.parts)):
        raise ValueError("Unsafe state path")
    target = root.joinpath(*rel.parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("State path leaves destination")
    # Reject symlinks even if they currently point inside the root.
    if any(p.is_symlink() for p in (target, *target.parents) if p != root.parent):
        raise ValueError("Symlink in state path")
    return target


class Client:
    def __init__(self, base_url=None, token=None, project=None):
        self.project = project or os.environ["RESEARCH_PROJECT"]
        if self.project not in {"estate", "investment"}:
            raise ValueError("Unknown project")
        base_url = (base_url or os.environ["RESEARCH_BACKEND_URL"]).rstrip("/")
        parsed = urllib.parse.urlsplit(base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise ValueError("Invalid backend URL")
        if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Remote backend requires HTTPS")
        self.base_url = base_url + "/v1/" + self.project
        self.token = token or os.environ["RESEARCH_BACKEND_TOKEN"]
        self.versions = {}
        self.reference_bases = {}
        # Never forward bearer tokens across redirects.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, method, path, body=None, file=None):
        headers = {"Authorization": "Bearer " + self.token}
        if file is not None:
            headers["Content-Type"] = "application/octet-stream"
            headers["Content-Length"] = str(os.fstat(file.fileno()).st_size)
            data = file
        elif body is not None:
            data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(data))
        elif method in {"POST", "PUT"}:
            data = b""
            headers["Content-Length"] = "0"
        else:
            data = None
        request = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
        try:
            return self.opener.open(request, timeout=120)
        except urllib.error.HTTPError as error:
            # Responses have bounded sanitized errors. Do not print URLs with credentials or request data.
            message = error.read(4096).decode("utf-8", errors="replace")
            raise BackendError(error.code, message) from None

    def json(self, method, path, body=None):
        with self.request(method, path, body) as response:
            return json.load(response)

    def upload(self, source):
        source = Path(source)
        with source.open("rb") as stream:
            sha = hashlib.file_digest(stream, "sha256").hexdigest()
        try:
            return self.json("GET", f"/files/{sha}/info") | {"changed": False}
        except BackendError as error:
            if error.status != 404:
                raise
        with source.open("rb") as stream, self.request("PUT", f"/files/{sha}", file=stream) as response:
            return json.load(response)

    def download(self, sha, target, expected_size=None):
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        digest, count = hashlib.sha256(), 0
        try:
            with os.fdopen(fd, "wb") as out, self.request("GET", f"/files/{sha}") as response:
                while chunk := response.read(1024 * 1024):
                    count += len(chunk)
                    if expected_size is not None and count > expected_size:
                        raise ValueError("Artifact exceeds manifest size")
                    digest.update(chunk); out.write(chunk)
            if digest.hexdigest() != sha or (expected_size is not None and count != expected_size):
                raise ValueError("Artifact failed integrity check; existing file retained")
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def read_json(self, relative_path):
        key = hashlib.sha256(relative_path.encode()).hexdigest()
        document = self.json("GET", f"/records/documents/{key}")
        self.versions[relative_path] = document["version"]
        sha = document["payload"]["artifact_sha256"]
        known_reference = relative_path == "data/reference/" + PurePosixPath(relative_path).name and PurePosixPath(relative_path).name in REFERENCE_KINDS
        limit = REFERENCE_MAX_BYTES if known_reference else 16 * 1024 * 1024
        with self.request("GET", f"/files/{sha}") as response:
            raw = response.read(limit + 1)
        if len(raw) > limit or hashlib.sha256(raw).hexdigest() != sha:
            raise ValueError("JSON artifact too large or corrupt; use bounded record queries")
        if known_reference:
            self.reference_bases[relative_path] = raw
        return json.loads(raw)

    def write_references(self, updates):
        if self.project != "investment" or not 1 <= len(updates) <= 10:
            raise ValueError("Investment reference transactions require 1..10 documents")
        documents, records, encoded = [], [], {}
        projected_count = 0
        for relative_path, value in sorted(updates.items()):
            if relative_path not in self.versions or relative_path not in self.reference_bases:
                raise BackendError(409, "Read every reference document before writing; reread after a conflict")
            raw = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()
            if len(raw) > REFERENCE_MAX_BYTES:
                raise ValueError("Reference artifact exceeds 1 MiB; split the source first")
            previous_raw = self.reference_bases[relative_path]
            previous = json.loads(previous_raw) if previous_raw is not None else None
            projected_count += len(reference_projection(relative_path, value))
            records.extend(reference_delta(relative_path, previous, value))
            documents.append({"relative_path": relative_path, "artifact_sha256": hashlib.sha256(raw).hexdigest(),
                              "base_artifact_sha256": hashlib.sha256(previous_raw).hexdigest() if previous_raw is not None else None,
                              "expected_version": self.versions[relative_path]})
            encoded[relative_path] = raw
        request = {"documents": documents, "records": records}
        request_bytes = json.dumps(request, ensure_ascii=False, allow_nan=False).encode()
        if projected_count > 200 or len(records) > 200 or len(request_bytes) > REFERENCE_MAX_BYTES:
            raise ValueError("Reference transaction exceeds 200 records or 1 MiB; split the source first")
        with tempfile.TemporaryDirectory() as tmp:
            for index, raw in enumerate(encoded.values()):
                path = Path(tmp) / str(index)
                path.write_bytes(raw)
                self.upload(path)
        result = self.json("POST", "/reference-transactions", request)
        for document in result["documents"]:
            relative_path = document["relative_path"]
            self.versions[relative_path] = document["version"]
            self.reference_bases[relative_path] = encoded[relative_path]
        return result

    def write_json(self, relative_path, value):
        key = hashlib.sha256(relative_path.encode()).hexdigest()
        if relative_path not in self.versions:
            try:
                self.json("GET", f"/records/documents/{key}")
            except BackendError as error:
                if error.status != 404:
                    raise
                self.versions[relative_path] = 0
            else:
                raise BackendError(409, "Read this document first; unconditional overwrite is disabled")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "document.json"
            path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
            info = self.upload(path)
        result = self.json("PUT", f"/records/documents/{key}", {
            "payload": {"artifact_sha256": info["sha256"], "format": "json", "relative_path": relative_path},
            "summary": relative_path[:500], "expected_version": self.versions[relative_path]})
        self.versions[relative_path] = result["version"]
        return result

    def snapshot_entries(self, sid):
        cursor = ""
        while True:
            page = self.json("GET", f"/snapshots/{sid}/files?" + urllib.parse.urlencode({"after": cursor}))
            yield from page["items"]
            cursor = page["next_cursor"]
            if cursor is None:
                return

    def _receipt(self, root, name):
        path = root / '.research-backend' / (hashlib.sha256(name.encode()).hexdigest() + '.json')
        safe_path(root, path.relative_to(root).as_posix())
        return path

    def _save_receipt(self, root, name, sid):
        path = self._receipt(root, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump({'snapshot_id': sid}, stream)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def push(self, name, root, paths):
        root = Path(root).resolve()
        head = self.json("GET", f"/snapshot-heads/{urllib.parse.quote(name, safe='')}")
        receipt = self._receipt(root, name)
        restored = json.loads(receipt.read_text(encoding='utf-8'))['snapshot_id'] if receipt.exists() else None
        if head['snapshot_id'] != restored:
            raise BackendError(409, 'Remote state changed since restore, or no restore receipt exists. Pull before editing and pushing.')
        old = {e["relative_path"]: e for e in self.snapshot_entries(head["snapshot_id"])} if head["snapshot_id"] else {}
        # Retain unselected old files; selected missing paths are errors, never deletion requests.
        entries, uploaded = dict(old), 0
        for value in paths:
            source = safe_path(root, value)
            if not source.exists():
                raise FileNotFoundError("Required state path missing: " + value)
            candidates = source.rglob("*") if source.is_dir() else [source]
            for path in candidates:
                if not path.is_file():
                    continue
                rel = path.relative_to(root).as_posix()
                safe_path(root, rel)
                with tempfile.TemporaryDirectory() as tmp:
                    staged = Path(tmp) / "artifact"
                    with path.open("rb") as inp, staged.open("wb") as out:
                        shutil.copyfileobj(inp, out, 1024 * 1024)
                    info = self.upload(staged)
                entries[rel] = {"relative_path": rel, "sha256": info["sha256"], "byte_size": info["byte_size"]}
                uploaded += bool(info.get("changed"))
        same = len(entries) == len(old) and all(old[k]["sha256"] == v["sha256"] for k, v in entries.items())
        if same and head["snapshot_id"]:
            return {"snapshot_id": head["snapshot_id"], "files": len(entries), "uploaded": 0, "changed": False}
        sid = self.json("POST", "/snapshots", {"name": name, "previous_id": head["snapshot_id"]})["snapshot_id"]
        values = list(entries.values())
        for start in range(0, len(values), 200):
            self.json("PUT", f"/snapshots/{sid}/files", values[start:start + 200])
        self.json("POST", f"/snapshots/{sid}/commit?expected_count={len(values)}")
        self._save_receipt(root, name, sid)
        return {"snapshot_id": sid, "files": len(entries), "uploaded": uploaded, "changed": True}

    def pull(self, name, root):
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        head = self.json("GET", f"/snapshot-heads/{urllib.parse.quote(name, safe='')}")
        sid = head["snapshot_id"]
        if not sid:
            raise BackendError(404, "No complete backend snapshot; migrate existing Git state first")
        changed, files = 0, 0
        # Validate and download all changed bytes before replacing any working files.
        with tempfile.TemporaryDirectory(prefix="state-restore-", dir=root) as tmp:
            pending = []
            for entry in self.snapshot_entries(sid):
                files += 1
                target = safe_path(root, entry["relative_path"])
                if target.is_file() and target.stat().st_size == entry["byte_size"]:
                    with target.open("rb") as source:
                        if hashlib.file_digest(source, "sha256").hexdigest() == entry["sha256"]:
                            continue
                staged = Path(tmp) / str(changed)
                self.download(entry["sha256"], staged, entry["byte_size"])
                pending.append((staged, target)); changed += 1
            for source, target in pending:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, target)
        self._save_receipt(root, name, sid)
        return {"snapshot_id": sid, "files": files, "downloaded": changed}


def main():
    p = argparse.ArgumentParser(description="Store data outside Git; preserve verified snapshot versions")
    p.add_argument("command", choices=["push", "pull", "check"])
    p.add_argument("--project", choices=["estate", "investment"], required=True)
    p.add_argument("--name", default="pipeline-state")
    p.add_argument("--root", type=Path, default=Path("."))
    p.add_argument("--paths", nargs="+")
    args = p.parse_args()
    client = Client(project=args.project)
    if args.command == "check":
        result = client.json("GET", "/ready")
    elif args.command == "pull":
        result = client.pull(args.name, args.root)
    else:
        if not args.paths:
            p.error("push requires explicit --paths")
        result = client.push(args.name, args.root, args.paths)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
