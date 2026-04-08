from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class IncidentStatus(str, Enum):
    PENDING = "pending"
    ANALYZING = "analyzing"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass
class IncidentEvent:
    incident_id: str
    source: str                  # "sentry", "datadog", ...
    title: str
    raw_payload: dict[str, Any]
    received_at: datetime = field(default_factory=datetime.utcnow)
    severity: Severity | None = None
    status: IncidentStatus = IncidentStatus.PENDING


@dataclass
class ResolutionReport:
    incident_id: str
    severity: Severity
    triage_summary: str
    root_cause: str
    patch_suggestion: str
    post_mortem_draft: str
    is_approved: bool | None = None   # None = 미결정
    created_at: datetime = field(default_factory=datetime.utcnow)
