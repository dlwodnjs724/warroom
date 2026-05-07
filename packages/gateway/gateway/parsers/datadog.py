"""Datadog webhook 페이로드 파서.

Datadog Monitor / Incident webhook 두 형태 모두를 IncidentEvent로 변환한다.

Monitor webhook 예시 페이로드:
    {
      "id": "1234567890",
      "title": "[Triggered] CPU usage > 90%",
      "alert_type": "error",
      "body": "...",
      "event_type": "alert",
      "tags": "service:payment-service,env:prod",
      "url": "https://app.datadoghq.com/event/event?id=1234567890"
    }

Incident webhook 예시 페이로드:
    {
      "incident_public_id": "abc-123",
      "incident_severity": "SEV-2",
      "incident_title": "Payment service degradation",
      "incident_url": "https://app.datadoghq.com/incidents/...",
      "incident_state": "active"
    }
"""
import uuid
from typing import Any

from common.models import IncidentEvent


def parse(payload: dict[str, Any]) -> IncidentEvent:
    """Datadog webhook 페이로드를 IncidentEvent로 변환."""
    if "incident_public_id" in payload:
        return _parse_incident(payload)
    return _parse_monitor(payload)


def _parse_monitor(payload: dict[str, Any]) -> IncidentEvent:
    incident_id = str(payload.get("id") or payload.get("alert_id") or uuid.uuid4())
    title = payload.get("title") or payload.get("alert_title") or "Datadog alert"
    return IncidentEvent(
        incident_id=incident_id,
        source="datadog",
        title=title,
        raw_payload=payload,
    )


def _parse_incident(payload: dict[str, Any]) -> IncidentEvent:
    incident_id = str(payload.get("incident_public_id") or uuid.uuid4())
    title = payload.get("incident_title") or "Datadog incident"
    return IncidentEvent(
        incident_id=incident_id,
        source="datadog",
        title=title,
        raw_payload=payload,
    )
