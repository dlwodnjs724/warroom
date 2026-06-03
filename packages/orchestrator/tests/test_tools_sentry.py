"""Sentry Issue Lookup tool 단위 테스트.

LLM tool 표면이라 broad except → mock fallback. 검증 포인트:
- SENTRY_AUTH_TOKEN 미설정 → mock 응답
- 호출 성공 → API 응답에서 추출한 텍스트
- 호출 실패 (network / 401 / 500) → mock 폴백
- event 누락은 silent (메타만으로도 OK)
"""

import httpx
import pytest

# CrewAI tool decorator 가 래핑된 함수 — `.func` 또는 직접 호출 가능.
from orchestrator.tools.sentry import _format_issue, _mock_response, sentry_issue_lookup


def _call(issue_id: str) -> str:
    """CrewAI BaseTool 의 callable 인터페이스. ``run`` 또는 직접 호출."""
    # crewai.tools.tool 데코레이터는 BaseTool 인스턴스를 반환. ``_run`` 이 원본.
    return sentry_issue_lookup.run(issue_id=issue_id)


class TestMockFallback:
    def test_no_token_returns_mock(self, monkeypatch):
        monkeypatch.delenv("SENTRY_AUTH_TOKEN", raising=False)
        result = _call("ABC-1")
        assert "Mock 데이터" in result
        assert "ABC-1" in result

    def test_http_exception_falls_back_to_mock(self, monkeypatch):
        monkeypatch.setenv("SENTRY_AUTH_TOKEN", "dummy-token")

        class _BoomClient:
            def __init__(self, *a, **kw): ...
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, *a, **kw):
                raise httpx.ConnectError("network down")

        monkeypatch.setattr("orchestrator.tools.sentry.httpx.Client", _BoomClient)
        result = _call("XYZ-2")
        assert "Mock 데이터" in result

    def test_4xx_response_falls_back_to_mock(self, monkeypatch):
        monkeypatch.setenv("SENTRY_AUTH_TOKEN", "bad-token")

        class _Resp:
            status_code = 401

            def raise_for_status(self):
                raise httpx.HTTPStatusError("401", request=None, response=self)

            def json(self):
                return {}

        class _Client:
            def __init__(self, *a, **kw): ...
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, *a, **kw):
                return _Resp()

        monkeypatch.setattr("orchestrator.tools.sentry.httpx.Client", _Client)
        result = _call("AUTH-3")
        assert "Mock 데이터" in result


class TestRealApiHappyPath:
    def test_issue_meta_only_no_event(self, monkeypatch):
        """event 응답이 실패해도 issue 메타만으로 응답 반환."""
        monkeypatch.setenv("SENTRY_AUTH_TOKEN", "ok")

        issue_payload = {
            "id": "555",
            "title": "NullPointerException in stripe.charge",
            "culprit": "stripe.charge",
            "firstSeen": "2026-06-01T10:00:00Z",
            "lastSeen": "2026-06-03T15:00:00Z",
            "count": "847",
            "userCount": "312",
            "permalink": "https://warroom.sentry.io/issues/555/",
        }

        class _IssueResp:
            status_code = 200

            def raise_for_status(self): ...

            def json(self):
                return issue_payload

        class _EventBoom:
            status_code = 500

            def raise_for_status(self):
                raise httpx.HTTPStatusError("500", request=None, response=self)

            def json(self):
                return {}

        class _Client:
            calls: list[str] = []

            def __init__(self, *a, **kw): ...
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url, headers):
                _Client.calls.append(url)
                if url.endswith("/events/latest/"):
                    return _EventBoom()
                return _IssueResp()

        monkeypatch.setattr("orchestrator.tools.sentry.httpx.Client", _Client)
        result = _call("555")
        # mock 이 아닌 실 응답
        assert "Mock 데이터" not in result
        assert "NullPointerException in stripe.charge" in result
        assert "stripe.charge" in result  # culprit
        assert "847" in result
        assert "312" in result
        assert "https://warroom.sentry.io/issues/555/" in result

    def test_full_response_includes_stacktrace_and_tags(self, monkeypatch):
        monkeypatch.setenv("SENTRY_AUTH_TOKEN", "ok")

        issue_payload = {
            "id": "777",
            "title": "ValueError: bad input",
            "culprit": "app.payment",
            "firstSeen": "2026-06-01T10:00:00Z",
            "lastSeen": "2026-06-03T15:00:00Z",
            "count": "10",
            "userCount": "3",
        }
        event_payload = {
            "entries": [
                {
                    "type": "exception",
                    "data": {
                        "values": [
                            {
                                "type": "ValueError",
                                "value": "bad input",
                                "stacktrace": {
                                    "frames": [
                                        {
                                            "filename": "app/main.py",
                                            "function": "boot",
                                            "lineNo": 1,
                                            "inApp": True,
                                        },
                                        {
                                            "filename": "app/payment.py",
                                            "function": "charge",
                                            "lineNo": 42,
                                            "inApp": True,
                                        },
                                    ]
                                },
                            }
                        ]
                    },
                }
            ],
            "tags": [
                {"key": "release", "value": "v1.2.3"},
                {"key": "environment", "value": "production"},
            ],
        }

        class _IssueResp:
            status_code = 200

            def raise_for_status(self): ...

            def json(self):
                return issue_payload

        class _EventResp:
            status_code = 200

            def raise_for_status(self): ...

            def json(self):
                return event_payload

        class _Client:
            def __init__(self, *a, **kw): ...
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url, headers):
                if url.endswith("/events/latest/"):
                    return _EventResp()
                return _IssueResp()

        monkeypatch.setattr("orchestrator.tools.sentry.httpx.Client", _Client)
        result = _call("777")
        assert "ValueError: bad input" in result
        assert 'File "app/payment.py", line 42, in charge' in result
        assert "Release: v1.2.3" in result
        assert "Environment: production" in result


class TestFormatHelper:
    def test_format_issue_handles_minimal_payload(self):
        result = _format_issue({"id": "1"}, None)
        assert "[Sentry Issue #1]" in result
        assert "(no title)" in result

    def test_mock_response_contains_issue_id(self):
        assert "MOCK-ID" in _mock_response("MOCK-ID")


# pytest 가 이 파일을 single function 호출 패턴으로 못 알아채면 호환 위해.
@pytest.fixture(autouse=True)
def _isolate_sentry_env(monkeypatch):
    # 다른 테스트 누수 차단 — SENTRY_AUTH_TOKEN 이 누설되면 외부 호출 발생.
    monkeypatch.delenv("SENTRY_AUTH_TOKEN", raising=False)
    yield
