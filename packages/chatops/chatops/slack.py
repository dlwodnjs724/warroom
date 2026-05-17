"""Slack Bot Token 기반 Notifier — chat.postMessage / thread reply / chat.update.

환경변수:
    SLACK_BOT_TOKEN      Bot User OAuth Token (xoxb-...). 없으면 dry-run.
    SLACK_CHANNEL        메시지 대상 채널 (#name 또는 채널 ID). 기본 "#warroom-alerts".
    SLACK_DRY_RUN_LOG    Bot Token 미설정 시 페이로드 기록 경로.
                         기본: ./output/slack_payloads.jsonl

플로우:
1. `on_incident_received` 시 chat.postMessage 로 incident 알림 송신,
   응답의 (channel, ts) 를 in-memory 캐시 + (옵션) 영속화 콜백으로 기록.
2. `on_agent_update` 는 thread reply 로 진행 상황 송신.
3. `on_resolution_ready` 는 chat.update 로 원본 메시지를 분석 결과로 갱신.
4. `on_pipeline_failed` 는 thread reply 로 실패 사실 + 에러 송신.

캐시 miss (재시작 후 등) 시 `thread_lookup` 콜백으로 DB 에서 복구.

Sync API — CrewAI 워커 스레드에서 직접 호출되므로 모든 메서드는 sync.
DB 영속화는 콜백을 통해 외부 (gateway main loop) 에 위임한다.
"""

import json
import os
from collections.abc import Callable
from pathlib import Path

import httpx
from common.models import IncidentEvent, ResolutionReport
from common.redact import redact_secrets

from .base import Notifier

_SEV_EMOJI = {
    "CRITICAL": "🔥",
    "HIGH": "⚠️",
    "MEDIUM": "🟡",
    "LOW": "🟢",
}

# Slack section text 한도는 3000 자. 코드블록 펜스/라벨 여유로 보수적으로.
_RCA_LIMIT = 2500
_PATCH_LIMIT = 2500

_SLACK_API = "https://slack.com/api"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


# Persist callback signature: (incident_id, channel_id, ts) -> None
# Lookup callback signature: (incident_id) -> (channel_id, ts) | None
ThreadPersist = Callable[[str, str, str], None]
ThreadLookup = Callable[[str], tuple[str, str] | None]


