"""Composition root — gateway 의 모든 외부 어댑터 wiring + env credential 읽기 단일 지점 (§ 4c).

services / api 는 이 모듈의 getter / factory 만 호출하고 ``os.getenv`` /
어댑터 factory 를 직접 부르지 않는다.

캐싱 정책 — 책임별 분리:

- **lazy singleton** (``get_github_client`` / ``get_slack_notifier``): 비싼
  factory (PEM 파일 read, httpx client) 를 lifecycle 동안 1회만 호출. 테스트
  격리는 ``reset_X``.
- **매 호출 env read** (``get_github_repo`` / ``get_*_secret`` /
  ``get_*_token``): ``os.getenv`` wrapper. 캐싱하면 테스트 격리만 복잡해진다
  (env getter 호출 비용은 무시 가능).
- **factory wrapper** (``make_pipeline_notifier``): 매 incident fresh 인스턴스
  가 의도 — thread 영속화 콜백이 module-level 함수에 묶여 매번 wiring 필요.
  캐싱 없음.

env credential 미설정 시 dev 폴백은 각 factory 책임 (``make_github_client``
가 ``DryRunGitHubClient``, ``SlackNotifier`` 가 token 없으면 dry-run print).
"""

import os

import sentry_sdk
from chatops.base import InteractiveNotifier, Notifier
from chatops.clients.factory import make_notifier
from chatops.clients.slack import SlackNotifier, ThreadLookup, ThreadPersist
from github.base import GitHubClient
from github.clients.factory import make_github_client

_github_client: GitHubClient | None = None
_slack_notifier: SlackNotifier | None = None
_sentry_initialized: bool = False


def init_sentry() -> bool:
    """Warroom 자체의 self-monitoring — 본인 에러를 Sentry 로 capture.

    Dogfooding: AIOps 도구가 자기 자신에게도 적용된다. ``SENTRY_DSN`` 미설정
    시 no-op (테스트 / dev 안전). idempotent — 두 번째 호출은 skip.

    환경:
        SENTRY_DSN                  : 없으면 disabled
        SENTRY_ENVIRONMENT          : 기본 "development"
        SENTRY_TRACES_SAMPLE_RATE   : 기본 0.0 (성능 trace 비활성)
    """
    global _sentry_initialized
    if _sentry_initialized:
        return True
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("SENTRY_ENVIRONMENT", "development"),
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0")),
        # 단순 error capture 만 — session pings 는 시연 노이즈.
        auto_session_tracking=False,
    )
    _sentry_initialized = True
    return True


def reset_sentry() -> None:
    """테스트 격리용 — 다음 ``init_sentry`` 호출이 다시 활성화 가능하도록."""
    global _sentry_initialized
    _sentry_initialized = False


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


def get_sentry_secret() -> str | None:
    """``SENTRY_CLIENT_SECRET`` env 값. 캐싱 안 함 (``get_github_repo`` 와 동일 패턴)."""
    return os.getenv("SENTRY_CLIENT_SECRET")


def get_datadog_token() -> str | None:
    """``WARROOM_DATADOG_TOKEN`` env 값. 캐싱 안 함."""
    return os.getenv("WARROOM_DATADOG_TOKEN")


def get_slack_signing_secret() -> str | None:
    """``SLACK_SIGNING_SECRET`` env 값. 캐싱 안 함."""
    return os.getenv("SLACK_SIGNING_SECRET")


def reset_github_client() -> None:
    """테스트 격리용 — 다음 ``get_github_client`` 호출이 factory 재실행."""
    global _github_client
    _github_client = None


def get_slack_notifier() -> InteractiveNotifier:
    """Slack Interactivity 경로 (views.open 등) 전용 ``InteractiveNotifier``.

    Pipeline 의 ``Notifier`` (chat.postMessage / thread reply / chat.update)
    와 별도 인스턴스 — interactivity 는 thread 영속화 콜백이 필요 없고,
    pipeline 은 매 incident 마다 fresh 한 notifier 를 만드는 패턴이라 공유
    이점이 없다. Token 은 ``SLACK_BOT_TOKEN`` env 에서 읽음 (미설정 시 dry-run).

    반환 contract 는 ``InteractiveNotifier`` (Protocol) — consumer 가 필요한
    표면 (현재 ``open_reject_modal`` 만) 만 의존. 실 구현체는 ``SlackNotifier``
    가 structural typing 으로 자동 매칭.
    """
    global _slack_notifier
    if _slack_notifier is None:
        _slack_notifier = SlackNotifier()
    return _slack_notifier


def reset_slack_notifier() -> None:
    """테스트 격리용."""
    global _slack_notifier
    _slack_notifier = None


def make_pipeline_notifier(
    persist_cb: ThreadPersist | None = None,
    lookup_cb: ThreadLookup | None = None,
) -> Notifier:
    """Pipeline 실행 시점에 호출되는 fresh ``Notifier`` factory.

    pipeline.py 의 ``_persist_slack_thread`` / ``_lookup_slack_thread`` 는
    sync 콜백이라 매 incident 마다 wiring 이 필요. 캐싱 없음 (의도) — 매
    incident 마다 새 인스턴스가 thread 영속화 상태를 독립적으로 들고 간다.

    services 가 ``chatops.clients.factory.make_notifier`` 를 직접 import 하지
    않게 한 단계 wrapping — § layering.md § 4c.
    """
    return make_notifier(persist_cb=persist_cb, lookup_cb=lookup_cb)
