"""인시던트 스토어.

환경변수:
    WARROOM_STORE     memory | sqlite       (기본: sqlite)
    WARROOM_DB_PATH   sqlite 파일 경로      (기본: ./data/warroom.db)

memory 백엔드는 프로세스 라이프사이클과 함께 사라지며, sqlite 백엔드는
재시작·다중 클라이언트(`demo.py` ↔ `serve.py`) 간 인시던트 상태를 공유한다.
테스트는 `WARROOM_DB_PATH=:memory:` 로 격리할 수 있다.
"""
import os
import sqlite3
from pathlib import Path
from threading import Lock
from typing import Protocol, runtime_checkable

from common.models import IncidentCategory, IncidentEvent, IncidentStatus, ResolutionReport


@runtime_checkable
class IncidentStore(Protocol):
    def add(self, event: IncidentEvent) -> None: ...
    def update_status(
        self,
        incident_id: str,
        status: IncidentStatus,
        is_approved: bool | None = None,
    ) -> None: ...
    def save_report(self, incident_id: str, report: ResolutionReport) -> None: ...
    def get(self, incident_id: str) -> dict | None: ...
    def list_all(self) -> list[dict]: ...
    def increment_dupe_count(self, incident_id: str) -> int: ...


class InMemoryIncidentStore:
    def __init__(self):
        self._store: dict[str, dict] = {}

    def add(self, event: IncidentEvent) -> None:
        self._store[event.incident_id] = {
            "incident_id": event.incident_id,
            "source": event.source,
            "title": event.title,
            "status": IncidentStatus.PENDING,
            "report": None,
            "dupe_count": 1,
        }

    def update_status(self, incident_id, status, is_approved=None):
        if incident_id in self._store:
            self._store[incident_id]["status"] = status
            if is_approved is not None and self._store[incident_id]["report"]:
                self._store[incident_id]["report"]["is_approved"] = is_approved

    def save_report(self, incident_id, report):
        if incident_id in self._store:
            self._store[incident_id]["report"] = {
                "severity": report.severity.value,
                "category": report.category.value,
                "triage_summary": report.triage_summary,
                "root_cause": report.root_cause,
                "patch_suggestion": report.patch_suggestion,
                "post_mortem_draft": report.post_mortem_draft,
                "is_approved": report.is_approved,
                "created_at": report.created_at.isoformat(),
            }

    def get(self, incident_id):
        return self._store.get(incident_id)

    def list_all(self):
        return list(self._store.values())

    def increment_dupe_count(self, incident_id: str) -> int:
        if incident_id not in self._store:
            return 0
        self._store[incident_id]["dupe_count"] = self._store[incident_id].get("dupe_count", 1) + 1
        return self._store[incident_id]["dupe_count"]


