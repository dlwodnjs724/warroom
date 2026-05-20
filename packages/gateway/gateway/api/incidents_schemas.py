"""``/incidents`` 라우터의 HTTP request/response DTO.

라우터 (``api/incidents.py``) 는 wiring 만, schema 정의는 본 모듈에 격리.
도메인 모델 (``common.models``) 과 다르다 — common 은 패키지 전체가 import
하는 도메인 객체, 본 모듈은 HTTP 표면에서만 쓰는 직렬화 형식.

새 endpoint 추가 시: ``api/<domain>.py`` 라우터 ↔ ``api/<domain>_schemas.py``
DTO 쌍으로 묶는다 (layering.md § 6 naming).
"""

from pydantic import BaseModel


class RejectBody(BaseModel):
    """``POST /incidents/{id}/reject`` 의 optional body.

    사유 캡쳐는 Slack modal (Phase 3.4) 이 메인 경로지만, HTTP 호출자도
    동일 입력을 제공할 수 있도록 받는다. body 없는 호출도 그대로 작동.
    """

    rejection_reason: str | None = None
