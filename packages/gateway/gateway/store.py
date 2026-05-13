"""인시던트 영속화 — Async SQLAlchemy 2.0.

백엔드는 DATABASE_URL 환경변수로 결정 (sqlite vs mysql). 자세한 정책은
docs/plan.md 의 환경 매트릭스 참조.
"""

from datetime import datetime

from common.models import IncidentEvent, IncidentStatus, ResolutionReport
from sqlalchemy import select

from gateway.db.models import Incident, Report
from gateway.db.session import get_session_factory


class IncidentStore:
    async def add(self, event: IncidentEvent) -> None:
        """incident 를 새로 등록한다. 동일 ID 가 있으면 report 까지 초기화한다."""
        sf = get_session_factory()
        async with sf() as s:
            existing = await s.get(Incident, event.incident_id)
            if existing:
                existing_report = await s.get(Report, event.incident_id)
                if existing_report is not None:
                    await s.delete(existing_report)
                existing.source = event.source
                existing.title = event.title
                existing.status = IncidentStatus.PENDING.value
                existing.dupe_count = 1
            else:
                s.add(
                    Incident(
                        incident_id=event.incident_id,
                        source=event.source,
                        title=event.title,
                        status=IncidentStatus.PENDING.value,
                        dupe_count=1,
                    )
                )
            await s.commit()

    async def increment_dupe_count(self, incident_id: str) -> int:
        sf = get_session_factory()
        async with sf() as s:
            incident = await s.get(Incident, incident_id)
            if not incident:
                return 0
            incident.dupe_count += 1
            count = incident.dupe_count
            await s.commit()
            return count

    async def update_status(
        self,
        incident_id: str,
        status: IncidentStatus,
        is_approved: bool | None = None,
    ) -> None:
        sf = get_session_factory()
        async with sf() as s:
            incident = await s.get(Incident, incident_id)
            if not incident:
                return
            incident.status = status.value if hasattr(status, "value") else status
            if is_approved is not None:
                report = await s.get(Report, incident_id)
                if report:
                    report.is_approved = is_approved
            await s.commit()

    async def save_report(self, incident_id: str, report: ResolutionReport) -> None:
        sf = get_session_factory()
        async with sf() as s:
            existing = await s.get(Report, incident_id)
            if existing:
                existing.severity = report.severity.value
                existing.category = report.category.value
                existing.triage_summary = report.triage_summary
                existing.root_cause = report.root_cause
                existing.patch_suggestion = report.patch_suggestion
                existing.post_mortem_draft = report.post_mortem_draft
                existing.is_approved = report.is_approved
                existing.created_at = report.created_at
            else:
                s.add(
                    Report(
                        incident_id=incident_id,
                        severity=report.severity.value,
                        category=report.category.value,
                        triage_summary=report.triage_summary,
                        root_cause=report.root_cause,
                        patch_suggestion=report.patch_suggestion,
                        post_mortem_draft=report.post_mortem_draft,
                        is_approved=report.is_approved,
                        created_at=report.created_at,
                    )
                )
            await s.commit()

    async def get(self, incident_id: str) -> dict | None:
        sf = get_session_factory()
        async with sf() as s:
            incident = await s.get(Incident, incident_id)
            if not incident:
                return None
            report = await s.get(Report, incident_id)
            return _incident_to_dict(incident, report)

    async def list_all(self) -> list[dict]:
        sf = get_session_factory()
        async with sf() as s:
            rows = (await s.execute(select(Incident).order_by(Incident.incident_id))).scalars().all()
            output: list[dict] = []
            for incident in rows:
                report = await s.get(Report, incident.incident_id)
                output.append(_incident_to_dict(incident, report))
            return output


def _incident_to_dict(incident: Incident, report: Report | None) -> dict:
    return {
        "incident_id": incident.incident_id,
        "source": incident.source,
        "title": incident.title,
        "status": IncidentStatus(incident.status),
        "dupe_count": incident.dupe_count,
        "report": _report_to_dict(report) if report else None,
    }


def _report_to_dict(report: Report) -> dict:
    return {
        "severity": report.severity,
        "category": report.category,
        "triage_summary": report.triage_summary,
        "root_cause": report.root_cause,
        "patch_suggestion": report.patch_suggestion,
        "post_mortem_draft": report.post_mortem_draft,
        "is_approved": report.is_approved,
        "created_at": (
            report.created_at.isoformat() if isinstance(report.created_at, datetime) else report.created_at
        ),
    }


_store: IncidentStore | None = None


def get_store() -> IncidentStore:
    global _store
    if _store is None:
        _store = IncidentStore()
    return _store


def reset_store() -> None:
    """테스트 격리용 — 캐시된 store 객체를 폐기한다."""
    global _store
    _store = None
