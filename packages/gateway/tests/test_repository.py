"""IncidentRepository (Async SQLAlchemy) 단위 테스트.

conftest 의 _isolate_db (autouse) + schema fixture 가 in-memory SQLite 에
스키마를 생성한다. 각 테스트는 격리된 fresh DB 를 받는다.
"""

from datetime import datetime

import pytest
from common.clock import APP_TZ
from common.models import (
    IncidentCategory,
    IncidentEvent,
    IncidentStatus,
    ResolutionReport,
    Severity,
)
from gateway.infrastructure.db.repository import IncidentRepository


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
        category=IncidentCategory.CODE,
        triage_summary="HIGH severity",
        root_cause="charge() 누락",
        patch_suggestion="...",
        post_mortem_draft="...",
        created_at=datetime(2026, 5, 7, 10, 0, 0, tzinfo=APP_TZ),
    )


@pytest.fixture
def repo():
    return IncidentRepository()


class TestBasicCrud:
    async def test_get_returns_none_when_empty(self, schema, repo):
        assert await repo.get("nonexistent") is None

    async def test_add_then_get(self, schema, repo, event):
        await repo.add(event)
        entry = await repo.get("sentry-1")
        assert entry is not None
        assert entry["incident_id"] == "sentry-1"
        assert entry["source"] == "sentry"
        assert entry["title"] == "NPE in stripe.charge"
        assert entry["status"] == IncidentStatus.PENDING
        assert entry["report"] is None
        assert entry["dupe_count"] == 1

    async def test_list_all_empty(self, schema, repo):
        assert await repo.list_all() == []

    async def test_list_all_returns_added_incidents(self, schema, repo, event):
        await repo.add(event)
        events = await repo.list_all()
        assert len(events) == 1
        assert events[0]["incident_id"] == "sentry-1"


class TestStatusTransitions:
    async def test_update_status_changes_state(self, schema, repo, event):
        await repo.add(event)
        await repo.update_status("sentry-1", IncidentStatus.ANALYZING)
        entry = await repo.get("sentry-1")
        assert entry["status"] == IncidentStatus.ANALYZING

    async def test_update_status_on_missing_id_is_noop(self, schema, repo):
        await repo.update_status("missing", IncidentStatus.ANALYZING)
        assert await repo.get("missing") is None

    async def test_full_lifecycle(self, schema, repo, event, report):
        await repo.add(event)
        await repo.update_status("sentry-1", IncidentStatus.ANALYZING)
        await repo.save_report("sentry-1", report)
        await repo.update_status("sentry-1", IncidentStatus.AWAITING_APPROVAL)
        await repo.update_status("sentry-1", IncidentStatus.APPROVED, is_approved=True)

        entry = await repo.get("sentry-1")
        assert entry["status"] == IncidentStatus.APPROVED
        assert entry["report"] is not None
        assert entry["report"]["is_approved"] is True


class TestReportPersistence:
    async def test_save_report_after_add(self, schema, repo, event, report):
        await repo.add(event)
        await repo.save_report("sentry-1", report)

        entry = await repo.get("sentry-1")
        assert entry["report"]["severity"] == "high"
        assert entry["report"]["category"] == "code"
        assert entry["report"]["root_cause"] == "charge() 누락"
        assert entry["report"]["is_approved"] is None

    async def test_save_report_overwrites_previous(self, schema, repo, event, report):
        await repo.add(event)
        await repo.save_report("sentry-1", report)

        report2 = ResolutionReport(
            incident_id="sentry-1",
            severity=Severity.CRITICAL,
            category=IncidentCategory.CODE,
            triage_summary="updated",
            root_cause="updated cause",
            patch_suggestion="updated patch",
            post_mortem_draft="updated postmortem",
        )
        await repo.save_report("sentry-1", report2)

        entry = await repo.get("sentry-1")
        assert entry["report"]["severity"] == "critical"
        assert entry["report"]["root_cause"] == "updated cause"


class TestDedupe:
    async def test_new_incident_has_dupe_count_one(self, schema, repo, event):
        await repo.add(event)
        entry = await repo.get("sentry-1")
        assert entry["dupe_count"] == 1

    async def test_increment_dupe_count_returns_new_value(self, schema, repo, event):
        await repo.add(event)
        assert await repo.increment_dupe_count("sentry-1") == 2
        assert await repo.increment_dupe_count("sentry-1") == 3
        entry = await repo.get("sentry-1")
        assert entry["dupe_count"] == 3

    async def test_increment_on_missing_returns_zero(self, schema, repo):
        assert await repo.increment_dupe_count("missing") == 0

    async def test_add_resets_dupe_count(self, schema, repo, event):
        await repo.add(event)
        await repo.increment_dupe_count("sentry-1")
        await repo.increment_dupe_count("sentry-1")
        entry = await repo.get("sentry-1")
        assert entry["dupe_count"] == 3

        await repo.add(event)
        entry2 = await repo.get("sentry-1")
        assert entry2["dupe_count"] == 1


class TestAddClearsStaleReport:
    async def test_add_clears_stale_report(self, schema, repo, event, report):
        await repo.add(event)
        await repo.save_report("sentry-1", report)
        entry = await repo.get("sentry-1")
        assert entry["report"] is not None

        await repo.add(event)
        entry2 = await repo.get("sentry-1")
        assert entry2["report"] is None
