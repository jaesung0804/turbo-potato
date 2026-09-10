from __future__ import annotations

import os
from collections.abc import Iterable
from sqlalchemy import create_engine, event
from .config import Settings, PROJECTS, project_name
from .schema import metadata


def _small_observation_binds(inputsizes, cursor, statement, parameters, context):
    """Avoid per-row temporary LOBs for small observation JSON inserts.

    The column remains CLOB. Other records and larger values keep CLOB binding.
    https://docs.sqlalchemy.org/en/20/dialects/oracle.html#fine-grained-control-over-oracledb-data-binding-with-setinputsizes
    """
    import oracledb
    compiled = getattr(context, "compiled", None)
    table = getattr(getattr(compiled, "statement", None), "table", None)
    if not getattr(context, "isinsert", False) or getattr(table, "name", None) != "observations":
        return
    if not parameters or not all(isinstance(row, dict) for row in parameters):
        return
    for bind, dbtype in list(inputsizes.items()):
        if bind.key != "payload" or dbtype is not oracledb.DB_TYPE_CLOB:
            continue
        name = getattr(compiled, "bind_names", {}).get(bind, bind.key)
        values = [row.get(name) for row in parameters]
        if all(isinstance(value, str) and 0 < len(value.encode("utf-8")) <= 4000 for value in values):
            del inputsizes[bind]


class Databases:
    def __init__(self, settings: Settings, projects: Iterable[str] | None = None):
        self.settings = settings
        self.engines = {}
        if isinstance(projects, str):
            projects = (projects,)
        selected = tuple(dict.fromkeys(project_name(project) for project in
                                       (PROJECTS if projects is None else projects)))
        if not selected:
            raise ValueError("Select at least one project")
        self.projects = selected
        # Initialize serially before HTTP worker threads can access the registry.
        # CLI operations can prepare one DB before the other has credentials.
        try:
            for project in selected:
                self.engines[project] = self._engine(project)
        except Exception:
            self.close()
            raise

    def _engine(self, project):
        if self.settings.database == "sqlite":
            self.settings.data_dir.mkdir(parents=True, exist_ok=True)
            engine = create_engine(
                "sqlite:///" + (self.settings.data_dir / f"{project}.sqlite3").as_posix(),
                connect_args={"timeout": 30, "check_same_thread": False},
            )
            @event.listens_for(engine, "connect")
            def configure(dbapi, _):
                dbapi.execute("PRAGMA journal_mode=WAL")
                dbapi.execute("PRAGMA busy_timeout=30000")
            return engine
        import oracledb
        prefix = project.upper() + "_ORACLE_"
        values = {name: os.getenv(prefix + name) for name in ("USER", "PASSWORD", "DSN")}
        if not all(values.values()):
            raise ValueError(f"Missing {prefix}USER, PASSWORD or DSN")
        kwargs = {"user": values["USER"], "password": values["PASSWORD"], "dsn": values["DSN"]}
        wallet = os.getenv(prefix + "WALLET_DIR")
        if wallet:
            kwargs.update(config_dir=wallet, wallet_location=wallet,
                          wallet_password=os.getenv(prefix + "WALLET_PASSWORD"))
        elif "tcps" not in values["DSN"].lower():
            raise ValueError("Oracle cloud connections require a wallet/TNS config or explicit TCPS DSN")
        size = int(os.getenv("ORACLE_POOL_SIZE", "3"))
        if not 1 <= size <= 10:
            raise ValueError("ORACLE_POOL_SIZE must be 1..10 (free DB has 30 sessions total)")
        def connect():
            connection = oracledb.connect(**kwargs)
            connection.call_timeout = 30000
            return connection
        engine = create_engine("oracle+oracledb://", creator=connect, pool_size=size,
                               max_overflow=0, pool_timeout=15, pool_pre_ping=True,
                               pool_recycle=1800, hide_parameters=True)
        event.listen(engine, "do_setinputsizes", _small_observation_binds)
        return engine

    def engine(self, project):
        project = project_name(project)
        if project not in self.engines:
            raise ValueError(f"Project is not configured in this database context: {project}")
        return self.engines[project]

    def initialize(self):
        for engine in self.engines.values():
            metadata.create_all(engine)

    def close(self):
        for engine in self.engines.values():
            engine.dispose()
