"""Composition root 의존성.

``GitHubClient`` 인스턴스를 앱 lifecycle 동안 캐싱한다 (factory 호출 + PEM
파일 read 가 비싸므로). ``GITHUB_REPO`` 는 단일 호출 지점 (이 모듈) 만 유지
하고 캐싱하지 않는다 — services 가 env 직접 읽지 않게 boundary 통일이 목적.

env credentials 미설정 시 ``make_github_client`` 가 ``DryRunGitHubClient`` 를
돌려주므로 별도 분기 불필요. 테스트는 ``reset_github_client`` + ``monkeypatch``
로 격리.

Slack interactivity 전용 ``SlackNotifier`` 는 ``views.open`` 호출 시점에만
필요하다 (pipeline 의 SlackNotifier 와 별개 — thread 영속화 콜백 불필요).
``get_slack_notifier`` 가 lazy 캐싱.
"""

import os

from chatops.clients.slack import SlackNotifier
from github.base import GitHubClient
from github.clients.factory import make_github_client

_github_client: GitHubClient | None = None
_slack_notifier: SlackNotifier | None = None


def get_github_client() -> GitHubClient:
    """프로세스 lifecycle 동안 단일 인스턴스. 첫 호출 시 ``make_github_client`` 실행."""
    global _github_client
    if _github_client is None:
        _github_client = make_github_client()
    return _github_client


def get_github_repo() -> str | None:
    """``GITHUB_REPO`` env 값 (없으면 None).

    캐싱 안 함 — env 읽기는 싸고, 캐싱하면 테스트 격리만 복잡해진다.
    services 가 ``os.getenv`` 를 직접 호출하지 않게 boundary 통일이 목적.
    """
    return os.getenv("GITHUB_REPO")


def reset_github_client() -> None:
    """테스트 격리용 — 다음 ``get_github_client`` 호출이 factory 재실행."""
    global _github_client
    _github_client = None


def get_slack_notifier() -> SlackNotifier:
    """Slack Interactivity 경로 (views.open 등) 전용 SlackNotifier.

    Pipeline 의 SlackNotifier (chat.postMessage / thread reply / chat.update)
    와 별도 인스턴스 — interactivity 는 thread 영속화 콜백이 필요 없고,
    pipeline 은 매 incident 마다 fresh 한 notifier 를 만드는 패턴이라 공유
    이점이 없다. Token 은 ``SLACK_BOT_TOKEN`` env 에서 읽음 (미설정 시 dry-run).
    """
    global _slack_notifier
    if _slack_notifier is None:
        _slack_notifier = SlackNotifier()
    return _slack_notifier


def reset_slack_notifier() -> None:
    """테스트 격리용."""
    global _slack_notifier
    _slack_notifier = None
