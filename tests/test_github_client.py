"""GitHub client 단위 테스트.

dry-run 백엔드의 파일 출력과 GitHubAppClient 의 REST 호출 시퀀스를 검증한다.
실 GitHub API 는 호출하지 않고 FakeHttp 로 응답을 가로챈다.
"""
import base64
import json
from datetime import datetime

import pytest

from common.models import ResolutionReport, Severity
from github.app import GitHubAppClient
from github.dry_run import DryRunGitHubClient
from github.factory import make_github_client


@pytest.fixture
def report():
    return ResolutionReport(
        incident_id="INC-TEST-001",
        severity=Severity.HIGH,
        triage_summary="결제 NPE",
        root_cause="charge() 에서 customer null 검증 누락",
        patch_suggestion="if customer is None: raise ...",
        post_mortem_draft="단/중/장기 액션",
        is_approved=True,
        created_at=datetime(2026, 5, 12, 9, 0, 0),
    )


class TestDryRun:
    def test_creates_incident_markdown_and_payload(self, report, tmp_path):
        client = DryRunGitHubClient(
            payload_log=str(tmp_path / "gh.jsonl"),
            incidents_dir=str(tmp_path / "incidents"),
        )
        result = client.create_patch_pr(report, repo="toby/demo")

        assert result.dry_run is True
        assert result.pr_number is None
        assert result.branch.startswith("warroom/incident-INC-TEST-001-")

        md = (tmp_path / "incidents" / "INC-TEST-001.md").read_text(encoding="utf-8")
        assert "# Incident INC-TEST-001" in md
        assert "HIGH" in md
        assert "charge() 에서 customer null 검증 누락" in md

        payload = json.loads((tmp_path / "gh.jsonl").read_text().splitlines()[0])
        assert payload["repo"] == "toby/demo"
        assert payload["base"] == "main"
        assert payload["head"] == result.branch
        assert payload["files"][0]["path"] == "incidents/INC-TEST-001.md"

    def test_appends_each_invocation(self, report, tmp_path):
        client = DryRunGitHubClient(
            payload_log=str(tmp_path / "gh.jsonl"),
            incidents_dir=str(tmp_path / "incidents"),
        )
        client.create_patch_pr(report, repo="toby/demo")
        client.create_patch_pr(report, repo="toby/demo")
        assert len((tmp_path / "gh.jsonl").read_text().splitlines()) == 2


class TestFactory:
    def test_returns_dry_run_when_credentials_missing(self, monkeypatch):
        monkeypatch.delenv("GITHUB_APP_ID", raising=False)
        monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY_PATH", raising=False)
        monkeypatch.delenv("GITHUB_INSTALLATION_ID", raising=False)
        client = make_github_client()
        assert isinstance(client, DryRunGitHubClient)

    def test_returns_dry_run_when_only_partial_credentials(self, monkeypatch):
        monkeypatch.setenv("GITHUB_APP_ID", "12345")
        monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY_PATH", raising=False)
        monkeypatch.delenv("GITHUB_INSTALLATION_ID", raising=False)
        client = make_github_client()
        assert isinstance(client, DryRunGitHubClient)


# --- GitHubAppClient FakeHttp ---------------------------------------------

_FAKE_KEY = """-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEAvZ7vF9z7XJzDqGqz8nLk7K7HfqJL6f8VqJfQ8X9R1Gq3kVQv
-----END RSA PRIVATE KEY-----
"""


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeHttp:
    """순서가 정해진 응답 큐를 가진 httpx.Client 대체.

    GitHubAppClient 가 보내는 호출을 calls 리스트로 모아 검증한다.
    """

    def __init__(self, responses: list[FakeResponse]):
        self._queue = list(responses)
        self.calls: list[tuple[str, str, dict, dict | None]] = []

    def _next(self) -> FakeResponse:
        return self._queue.pop(0)

    def post(self, url, headers, json=None):
        self.calls.append(("POST", url, headers, json))
        return self._next()

    def get(self, url, headers):
        self.calls.append(("GET", url, headers, None))
        return self._next()

    def put(self, url, headers, json=None):
        self.calls.append(("PUT", url, headers, json))
        return self._next()


