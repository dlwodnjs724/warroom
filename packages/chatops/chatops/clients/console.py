from common.models import IncidentEvent, ResolutionReport

from ..base import Notifier


class ConsoleNotifier(Notifier):
    def on_incident_received(self, event: IncidentEvent) -> None:
        print(f"\n{'=' * 60}")
        print(f"[WARROOM] 인시던트 수신: {event.incident_id}")
        print(f"  소스   : {event.source.upper()}")
        print(f"  제목   : {event.title}")
        print(f"{'=' * 60}\n")

    def on_agent_update(self, incident_id: str, agent_name: str, message: str) -> None:
        print(f"[{agent_name}] ({incident_id}) {message}")

    def on_resolution_ready(self, report: ResolutionReport) -> None:
        print(f"\n{'=' * 60}")
        print(f"[WARROOM] 분석 완료 — 인시던트 {report.incident_id}")
        print(f"{'=' * 60}")
        print(f"\n[심각도] {report.severity.value.upper()}")
        print(f"\n[트리아지 요약]\n{report.triage_summary}")
        print(f"\n[근본 원인]\n{report.root_cause}")
        print(f"\n[패치 제안]\n{report.patch_suggestion}")
        print(f"\n[포스트모템 초안]\n{report.post_mortem_draft}")
        print(f"\n{'=' * 60}\n")

    def on_pipeline_failed(self, incident_id: str, error: str) -> None:
        print(f"\n[WARROOM] ❌ 파이프라인 실패 — {incident_id}: {error}\n")
