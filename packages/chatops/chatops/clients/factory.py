"""Notifier 백엔드 선택 팩토리.

환경변수:
    WARROOM_NOTIFIER    console | slack | both    (기본: both)

Slack 스레드 영속화 콜백 (persist_cb, lookup_cb) 은 sync wrapper 로
SlackNotifier 에 주입되어, sync 한 SlackNotifier 가 async store 를 호출할 수
있게 한다. 미지정 시 in-memory 캐시만 사용.
"""

import os

from ..base import Notifier
from .console import ConsoleNotifier
from .slack import SlackNotifier, ThreadLookup, ThreadPersist


class _MultiNotifier(Notifier):
    """여러 Notifier에 동시에 전파하는 어댑터."""

    def __init__(self, notifiers: list[Notifier]):
        self._notifiers = notifiers

    def on_incident_received(self, event):
        for n in self._notifiers:
            n.on_incident_received(event)

    def on_agent_update(self, incident_id, agent_name, message):
        for n in self._notifiers:
            n.on_agent_update(incident_id, agent_name, message)

    def on_resolution_ready(self, report):
        for n in self._notifiers:
            n.on_resolution_ready(report)

    def on_pipeline_failed(self, incident_id, error):
        for n in self._notifiers:
            n.on_pipeline_failed(incident_id, error)


def make_notifier(
    persist_cb: ThreadPersist | None = None,
    lookup_cb: ThreadLookup | None = None,
) -> Notifier:
    backend = os.getenv("WARROOM_NOTIFIER", "both").lower()
    slack = SlackNotifier(on_thread_persist=persist_cb, thread_lookup=lookup_cb)
    if backend == "console":
        return ConsoleNotifier()
    if backend == "slack":
        return slack
    if backend == "both":
        return _MultiNotifier([ConsoleNotifier(), slack])
    raise ValueError(f"지원하지 않는 WARROOM_NOTIFIER: {backend!r}. 사용 가능: console, slack, both")
