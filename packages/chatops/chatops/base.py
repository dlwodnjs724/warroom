from abc import ABC, abstractmethod
from typing import Protocol

from common.models import IncidentEvent, ResolutionReport


class Notifier(ABC):
    @abstractmethod
    def on_incident_received(self, event: IncidentEvent) -> None: ...

    @abstractmethod
    def on_agent_update(self, incident_id: str, agent_name: str, message: str) -> None: ...

    @abstractmethod
    def on_resolution_ready(self, report: ResolutionReport) -> None: ...

    @abstractmethod
    def on_pipeline_failed(self, incident_id: str, error: str) -> None: ...


class InteractiveNotifier(Protocol):
    """Slack interactivity 경로 (views.open / modal trigger) 한정 contract.

    pipeline 의 ``Notifier`` 와 책임이 다르다 — interactivity 는 thread 영속화
    불필요, 대신 ``trigger_id`` 기반 modal open 만 필요. 구현체는 ``SlackNotifier``
    가 structural typing 으로 자동 매칭 (open_reject_modal 메서드만 검사).

    consumer (``services/slack_interactions.py``) 가 ``SlackNotifier`` 전체
    표면이 아닌 필요한 메서드만 의존하게 — 향후 PAT 기반 client / mock 등
    대체 구현 도입 시 contract 가 명확해진다.
    """

    def open_reject_modal(self, trigger_id: str, incident_id: str) -> None: ...
