import uuid
from typing import Any
from common.models import IncidentEvent


def parse(payload: dict[str, Any]) -> IncidentEvent:
    """Sentry webhook 페이로드를 IncidentEvent로 변환."""
    # TODO: Datadog 등 추가 시 parsers/datadog.py 파일 추가
    issue = payload.get("data", {}).get("issue", payload)

    return IncidentEvent(
        incident_id=str(issue.get("id", uuid.uuid4())),
        source="sentry",
        title=issue.get("title", issue.get("culprit", "Unknown error")),
        raw_payload=payload,
    )