class SqliteIncidentStore:
    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS incidents (
        incident_id TEXT PRIMARY KEY,
        source      TEXT NOT NULL,
        title       TEXT NOT NULL,
        status      TEXT NOT NULL,
        dupe_count  INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS reports (
        incident_id       TEXT PRIMARY KEY REFERENCES incidents(incident_id),
        severity          TEXT NOT NULL,
        category          TEXT NOT NULL DEFAULT 'code',
        triage_summary    TEXT,
        root_cause        TEXT,
        patch_suggestion  TEXT,
        post_mortem_draft TEXT,
        is_approved       INTEGER,
        created_at        TEXT NOT NULL
    );
    """

    def __init__(self, db_path: str = ":memory:"):
        self._db_path = db_path
        self._lock = Lock()
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(self._SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        # ALTER TABLE ADD COLUMN 은 멱등이 아니므로 PRAGMA 로 존재 여부 확인
        incident_cols = {row["name"] for row in self._conn.execute("PRAGMA table_info(incidents)")}
        if "dupe_count" not in incident_cols:
            self._conn.execute(
                "ALTER TABLE incidents ADD COLUMN dupe_count INTEGER NOT NULL DEFAULT 1"
            )
        report_cols = {row["name"] for row in self._conn.execute("PRAGMA table_info(reports)")}
        if "category" not in report_cols:
            self._conn.execute(
                "ALTER TABLE reports ADD COLUMN category TEXT NOT NULL DEFAULT 'code'"
            )

    def add(self, event: IncidentEvent) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO incidents (incident_id, source, title, status, dupe_count) "
                "VALUES (?, ?, ?, ?, 1)",
                (event.incident_id, event.source, event.title, IncidentStatus.PENDING.value),
            )
            self._conn.execute(
                "DELETE FROM reports WHERE incident_id = ?",
                (event.incident_id,),
            )
            self._conn.commit()

    def update_status(self, incident_id, status, is_approved=None):
        status_value = status.value if hasattr(status, "value") else status
        with self._lock:
            self._conn.execute(
                "UPDATE incidents SET status = ? WHERE incident_id = ?",
                (status_value, incident_id),
            )
            if is_approved is not None:
                self._conn.execute(
                    "UPDATE reports SET is_approved = ? WHERE incident_id = ?",
                    (1 if is_approved else 0, incident_id),
                )
            self._conn.commit()

    def save_report(self, incident_id, report: ResolutionReport):
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO reports
                   (incident_id, severity, category, triage_summary, root_cause,
                    patch_suggestion, post_mortem_draft, is_approved, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    incident_id,
                    report.severity.value,
                    report.category.value,
                    report.triage_summary,
                    report.root_cause,
                    report.patch_suggestion,
                    report.post_mortem_draft,
                    None if report.is_approved is None else int(report.is_approved),
                    report.created_at.isoformat(),
                ),
            )
            self._conn.commit()

    def get(self, incident_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT incident_id, source, title, status, dupe_count FROM incidents WHERE incident_id = ?",
            (incident_id,),
        ).fetchone()
        if not row:
            return None
        result = {
            "incident_id": row["incident_id"],
            "source": row["source"],
            "title": row["title"],
            "status": IncidentStatus(row["status"]),
            "dupe_count": row["dupe_count"],
            "report": None,
        }
        rep = self._conn.execute(
            """SELECT severity, category, triage_summary, root_cause, patch_suggestion,
                      post_mortem_draft, is_approved, created_at
               FROM reports WHERE incident_id = ?""",
            (incident_id,),
        ).fetchone()
        if rep:
            result["report"] = {
                "severity": rep["severity"],
                "category": rep["category"],
                "triage_summary": rep["triage_summary"],
                "root_cause": rep["root_cause"],
                "patch_suggestion": rep["patch_suggestion"],
                "post_mortem_draft": rep["post_mortem_draft"],
                "is_approved": None if rep["is_approved"] is None else bool(rep["is_approved"]),
                "created_at": rep["created_at"],
            }
        return result

    def list_all(self) -> list[dict]:
        ids = [r["incident_id"] for r in self._conn.execute(
            "SELECT incident_id FROM incidents ORDER BY incident_id"
        ).fetchall()]
        return [self.get(i) for i in ids if self.get(i) is not None]

    def increment_dupe_count(self, incident_id: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE incidents SET dupe_count = dupe_count + 1 "
                "WHERE incident_id = ? RETURNING dupe_count",
                (incident_id,),
            )
            row = cur.fetchone()
            self._conn.commit()
            return row["dupe_count"] if row else 0


def _build_default_store() -> IncidentStore:
    backend = os.getenv("WARROOM_STORE", "sqlite").lower()
    if backend == "memory":
        return InMemoryIncidentStore()
    if backend == "sqlite":
        return SqliteIncidentStore(os.getenv("WARROOM_DB_PATH", "./data/warroom.db"))
    raise ValueError(
        f"지원하지 않는 WARROOM_STORE: {backend!r}. 사용 가능: memory, sqlite"
    )


incident_store: IncidentStore = _build_default_store()
