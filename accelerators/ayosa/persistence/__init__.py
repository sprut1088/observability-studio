"""AYOSA persistence layer (SQLite, optional).

Public surface:
    init_db(path=None) -> Repository
    get_default_repository() -> Repository | None
    Repository.create_run(...)
    Repository.get_run(run_id)
    Repository.list_runs(session_id=None, service=None, limit=50)
    Repository.compare_runs(run_id_a, run_id_b)

Failure mode: every public call must swallow DB errors and either return
None / [] / {} or log a warning. AYOSA chat MUST keep working even when
the database is unavailable.
"""

from __future__ import annotations

from accelerators.ayosa.persistence.db import (
    DEFAULT_DB_PATH,
    Database,
    init_db,
)
from accelerators.ayosa.persistence.models import (
    PersistedRun,
    RunComparison,
    RunSummary,
)
from accelerators.ayosa.persistence.repository import (
    Repository,
    get_default_repository,
    persist_agent_result,
    set_default_repository,
)

__all__ = [
    "DEFAULT_DB_PATH",
    "Database",
    "PersistedRun",
    "Repository",
    "RunComparison",
    "RunSummary",
    "get_default_repository",
    "init_db",
    "persist_agent_result",
    "set_default_repository",
]
