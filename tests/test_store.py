"""IncidentStore 구현체 단위 테스트.

memory / sqlite 두 백엔드가 동일한 IncidentStore Protocol을 만족하는지
교차 검증한다.
"""
from datetime import datetime

import pytest

from common.models import IncidentEvent, IncidentStatus, ResolutionReport, Severity
from gateway.store import (
    InMemoryIncidentStore,
    SqliteIncidentStore,
    IncidentStore,
)


@pytest.fixture(params=["memory", "sqlite"])
def store(request) -> IncidentStore:
    if request.param == "memory":
        return InMemoryIncidentStore()
    return SqliteIncidentStore(":memory:")


@pytest.fixture
def event():
    return IncidentEvent(
        incident_id="sentry-1",
        source="sentry",
        title="NPE in stripe.charge",
        raw_payload={"data": {}},
    )


@pytest.fixture
def report():
    return ResolutionReport(
        incident_id="sentry-1",
        severity=Severity.HIGH,
        triage_summary="HIGH severity",
        root_cause="charge() 누락",
        patch_suggestion="...",
        post_mortem_draft="...",
        created_at=datetime(2026, 5, 7, 10, 0, 0),
    )


class TestBasicCrud:
    def test_get_returns_none_when_empty(self, store):
        assert store.get("nonexistent") is None

    def test_add_then_get(self, store, event):
        store.add(event)
        entry = store.get("sentry-1")
        assert entry is not None
        assert entry["incident_id"] == "sentry-1"
        assert entry["source"] == "sentry"
        assert entry["title"] == "NPE in stripe.charge"
        assert entry["status"] == IncidentStatus.PENDING
        assert entry["report"] is None

    def test_list_all_empty(self, store):
        assert store.list_all() == []

    def test_list_all_returns_added_incidents(self, store, event):
        store.add(event)
        events = store.list_all()
        assert len(events) == 1
        assert events[0]["incident_id"] == "sentry-1"


class TestStatusTransitions:
    def test_update_status_changes_state(self, store, event):
        store.add(event)
        store.update_status("sentry-1", IncidentStatus.ANALYZING)
        assert store.get("sentry-1")["status"] == IncidentStatus.ANALYZING

    def test_update_status_on_missing_id_is_noop(self, store):
        # 둘 다 raise 하지 않아야 함
        store.update_status("missing", IncidentStatus.ANALYZING)
        assert store.get("missing") is None

    def test_full_lifecycle(self, store, event, report):
        store.add(event)
        store.update_status("sentry-1", IncidentStatus.ANALYZING)
        store.save_report("sentry-1", report)
        store.update_status(
            "sentry-1", IncidentStatus.AWAITING_APPROVAL
        )
        store.update_status(
            "sentry-1", IncidentStatus.APPROVED, is_approved=True
        )

        entry = store.get("sentry-1")
        assert entry["status"] == IncidentStatus.APPROVED
        assert entry["report"] is not None
        assert entry["report"]["is_approved"] is True


class TestReportPersistence:
    def test_save_report_after_add(self, store, event, report):
        store.add(event)
        store.save_report("sentry-1", report)

        entry = store.get("sentry-1")
        assert entry["report"]["severity"] == "high"
        assert entry["report"]["root_cause"] == "charge() 누락"
        assert entry["report"]["is_approved"] is None

    def test_save_report_overwrites_previous(self, store, event, report):
        store.add(event)
        store.save_report("sentry-1", report)

        report2 = ResolutionReport(
            incident_id="sentry-1",
            severity=Severity.CRITICAL,
            triage_summary="updated",
            root_cause="updated cause",
            patch_suggestion="updated patch",
            post_mortem_draft="updated postmortem",
        )
        store.save_report("sentry-1", report2)

        entry = store.get("sentry-1")
        assert entry["report"]["severity"] == "critical"
        assert entry["report"]["root_cause"] == "updated cause"


class TestDedupe:
    def test_new_incident_has_dupe_count_one(self, store, event):
        store.add(event)
        assert store.get("sentry-1")["dupe_count"] == 1

    def test_increment_dupe_count_returns_new_value(self, store, event):
        store.add(event)
        assert store.increment_dupe_count("sentry-1") == 2
        assert store.increment_dupe_count("sentry-1") == 3
        assert store.get("sentry-1")["dupe_count"] == 3

    def test_increment_on_missing_returns_zero(self, store):
        assert store.increment_dupe_count("missing") == 0

    def test_add_resets_dupe_count(self, store, event):
        store.add(event)
        store.increment_dupe_count("sentry-1")
        store.increment_dupe_count("sentry-1")
        assert store.get("sentry-1")["dupe_count"] == 3

        store.add(event)  # 재등록
        assert store.get("sentry-1")["dupe_count"] == 1


class TestSqlitePersistence:
    """SQLite 전용 — 실제 파일에 영속되는지 별도 검증."""

    def test_data_persists_across_connections(self, tmp_path, event, report):
        db_file = str(tmp_path / "test.db")

        # 첫 인스턴스로 데이터 기록
        store1 = SqliteIncidentStore(db_file)
        store1.add(event)
        store1.save_report("sentry-1", report)
        store1.update_status("sentry-1", IncidentStatus.AWAITING_APPROVAL)

        # 새 인스턴스로 같은 파일 열기 → 데이터 보존 확인
        store2 = SqliteIncidentStore(db_file)
        entry = store2.get("sentry-1")
        assert entry is not None
        assert entry["status"] == IncidentStatus.AWAITING_APPROVAL
        assert entry["report"]["severity"] == "high"

    def test_add_clears_stale_report(self, tmp_path, event, report):
        """동일 incident_id 재등록 시 이전 report는 초기화."""
        store = SqliteIncidentStore(":memory:")
        store.add(event)
        store.save_report("sentry-1", report)
        assert store.get("sentry-1")["report"] is not None

        store.add(event)  # 재등록
        assert store.get("sentry-1")["report"] is None