class TestAppClient:
    def test_create_patch_pr_full_sequence(self, report, tmp_path, monkeypatch):
        # JWT 생성은 실 키가 필요하므로 monkeypatch 로 우회
        monkeypatch.setattr(
            "github.app.jwt.encode", lambda payload, key, algorithm: "fake.jwt.token"
        )

        pem = tmp_path / "key.pem"
        pem.write_text(_FAKE_KEY)

        http = FakeHttp(
            [
                FakeResponse(payload={"token": "ghs_install_token"}),         # install token
                FakeResponse(payload={"object": {"sha": "base-sha-abc"}}),    # base sha
                FakeResponse(payload={}),                                     # create branch
                FakeResponse(payload={}),                                     # put file
                FakeResponse(
                    payload={
                        "html_url": "https://github.com/toby/demo/pull/42",
                        "number": 42,
                    }
                ),  # create pr
            ]
        )
        client = GitHubAppClient(
            app_id="999",
            private_key_path=str(pem),
            installation_id="123",
            http_client=http,
        )
        result = client.create_patch_pr(report, repo="toby/demo")

        assert result.dry_run is False
        assert result.pr_number == 42
        assert result.pr_url == "https://github.com/toby/demo/pull/42"

        methods_urls = [(m, u) for m, u, _, _ in http.calls]
        assert methods_urls == [
            ("POST", "https://api.github.com/app/installations/123/access_tokens"),
            ("GET", "https://api.github.com/repos/toby/demo/git/ref/heads/main"),
            ("POST", "https://api.github.com/repos/toby/demo/git/refs"),
            ("PUT", f"https://api.github.com/repos/toby/demo/contents/incidents/{report.incident_id}.md"),
            ("POST", "https://api.github.com/repos/toby/demo/pulls"),
        ]

        # branch payload
        _, _, _, branch_body = http.calls[2]
        assert branch_body["ref"].startswith("refs/heads/warroom/incident-INC-TEST-001-")
        assert branch_body["sha"] == "base-sha-abc"

        # file commit base64 정상 인코딩
        _, _, _, put_body = http.calls[3]
        decoded = base64.b64decode(put_body["content"]).decode("utf-8")
        assert "# Incident INC-TEST-001" in decoded

        # PR body 에 분석 결과 포함
        _, _, _, pr_body = http.calls[4]
        assert "INC-TEST-001" in pr_body["title"]
        assert "charge() 에서 customer null 검증 누락" in pr_body["body"]
        assert pr_body["base"] == "main"

    def test_token_is_cached_across_calls(self, report, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "github.app.jwt.encode", lambda payload, key, algorithm: "fake.jwt.token"
        )
        pem = tmp_path / "key.pem"
        pem.write_text(_FAKE_KEY)

        # 첫 호출은 정상 5단계, 두 번째 호출은 토큰 캐시되어 4단계만
        responses = [
            FakeResponse(payload={"token": "ghs_T1"}),
            FakeResponse(payload={"object": {"sha": "sha-1"}}),
            FakeResponse(payload={}),
            FakeResponse(payload={}),
            FakeResponse(payload={"html_url": "https://x/1", "number": 1}),
            # 2회차 — install token 호출 없음
            FakeResponse(payload={"object": {"sha": "sha-2"}}),
            FakeResponse(payload={}),
            FakeResponse(payload={}),
            FakeResponse(payload={"html_url": "https://x/2", "number": 2}),
        ]
        http = FakeHttp(responses)
        client = GitHubAppClient(
            app_id="999",
            private_key_path=str(pem),
            installation_id="123",
            http_client=http,
        )
        client.create_patch_pr(report, repo="toby/demo")
        client.create_patch_pr(report, repo="toby/demo")

        install_calls = [c for c in http.calls if "access_tokens" in c[1]]
        assert len(install_calls) == 1