class SlackNotifier(Notifier):
    def __init__(
        self,
        bot_token: str | None = None,
        channel: str | None = None,
        dry_run_log: str | None = None,
        http_client: httpx.Client | None = None,
        on_thread_persist: ThreadPersist | None = None,
        thread_lookup: ThreadLookup | None = None,
    ):
        self._token = bot_token if bot_token is not None else os.getenv("SLACK_BOT_TOKEN")
        self._channel = channel or os.getenv("SLACK_CHANNEL", "#warroom-alerts")
        self._dry_run = not self._token
        self._dry_run_log = Path(
            dry_run_log or os.getenv("SLACK_DRY_RUN_LOG", "./output/slack_payloads.jsonl")
        )
        self._http = http_client
        self._persist = on_thread_persist
        self._lookup = thread_lookup
        # incident_id → (channel_id, ts) — 같은 프로세스 내 메모리 캐시.
        self._threads: dict[str, tuple[str, str]] = {}

    # ---------- Notifier API ----------

    def on_incident_received(self, event: IncidentEvent) -> None:
        blocks = self._build_incident_blocks(event)
        result = self._post_message(self._channel, blocks, text=f"인시던트 수신: {event.title}")
        if result:
            channel_id, ts = result
            self._threads[event.incident_id] = (channel_id, ts)
            if self._persist:
                try:
                    self._persist(event.incident_id, channel_id, ts)
                except Exception as e:
                    print(f"[SlackNotifier] thread 영속화 실패: {e}")

    def on_agent_update(self, incident_id: str, agent_name: str, message: str) -> None:
        thread = self._get_thread(incident_id)
        if not thread:
            # 캐시/DB 모두 miss — 원본 메시지가 없으니 thread 도 불가. 조용히 skip.
            # (incident_received 전에 호출됐거나, dry-run 첫 호출이 incident 아닌 케이스)
            if self._dry_run:
                self._log_payload(
                    {"_type": "thread_update_skipped", "incident_id": incident_id, "agent": agent_name}
                )
            return
        channel_id, ts = thread
        text = f"*{agent_name}* — {message}"
        self._post_message(channel_id, blocks=None, text=text, thread_ts=ts)

    def on_resolution_ready(self, report: ResolutionReport) -> None:
        thread = self._get_thread(report.incident_id)
        blocks = self._build_resolution_blocks(report)
        text = f"분석 완료: {report.incident_id}"
        if thread:
            channel_id, ts = thread
            self._update_message(channel_id, ts, blocks, text=text)
        else:
            # 캐시/DB miss — 원본 갱신 불가, 새 메시지로 송신.
            self._post_message(self._channel, blocks, text=text)

    def on_pipeline_failed(self, incident_id: str, error: str) -> None:
        thread = self._get_thread(incident_id)
        text = f"❌ 파이프라인 실패 — `{error}`"
        if thread:
            channel_id, ts = thread
            self._post_message(channel_id, blocks=None, text=text, thread_ts=ts)
        else:
            self._post_message(self._channel, blocks=None, text=f"{incident_id} {text}")

    # ---------- Internal helpers ----------

    def _get_thread(self, incident_id: str) -> tuple[str, str] | None:
        cached = self._threads.get(incident_id)
        if cached:
            return cached
        if self._lookup:
            try:
                resolved = self._lookup(incident_id)
            except Exception as e:
                print(f"[SlackNotifier] thread lookup 실패: {e}")
                return None
            if resolved:
                self._threads[incident_id] = resolved
                return resolved
        return None

    def _post_message(
        self,
        channel: str,
        blocks: list[dict] | None,
        text: str,
        thread_ts: str | None = None,
    ) -> tuple[str, str] | None:
        payload: dict = {"channel": channel, "text": text}
        if blocks:
            payload["blocks"] = blocks
        if thread_ts:
            payload["thread_ts"] = thread_ts

        if self._dry_run:
            self._log_payload({"_api": "chat.postMessage", **payload})
            return None
        return self._call_slack("chat.postMessage", payload)

    def _update_message(self, channel: str, ts: str, blocks: list[dict], text: str) -> None:
        payload = {"channel": channel, "ts": ts, "text": text, "blocks": blocks}
        if self._dry_run:
            self._log_payload({"_api": "chat.update", **payload})
            return
        self._call_slack("chat.update", payload)

    def _call_slack(self, method: str, payload: dict) -> tuple[str, str] | None:
        """Slack Web API 호출. 성공 응답에서 (channel, ts) 반환 (해당되는 경우만).

        실패 시 dry-run 로 폴백 — 채널이 hooks 운영 중에도 분석 결과 유실 방지.
        """
        url = f"{_SLACK_API}/{method}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        try:
            client = self._http or httpx
            resp = client.post(url, headers=headers, json=payload, timeout=10.0)
            data = resp.json()
            if not data.get("ok"):
                err = data.get("error", "unknown")
                print(f"[SlackNotifier] {method} 실패: {err} — dry-run 로 폴백")
                self._log_payload({"_api": method, "_error": err, **payload})
                return None
            channel = data.get("channel")
            ts = data.get("ts")
            if channel and ts:
                return channel, ts
            return None
        except Exception as e:
            print(f"[SlackNotifier] {method} 예외: {e} — dry-run 로 폴백")
            self._log_payload({"_api": method, "_exception": str(e), **payload})
            return None

    def _log_payload(self, payload: dict) -> None:
        self._dry_run_log.parent.mkdir(parents=True, exist_ok=True)
        with self._dry_run_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        api = payload.get("_api", "?")
        print(f"[SlackNotifier:dry-run] {api} → {self._dry_run_log}")

    # ---------- Block builders ----------

    def _build_incident_blocks(self, event: IncidentEvent) -> list[dict]:
        return [
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

    def _build_resolution_blocks(self, report: ResolutionReport) -> list[dict]:
        sev = report.severity.value.upper()
        emoji = _SEV_EMOJI.get(sev, "")
        rca = _truncate(report.root_cause, _RCA_LIMIT)
        # patch_suggestion 은 ResolutionReport 에 raw 로 저장 → Slack 노출 직전 redact.
        # (orchestrator 가 patch 만 redact 안 한 이유: diff hunk 카운트 보존)
        patch = _truncate(redact_secrets(report.patch_suggestion), _PATCH_LIMIT)

        blocks: list[dict] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} 분석 완료 — {report.incident_id}",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Severity*\n{sev}"},
                    {"type": "mrkdwn", "text": f"*Category*\n{report.category.value}"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*근본 원인*\n```{rca}```"},
            },
        ]
        if patch:
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*패치 제안*\n```{patch}```"},
                }
            )
        blocks.append(
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
            }
        )
        return blocks
