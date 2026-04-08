"""
인메모리 인시던트 스토어.
TODO: JSON 파일 또는 RDB로 교체
"""
from common.models import IncidentEvent, IncidentStatus, ResolutionReport


class IncidentStore:
    def __init__(self):
        self._store: dict[str, dict] = {}

    def add(self, event: IncidentEvent) -> None:
        self._store[event.incident_id] = {
            "incident_id": event.incident_id,
            "source": event.source,
            "title": event.title,
            "status": IncidentStatus.PENDING,
            "report": None,
        }

    def update_status(
        self,
        incident_id: str,
        status: IncidentStatus,
        is_approved: bool | None = None,
    ) -> None:
        if incident_id in self._store:
            self._store[incident_id]["status"] = status
            if is_approved is not None and self._store[incident_id]["report"]:
                self._store[incident_id]["report"]["is_approved"] = is_approved

    def save_report(self, incident_id: str, report: ResolutionReport) -> None:
        if incident_id in self._store:
            self._store[incident_id]["report"] = {
                "severity": report.severity.value,
                "triage_summary": report.triage_summary,
                "root_cause": report.root_cause,
                "patch_suggestion": report.patch_suggestion,
                "post_mortem_draft": report.post_mortem_draft,
                "is_approved": report.is_approved,
                "created_at": report.created_at.isoformat(),
            }

    def get(self, incident_id: str) -> dict | None:
        return self._store.get(incident_id)

    def list_all(self) -> list[dict]:
        return list(self._store.values())


incident_store = IncidentStore()
