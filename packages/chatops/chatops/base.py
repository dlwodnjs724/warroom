from abc import ABC, abstractmethod
from common.models import IncidentEvent, ResolutionReport


class Notifier(ABC):
    @abstractmethod
    def on_incident_received(self, event: IncidentEvent) -> None: ...

    @abstractmethod
    def on_agent_update(self, agent_name: str, message: str) -> None: ...

    @abstractmethod
    def on_resolution_ready(self, report: ResolutionReport) -> None: ...
