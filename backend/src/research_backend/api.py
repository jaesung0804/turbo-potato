import hashlib
import os
import secrets
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from .config import PROJECTS, Settings
from .database import Databases
from .objects import LocalObjects, OCIObjects
from .service import Service
from .util import Conflict, Missing, sha_value
from .ingest import query_rows
from .retention import dataset_version
from .schema import datasets, jobs
from .limits import RequestLimits


class RecordInput(BaseModel):
    payload: dict
    expected_version: int = Field(ge=0)
    summary: str = Field(default="", max_length=600)
    source_uri: str | None = Field(default=None, max_length=4000)
    observed_at: str | None = None


class ReferenceDocument(BaseModel):
    relative_path: str = Field(max_length=200)
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    base_artifact_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    expected_version: int = Field(ge=0)


class ReferenceChange(BaseModel):
    relative_path: str = Field(max_length=200)
    kind: str = Field(max_length=30)
    key: str = Field(max_length=200)
    payload: dict
    summary: str = Field(max_length=500)
    before_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class ReferenceTransaction(BaseModel):
    documents: list[ReferenceDocument] = Field(min_length=1, max_length=10)
    records: list[ReferenceChange] = Field(max_length=200)


class SnapshotInput(BaseModel):
    name: str
    previous_id: str | None = None


class Entry(BaseModel):
    relative_path: str = Field(max_length=900)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    byte_size: int = Field(ge=0)


class JobInput(BaseModel):
    job_id: str
    job_type: str
    payload: dict
    max_attempts: int = Field(default=3, ge=1, le=10)


class LeaseInput(BaseModel):
    lease_token: str
    lease_seconds: int = Field(default=300, ge=10, le=3600)


class FinishInput(BaseModel):
    lease_token: str
    result: dict
    success: bool = True


