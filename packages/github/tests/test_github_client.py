"""GitHub client 단위 테스트.

dry-run 백엔드의 파일 출력과 GitHubAppClient 의 REST 호출 시퀀스를 검증한다.
실 GitHub API 는 호출하지 않고 FakeHttp 로 응답을 가로챈다.
"""

import base64
import json
from datetime import datetime

import pytest
from common.clock import APP_TZ
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
        created_at=datetime(2026, 5, 12, 9, 0, 0, tzinfo=APP_TZ),
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

    def patch(self, url, headers, json=None):
        self.calls.append(("PATCH", url, headers, json))
        return self._next()

    def delete(self, url, headers):
        self.calls.append(("DELETE", url, headers, None))
        return self._next()


class TestAppClient:
    def test_create_patch_pr_full_sequence(self, report, tmp_path, monkeypatch):
        # JWT 생성은 실 키가 필요하므로 monkeypatch 로 우회
        monkeypatch.setattr("github.app.jwt.encode", lambda payload, key, algorithm: "fake.jwt.token")

        pem = tmp_path / "key.pem"
        pem.write_text(_FAKE_KEY)

        http = FakeHttp(
            [
                FakeResponse(payload={"token": "ghs_install_token"}),  # install token
                FakeResponse(payload={"object": {"sha": "base-sha-abc"}}),  # base sha
                FakeResponse(payload={}),  # create branch
                FakeResponse(payload={}),  # put file
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

    def test_apply_diff_pr_uses_git_data_api(self, tmp_path, monkeypatch):
        """patch_suggestion 에 unified diff 가 있으면 Git Data API 로 단일 commit."""
        monkeypatch.setattr("github.app.jwt.encode", lambda payload, key, algorithm: "fake.jwt.token")
        pem = tmp_path / "key.pem"
        pem.write_text(_FAKE_KEY)

        diff_report = ResolutionReport(
            incident_id="INC-DIFF-001",
            severity=Severity.HIGH,
            triage_summary="결제 NPE",
            root_cause="charge() lazy init 누락",
            patch_suggestion=(
                "```diff\n"
                "--- a/foo.py\n"
                "+++ b/foo.py\n"
                "@@ -1,3 +1,4 @@\n"
                " line1\n"
                "+inserted\n"
                " line2\n"
                " line3\n"
                "```"
            ),
            post_mortem_draft="단/중/장기",
            is_approved=True,
            created_at=datetime(2026, 5, 16, 9, 0, 0, tzinfo=APP_TZ),
        )

        base_content_b64 = base64.b64encode(b"line1\nline2\nline3\n").decode("ascii")

        http = FakeHttp(
            [
                FakeResponse(payload={"token": "ghs_install_token"}),  # install token
                FakeResponse(payload={"object": {"sha": "base-sha"}}),  # base ref
                FakeResponse(payload={"content": base_content_b64}),  # GET contents foo.py
                FakeResponse(payload={"tree": {"sha": "base-tree"}}),  # GET commit
                FakeResponse(payload={"sha": "blob-foo"}),  # POST blob foo.py
                FakeResponse(payload={"sha": "blob-md"}),  # POST blob incidents/*.md
                FakeResponse(payload={"sha": "new-tree"}),  # POST tree
                FakeResponse(payload={"sha": "new-commit"}),  # POST commit
                FakeResponse(payload={}),  # POST refs (branch)
                FakeResponse(
                    payload={
                        "html_url": "https://github.com/toby/demo/pull/99",
                        "number": 99,
                    }
                ),  # POST pulls
            ]
        )
        client = GitHubAppClient(
            app_id="999",
            private_key_path=str(pem),
            installation_id="123",
            http_client=http,
        )
        result = client.create_patch_pr(diff_report, repo="toby/demo")

        assert result.pr_number == 99
        assert result.pr_url == "https://github.com/toby/demo/pull/99"
        assert result.dry_run is False

        methods_urls = [(m, u.split("?")[0]) for m, u, _, _ in http.calls]
        assert methods_urls == [
            ("POST", "https://api.github.com/app/installations/123/access_tokens"),
            ("GET", "https://api.github.com/repos/toby/demo/git/ref/heads/main"),
            ("GET", "https://api.github.com/repos/toby/demo/contents/foo.py"),
            ("GET", "https://api.github.com/repos/toby/demo/git/commits/base-sha"),
            ("POST", "https://api.github.com/repos/toby/demo/git/blobs"),
            ("POST", "https://api.github.com/repos/toby/demo/git/blobs"),
            ("POST", "https://api.github.com/repos/toby/demo/git/trees"),
            ("POST", "https://api.github.com/repos/toby/demo/git/commits"),
            ("POST", "https://api.github.com/repos/toby/demo/git/refs"),
            ("POST", "https://api.github.com/repos/toby/demo/pulls"),
        ]

        # blob 컨텐츠가 redact 된 변경 후 파일을 담는지 sanity
        _, _, _, blob_body = http.calls[4]
        decoded = base64.b64decode(blob_body["content"]).decode("utf-8")
        assert decoded == "line1\ninserted\nline2\nline3\n"

        # tree 가 두 항목 (foo.py + incidents/*.md) 을 base_tree 위에 쌓는지
        _, _, _, tree_body = http.calls[6]
        assert tree_body["base_tree"] == "base-tree"
        paths_in_tree = sorted(entry["path"] for entry in tree_body["tree"])
        assert paths_in_tree == ["foo.py", "incidents/INC-DIFF-001.md"]

        # branch ref 가 새 commit 을 가리키는지
        _, _, _, ref_body = http.calls[8]
        assert ref_body["sha"] == "new-commit"
        assert ref_body["ref"].startswith("refs/heads/warroom/incident-INC-DIFF-001-")

    def test_falls_back_to_markdown_when_diff_apply_fails(self, tmp_path, monkeypatch):
        """diff 가 base content 와 매치 안 되면 DiffApplyError → markdown 폴백."""
        monkeypatch.setattr("github.app.jwt.encode", lambda payload, key, algorithm: "fake.jwt.token")
        pem = tmp_path / "key.pem"
        pem.write_text(_FAKE_KEY)

        bad_diff_report = ResolutionReport(
            incident_id="INC-BAD-001",
            severity=Severity.MEDIUM,
            triage_summary="t",
            root_cause="r",
            patch_suggestion=(
                "```diff\n"
                "--- a/foo.py\n"
                "+++ b/foo.py\n"
                "@@ -1,2 +1,3 @@\n"
                " expected_context\n"
                "+inserted\n"
                " other_context\n"
                "```"
            ),
            post_mortem_draft="p",
            is_approved=True,
            created_at=datetime(2026, 5, 16, 9, 0, 0, tzinfo=APP_TZ),
        )

        # base 파일 컨텐츠가 diff context 와 전혀 다름 → verify_apply 실패
        wrong_b64 = base64.b64encode(b"completely different\nlines here\n").decode("ascii")

        http = FakeHttp(
            [
                FakeResponse(payload={"token": "ghs_install_token"}),  # install token
                FakeResponse(payload={"object": {"sha": "base-sha"}}),  # base ref (diff 경로)
                FakeResponse(payload={"content": wrong_b64}),  # GET contents → mismatch
                # 여기서 DiffApplyError → markdown 폴백
                FakeResponse(payload={"object": {"sha": "base-sha"}}),  # base ref (md 경로 재호출)
                FakeResponse(payload={}),  # POST refs
                FakeResponse(payload={}),  # PUT contents
                FakeResponse(
                    payload={"html_url": "https://github.com/toby/demo/pull/77", "number": 77}
                ),  # POST pulls
            ]
        )
        client = GitHubAppClient(
            app_id="999",
            private_key_path=str(pem),
            installation_id="123",
            http_client=http,
        )
        result = client.create_patch_pr(bad_diff_report, repo="toby/demo")

        assert result.pr_number == 77

        urls = [u.split("?")[0] for _, u, _, _ in http.calls]
        # diff 경로 GET 3개 → markdown 폴백 GET/POST refs/PUT contents/POST pulls
        assert "https://api.github.com/repos/toby/demo/git/trees" not in urls
        assert "https://api.github.com/repos/toby/demo/git/blobs" not in urls
        # markdown 폴백이 PUT contents/incidents 로 떨어졌는지
        assert any("contents/incidents/INC-BAD-001.md" in u for _, u, _, _ in http.calls)

    def test_apply_diff_pr_new_file_skips_base_fetch(self, tmp_path, monkeypatch):
        """신규 파일 (--- /dev/null) 케이스 — GET contents 안 부르고 git apply 가 생성."""
        monkeypatch.setattr("github.app.jwt.encode", lambda payload, key, algorithm: "fake.jwt.token")
        pem = tmp_path / "key.pem"
        pem.write_text(_FAKE_KEY)

        new_file_report = ResolutionReport(
            incident_id="INC-NEW-001",
            severity=Severity.MEDIUM,
            triage_summary="t",
            root_cause="r",
            patch_suggestion=(
                "```diff\n"
                "--- /dev/null\n"
                "+++ b/app/new_module.py\n"
                "@@ -0,0 +1,2 @@\n"
                "+def hello():\n"
                '+    return "world"\n'
                "```"
            ),
            post_mortem_draft="pm",
            is_approved=True,
            created_at=datetime(2026, 5, 17, 9, 0, 0, tzinfo=APP_TZ),
        )

        http = FakeHttp(
            [
                FakeResponse(payload={"token": "ghs_install"}),  # install
                FakeResponse(payload={"object": {"sha": "base-sha"}}),  # base ref
                # GET contents 호출이 여기 없어야 한다 (신규파일 = base 없음)
                FakeResponse(payload={"tree": {"sha": "base-tree"}}),  # GET commit
                FakeResponse(payload={"sha": "blob-new"}),  # POST blob new_module.py
                FakeResponse(payload={"sha": "blob-md"}),  # POST blob incidents/*.md
                FakeResponse(payload={"sha": "new-tree"}),  # POST tree
                FakeResponse(payload={"sha": "new-commit"}),  # POST commit
                FakeResponse(payload={}),  # POST refs
                FakeResponse(
                    payload={"html_url": "https://github.com/toby/demo/pull/55", "number": 55}
                ),  # POST pulls
            ]
        )
        client = GitHubAppClient(
            app_id="999",
            private_key_path=str(pem),
            installation_id="123",
            http_client=http,
        )
        result = client.create_patch_pr(new_file_report, repo="toby/demo")

        assert result.pr_number == 55

        # 신규 파일이므로 GET contents 호출이 없어야 한다
        contents_gets = [c for c in http.calls if c[0] == "GET" and "/contents/" in c[1]]
        assert contents_gets == []

        # tree 에 신규 파일 + 분석 리포트만 (base 파일 fetch 없음에도 새 파일이 commit 됨)
        _, _, _, tree_body = http.calls[5]
        paths_in_tree = sorted(entry["path"] for entry in tree_body["tree"])
        assert paths_in_tree == ["app/new_module.py", "incidents/INC-NEW-001.md"]

    def test_apply_diff_pr_falls_back_when_base_file_404(self, tmp_path, monkeypatch):
        """diff 가 기존 파일을 가리키는데 GitHub 에 그 파일이 없으면 DiffApplyError → markdown 폴백."""
        monkeypatch.setattr("github.app.jwt.encode", lambda payload, key, algorithm: "fake.jwt.token")
        pem = tmp_path / "key.pem"
        pem.write_text(_FAKE_KEY)

        missing_report = ResolutionReport(
            incident_id="INC-MISSING-001",
            severity=Severity.HIGH,
            triage_summary="t",
            root_cause="r",
            patch_suggestion=(
                "```diff\n"
                "--- a/app/nonexistent.py\n"
                "+++ b/app/nonexistent.py\n"
                "@@ -1 +1,2 @@\n"
                " line\n"
                "+added\n"
                "```"
            ),
            post_mortem_draft="pm",
            is_approved=True,
            created_at=datetime(2026, 5, 17, 9, 0, 0, tzinfo=APP_TZ),
        )

        http = FakeHttp(
            [
                FakeResponse(payload={"token": "ghs_install"}),
                FakeResponse(payload={"object": {"sha": "base-sha"}}),  # base ref (diff)
                FakeResponse(status_code=404, payload={}),  # GET contents → 404
                # DiffApplyError → markdown 폴백
                FakeResponse(payload={"object": {"sha": "base-sha"}}),  # base ref (md)
                FakeResponse(payload={}),  # POST refs
                FakeResponse(payload={}),  # PUT contents
                FakeResponse(payload={"html_url": "https://github.com/toby/demo/pull/88", "number": 88}),
            ]
        )
        client = GitHubAppClient(
            app_id="999",
            private_key_path=str(pem),
            installation_id="123",
            http_client=http,
        )
        result = client.create_patch_pr(missing_report, repo="toby/demo")

        assert result.pr_number == 88
        urls = [u.split("?")[0] for _, u, _, _ in http.calls]
        # diff 경로가 Git Data API (blobs/trees/commits) 까지 못 갔는지
        assert not any("/git/blobs" in u for u in urls)
        assert not any("/git/trees" in u for u in urls)
        # markdown 폴백 흔적 — PUT contents 가 떨어졌는지
        assert any("contents/incidents/INC-MISSING-001.md" in u for _, u, _, _ in http.calls)

    def test_close_pr_patches_then_deletes_branch(self, tmp_path, monkeypatch):
        monkeypatch.setattr("github.app.jwt.encode", lambda payload, key, algorithm: "fake.jwt.token")
        pem = tmp_path / "key.pem"
        pem.write_text(_FAKE_KEY)

        http = FakeHttp(
            [
                FakeResponse(payload={"token": "ghs_install"}),  # install token
                FakeResponse(status_code=200, payload={}),  # PATCH pulls
                FakeResponse(status_code=204, payload={}),  # DELETE refs
            ]
        )
        client = GitHubAppClient(
            app_id="999",
            private_key_path=str(pem),
            installation_id="123",
            http_client=http,
        )
        client.close_pr("toby/demo", 42, "warroom/incident-X-1")

        methods_urls = [(m, u) for m, u, _, _ in http.calls]
        assert methods_urls == [
            ("POST", "https://api.github.com/app/installations/123/access_tokens"),
            ("PATCH", "https://api.github.com/repos/toby/demo/pulls/42"),
            ("DELETE", "https://api.github.com/repos/toby/demo/git/refs/heads/warroom/incident-X-1"),
        ]
        # close payload
        _, _, _, patch_body = http.calls[1]
        assert patch_body == {"state": "closed"}

    def test_close_pr_tolerates_404(self, tmp_path, monkeypatch):
        """이미 닫혀있거나 브랜치 없음 — 멱등 처리."""
        monkeypatch.setattr("github.app.jwt.encode", lambda payload, key, algorithm: "fake.jwt.token")
        pem = tmp_path / "key.pem"
        pem.write_text(_FAKE_KEY)

        http = FakeHttp(
            [
                FakeResponse(payload={"token": "ghs_install"}),
                FakeResponse(status_code=404, payload={}),  # PATCH → already closed
                FakeResponse(status_code=404, payload={}),  # DELETE → branch gone
            ]
        )
        client = GitHubAppClient(
            app_id="999",
            private_key_path=str(pem),
            installation_id="123",
            http_client=http,
        )
        client.close_pr("toby/demo", 42, "x")  # 예외 없어야 한다

    def test_token_is_cached_across_calls(self, report, tmp_path, monkeypatch):
        monkeypatch.setattr("github.app.jwt.encode", lambda payload, key, algorithm: "fake.jwt.token")
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
