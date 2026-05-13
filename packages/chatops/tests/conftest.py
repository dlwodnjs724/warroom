"""chatops 테스트 — Slack 관련 env 변수 자동 격리.

실제 `.env` 에 SLACK_BOT_TOKEN 이 있으면 dry-run 테스트가 실 송신으로 전환되어
누설 위험. 매 테스트마다 명시적으로 비운다.
"""

import pytest


@pytest.fixture(autouse=True)
def _clear_slack_env(monkeypatch):
    for var in ("SLACK_BOT_TOKEN", "SLACK_CHANNEL", "SLACK_DRY_RUN_LOG"):
        monkeypatch.delenv(var, raising=False)