def create_app(settings=None, databases=None, objects=None, tokens=None):
    settings = settings or Settings.from_env()
    tokens = tokens if tokens is not None else {p: os.getenv(p.upper() + "_API_TOKEN", "") for p in PROJECTS}
    if (any(len(tokens.get(p, "")) < 32 or tokens[p].startswith("replace-") for p in PROJECTS)
            or len(set(tokens.values())) != len(PROJECTS)):
        raise ValueError("Configure separate random API tokens of at least 32 characters per project")
    databases = databases or Databases(settings)
    objects = objects or (LocalObjects(settings.data_dir / "objects") if settings.blob_store == "local" else OCIObjects())
    services = {p: Service(databases.engine(p), objects, p, settings.max_response_bytes) for p in PROJECTS}

    @asynccontextmanager
    async def lifespan(app):
        # Schema creation is an explicit CLI operation. Startup/GET never runs migration.
        yield
        databases.close()

    app = FastAPI(title="Project Research Backend", version="0.1.0", lifespan=lifespan)
    app.state.services = services
    app.add_middleware(RequestLimits, max_upload_bytes=settings.max_upload_bytes)

    @app.exception_handler(Conflict)
    async def conflict(_, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(Missing)
    async def missing(_, error):
        return JSONResponse({"detail": str(error)}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(_, error):
        return JSONResponse({"detail": str(error)}, status_code=422)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_, error):
        # Neither DSNs, bind values, research payloads nor credentials reach responses.
        return JSONResponse({"detail": "Database operation failed; retry or check server configuration"}, status_code=503)

    @app.middleware("http")
    async def protect_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def authorize(project: str, authorization: Annotated[str | None, Header()] = None):
        if project not in services:
            raise HTTPException(404, "Unknown project")
        supplied = authorization.removeprefix("Bearer ") if authorization and authorization.startswith("Bearer ") else ""
        if not secrets.compare_digest(supplied.encode(), tokens[project].encode()):
            raise HTTPException(401, "Valid project bearer token required")
        return services[project]

    Dep = Annotated[Service, Depends(authorize)]

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.get("/v1/{project}/ready")
    def ready(service: Dep):
        with service.engine.connect() as db:
            db.execute(select(datasets.c.dataset_key).limit(1))
        return {"status": "ready", "database": service.engine.dialect.name, "blob_store": service.objects.name}

    @app.get("/v1/{project}/records/{kind}")
    def records_list(kind: str, service: Dep, after: str = "", limit: int = Query(50, ge=1, le=200)):
        return service.list_records(kind, after, limit)

    @app.get("/v1/{project}/records/{kind}/{key:path}")
    def record_get(kind: str, key: str, service: Dep):
        return service.get_record(kind, key)

    @app.put("/v1/{project}/records/{kind}/{key:path}")
    def record_put(kind: str, key: str, body: RecordInput, service: Dep):
        return service.put_record(kind, key, **body.model_dump())

    @app.post("/v1/{project}/reference-transactions")
    def reference_transaction(body: ReferenceTransaction, service: Dep):
        return service.sync_references(**body.model_dump())

    @app.get("/v1/{project}/files/{sha}/info")
    def file_info(sha: str, service: Dep):
        return service.file_info(sha)

    @app.put("/v1/{project}/files/{sha}")
    async def file_put(sha: str, request: Request, service: Dep):
        sha_value(sha)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="upload-", dir=settings.data_dir)
        path, total, checksum = Path(name), 0, hashlib.sha256()
        try:
            with os.fdopen(fd, "wb") as stream:
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > settings.max_upload_bytes:
                        raise HTTPException(413, "Artifact exceeds configured limit")
                    checksum.update(chunk)
                    await run_in_threadpool(stream.write, chunk)
            if total != int(request.headers["content-length"]) or checksum.hexdigest() != sha:
                raise HTTPException(422, "Artifact length or SHA-256 mismatch")
            return await run_in_threadpool(service.register_file, sha, path)
        finally:
            path.unlink(missing_ok=True)

    @app.get("/v1/{project}/files/{sha}")
    def file_get(sha: str, service: Dep):
        info = service.file_info(sha)
        return StreamingResponse(service.objects.chunks(service.project, sha), media_type="application/octet-stream",
            headers={"Content-Length": str(info["byte_size"]), "Content-Disposition": f'attachment; filename="{sha}.bin"', "ETag": f'"{sha}"'})

    @app.get("/v1/{project}/snapshot-heads/{name}")
    def snapshot_head(name: str, service: Dep):
        return service.snapshot_head(name)

    @app.post("/v1/{project}/snapshots")
    def begin_snapshot(body: SnapshotInput, service: Dep):
        return service.begin_snapshot(body.name, body.previous_id)

    @app.put("/v1/{project}/snapshots/{sid}/files")
    def snapshot_write(sid: str, body: list[Entry], service: Dep):
        return service.add_snapshot_files(sid, [v.model_dump() for v in body])

    @app.post("/v1/{project}/snapshots/{sid}/commit")
    def snapshot_commit(sid: str, service: Dep, expected_count: int = Query(ge=1)):
        return service.commit_snapshot(sid, expected_count)

    @app.get("/v1/{project}/snapshots/{sid}/files")
    def snapshot_read(sid: str, service: Dep, after: str = "", limit: int = Query(200, ge=1, le=200)):
        return service.snapshot_entries(sid, after, limit)

    @app.get("/v1/{project}/datasets")
    def dataset_list(service: Dep, after: str = "", limit: int = Query(50, ge=1, le=200)):
        with service.engine.connect() as db:
            query = select(datasets)
            if after:
                query = query.where(datasets.c.dataset_key > after)
            rows = db.execute(query.order_by(datasets.c.dataset_key).limit(limit + 1)).mappings().all()
        return service._page(rows, limit, "dataset_key")

    @app.get("/v1/{project}/dataset-version")
    def dataset_version_get(service: Dep, dataset: str, version: str | None = None):
        return dataset_version(service, dataset, version)

    @app.get("/v1/{project}/observations")
    def observations_get(service: Dep, dataset: str, version: str | None = None, entity: str | None = None,
            region: str | None = None, start: str | None = None, end: str | None = None,
            after: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
        return query_rows(service, dataset, version=version, entity=entity, region=region,
                          start=start, end=end, after=after, limit=limit)

    @app.post("/v1/{project}/jobs")
    def job_enqueue(body: JobInput, service: Dep):
        return service.enqueue(body.job_id, body.job_type, body.payload, body.max_attempts)

    @app.post("/v1/{project}/jobs/claim")
    def job_claim(service: Dep, job_type: str, worker_id: str, lease_seconds: int = Query(300, ge=10, le=3600)):
        return {"job": service.claim_job(job_type, worker_id, lease_seconds)}

    @app.get("/v1/{project}/jobs/{jid}")
    def job_get(jid: str, service: Dep):
        with service.engine.connect() as db:
            row = db.execute(select(jobs.c.job_id, jobs.c.job_type, jobs.c.status, jobs.c.attempts,
                jobs.c.max_attempts, jobs.c.updated_at).where(jobs.c.job_id == jid)).mappings().first()
        if not row:
            raise Missing("Job not found")
        return dict(row)

    @app.post("/v1/{project}/jobs/{jid}/renew")
    def job_renew(jid: str, body: LeaseInput, service: Dep):
        return service.renew_job(jid, body.lease_token, body.lease_seconds)

    @app.post("/v1/{project}/jobs/{jid}/finish")
    def job_finish(jid: str, body: FinishInput, service: Dep):
        return service.finish_job(jid, body.lease_token, body.result, body.success)

    return app
