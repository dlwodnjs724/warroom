"""Slack Incoming Webhook 기반 Notifier.

환경변수:
    SLACK_WEBHOOK_URL    실제 Slack 채널의 Incoming Webhook URL
    SLACK_DRY_RUN_LOG    URL 미설정 시 페이로드를 기록할 파일 경로
                         (기본: ./output/slack_payloads.jsonl)

URL이 설정되어 있으면 실제로 POST 하고, 없으면 dry-run 모드로
Block Kit 페이로드를 JSON Lines 파일에 누적 기록한다. 두 경우 모두
콘솔에 송신/기록 사실을 한 줄 남겨 개발 가시성을 유지한다.
"""
import json
import os
from pathlib import Path

import httpx

from common.models import IncidentEvent, ResolutionReport

from .base import Notifier


_SEV_EMOJI = {
    "CRITICAL": "🔥",
    "HIGH": "⚠️",
    "MEDIUM": "🟡",
    "LOW": "🟢",
}


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


class SlackNotifier(Notifier):
    def __init__(
        self,
        webhook_url: str | None = None,
        dry_run_log: str | None = None,
        http_client: httpx.Client | None = None,
    ):
        self._webhook_url = webhook_url or os.getenv("SLACK_WEBHOOK_URL")
        self._dry_run = self._webhook_url is None
        self._dry_run_log = Path(
            dry_run_log or os.getenv("SLACK_DRY_RUN_LOG", "./output/slack_payloads.jsonl")
        )
        self._http = http_client

    def on_incident_received(self, event: IncidentEvent) -> None:
        self._send(self._build_incident_message(event))

    def on_agent_update(self, agent_name: str, message: str) -> None:
        # 진행 업데이트는 Slack에 매번 보내면 채널 도배가 되므로 생략.
        # ConsoleNotifier와 함께 사용 시 콘솔에서 진행 상황을 확인.
        return

    def on_resolution_ready(self, report: ResolutionReport) -> None:
        self._send(self._build_resolution_message(report))

    def _build_incident_message(self, event: IncidentEvent) -> dict:
        return {
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": f"🚨 인시던트 수신 — {event.incident_id}"},
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Source*\n{event.source.upper()}"},
                        {"type": "mrkdwn", "text": f"*Title*\n{event.title}"},
                    ],
                },
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": "AI 에이전트 분석 시작..."}],
                },
            ]
        }

    def _build_resolution_message(self, report: ResolutionReport) -> dict:
        sev = report.severity.value.upper()
        emoji = _SEV_EMOJI.get(sev, "")
        rca = _truncate(report.root_cause, 800)
        patch = _truncate(report.patch_suggestion, 1500)

        return {
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"{emoji} 분석 완료 — {report.incident_id}",
                    },
                },
                {
                    "type": "section",
                    "fields": [{"type": "mrkdwn", "text": f"*Severity*\n{sev}"}],
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*근본 원인*\n```{rca}```"},
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*패치 제안*\n```{patch}```"},
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "✅ Approve"},
                            "style": "primary",
                            "action_id": "warroom_approve",
                            "value": report.incident_id,
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "❌ Reject"},
                            "style": "danger",
                            "action_id": "warroom_reject",
                            "value": report.incident_id,
                        },
                    ],
                },
            ]
        }

    def _send(self, payload: dict) -> None:
        if self._dry_run:
            self._log_payload(payload)
            return
        try:
            client = self._http or httpx
            resp = client.post(self._webhook_url, json=payload, timeout=10.0)
            resp.raise_for_status()
            print(f"[SlackNotifier] 송신 완료 ({len(payload['blocks'])} blocks)")
        except Exception as e:
            print(f"[SlackNotifier] 송신 실패: {e} — dry-run 로그로 폴백")
            self._log_payload(payload)

    def _log_payload(self, payload: dict) -> None:
        self._dry_run_log.parent.mkdir(parents=True, exist_ok=True)
        with self._dry_run_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        print(f"[SlackNotifier:dry-run] {len(payload['blocks'])} blocks → {self._dry_run_log}")
