"""Notifier 백엔드 선택 팩토리.

환경변수:
    WARROOM_NOTIFIER    console | slack | both    (기본: both)
"""
import os

from .base import Notifier
from .console import ConsoleNotifier
from .slack import SlackNotifier


class _MultiNotifier(Notifier):
    """여러 Notifier에 동시에 전파하는 어댑터."""

    def __init__(self, notifiers: list[Notifier]):
        self._notifiers = notifiers

    def on_incident_received(self, event):
        for n in self._notifiers:
            n.on_incident_received(event)

    def on_agent_update(self, agent_name, message):
        for n in self._notifiers:
            n.on_agent_update(agent_name, message)

    def on_resolution_ready(self, report):
        for n in self._notifiers:
            n.on_resolution_ready(report)


def make_notifier() -> Notifier:
    backend = os.getenv("WARROOM_NOTIFIER", "both").lower()
    if backend == "console":
        return ConsoleNotifier()
    if backend == "slack":
        return SlackNotifier()
    if backend == "both":
        return _MultiNotifier([ConsoleNotifier(), SlackNotifier()])
    raise ValueError(
        f"지원하지 않는 WARROOM_NOTIFIER: {backend!r}. 사용 가능: console, slack, both"
    )
