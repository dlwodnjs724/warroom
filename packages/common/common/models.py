from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from common.clock import now


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class IncidentCategory(str, Enum):
    """장애의 성격 — Fixer 의 PR 생성 여부를 결정한다."""
    CODE = "code"               # 애플리케이션 코드 결함 → 패치 PR 가능
    INFRA = "infra"             # 인프라/배포 (k8s OOM, DB down 등) → 운영 대응
    EXTERNAL = "external"       # 외부 의존성 장애 (3rd-party API 등)
    OPERATIONAL = "operational" # 설정/운영 실수 (config 오설정 등)


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
    received_at: datetime = field(default_factory=now)
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
    category: IncidentCategory = IncidentCategory.CODE
    is_approved: bool | None = None   # None = 미결정
    created_at: datetime = field(default_factory=now)
