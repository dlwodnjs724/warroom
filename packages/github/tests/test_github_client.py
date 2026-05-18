"""GitHub client 단위 테스트.

세 가지 seam 을 분리 검증:

1. ``DryRunGitHubClient`` — transport primitive 호출이 JSONL 페이로드 +
   incidents/<id>.md 파일로 잘 떨어지는지.
2. ``GitHubAppClient`` — transport primitive 별 REST 시퀀스 (FakeHttp).
3. ``pr_builder.build_patch_pr`` — diff 경로 / markdown 폴백 정책 (FakeClient
   in-memory). REST 디테일에 묶이지 않게 ``GitHubClient`` Protocol 만 의존.
"""

import base64
import json
from datetime import datetime

import pytest
from common.clock import APP_TZ
from common.models import ResolutionReport, Severity
from github.base import GitHubAuthError, GitHubClient, GitHubError, GitHubTransientError
from github.clients.app import GitHubAppClient
from github.clients.dry_run import DryRunGitHubClient
from github.clients.factory import make_github_client
from github.pr_builder import build_patch_pr


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
    def test_commit_files_writes_incident_md_and_payload(self, report, tmp_path):
        client = DryRunGitHubClient(
            payload_log=str(tmp_path / "gh.jsonl"),
            incidents_dir=str(tmp_path / "incidents"),
        )
        result = build_patch_pr(client, report, repo="toby/demo")

        assert result.dry_run is True
        assert result.pr_number is None
        assert result.branch.startswith("warroom/incident-INC-TEST-001-")

        md = (tmp_path / "incidents" / "INC-TEST-001.md").read_text(encoding="utf-8")
        assert "# Incident INC-TEST-001" in md
        assert "HIGH" in md
        assert "charge() 에서 customer null 검증 누락" in md

        lines = (tmp_path / "gh.jsonl").read_text().splitlines()
        actions = [json.loads(line)["action"] for line in lines]
        assert actions == ["commit_files", "open_pr"]
        commit = json.loads(lines[0])
        assert commit["repo"] == "toby/demo"
        assert commit["base"] == "main"
        assert commit["branch"] == result.branch
        assert commit["files"][0]["path"] == "incidents/INC-TEST-001.md"

    def test_appends_each_invocation(self, report, tmp_path):
        client = DryRunGitHubClient(
            payload_log=str(tmp_path / "gh.jsonl"),
            incidents_dir=str(tmp_path / "incidents"),
        )
        build_patch_pr(client, report, repo="toby/demo")
        build_patch_pr(client, report, repo="toby/demo")
        # 매 호출이 commit_files + open_pr 두 줄 → 총 4 줄
        assert len((tmp_path / "gh.jsonl").read_text().splitlines()) == 4

    def test_close_pr_records_action(self, tmp_path):
        client = DryRunGitHubClient(
            payload_log=str(tmp_path / "gh.jsonl"),
            incidents_dir=str(tmp_path / "incidents"),
        )
        client.close_pr("toby/demo", 99, "warroom/incident-X")
        line = json.loads((tmp_path / "gh.jsonl").read_text().splitlines()[0])
        assert line == {
            "action": "close_pr",
            "repo": "toby/demo",
            "pr_number": 99,
            "branch": "warroom/incident-X",
        }

    def test_delete_branch_records_action(self, tmp_path):
        client = DryRunGitHubClient(
            payload_log=str(tmp_path / "gh.jsonl"),
            incidents_dir=str(tmp_path / "incidents"),
        )
        client.delete_branch("toby/demo", "warroom/orphan-Y")
        line = json.loads((tmp_path / "gh.jsonl").read_text().splitlines()[0])
        assert line == {
            "action": "delete_branch",
            "repo": "toby/demo",
            "branch": "warroom/orphan-Y",
        }


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


def _make_app_client(tmp_path, monkeypatch, http: FakeHttp) -> GitHubAppClient:
    monkeypatch.setattr("github.clients.app.jwt.encode", lambda payload, key, algorithm: "fake.jwt.token")
    pem = tmp_path / "key.pem"
    pem.write_text(_FAKE_KEY)
    return GitHubAppClient(
        app_id="999",
        private_key_path=str(pem),
        installation_id="123",
        http_client=http,
    )


class TestAppClientPrimitives:
    def test_get_file_content_decodes_base64(self, tmp_path, monkeypatch):
        content_b64 = base64.b64encode(b"hello\nworld\n").decode("ascii")
        http = FakeHttp(
            [
                FakeResponse(payload={"token": "ghs"}),
                FakeResponse(payload={"content": content_b64}),
            ]
        )
        client = _make_app_client(tmp_path, monkeypatch, http)
        assert client.get_file_content("toby/demo", "foo.py", "main") == "hello\nworld\n"
        assert http.calls[1][0] == "GET"
        assert "contents/foo.py" in http.calls[1][1]

    def test_get_file_content_404_raises_filenotfound(self, tmp_path, monkeypatch):
        http = FakeHttp(
            [
                FakeResponse(payload={"token": "ghs"}),
                FakeResponse(status_code=404, payload={}),
            ]
        )
        client = _make_app_client(tmp_path, monkeypatch, http)
        with pytest.raises(FileNotFoundError):
            client.get_file_content("toby/demo", "missing.py", "main")

    def test_get_file_content_url_encodes_spaces(self, tmp_path, monkeypatch):
        content_b64 = base64.b64encode(b"x\n").decode("ascii")
        http = FakeHttp(
            [
                FakeResponse(payload={"token": "ghs"}),
                FakeResponse(payload={"content": content_b64}),
            ]
        )
        client = _make_app_client(tmp_path, monkeypatch, http)
        client.get_file_content("toby/demo", "path with space.py", "main")
        url = http.calls[1][1]
        assert "path%20with%20space.py" in url
        assert " " not in url

    def test_commit_files_uses_git_data_api(self, tmp_path, monkeypatch):
        http = FakeHttp(
            [
                FakeResponse(payload={"token": "ghs"}),
                FakeResponse(payload={"object": {"sha": "base-sha"}}),
                FakeResponse(payload={"tree": {"sha": "base-tree"}}),
                FakeResponse(payload={"sha": "blob-a"}),
                FakeResponse(payload={"sha": "blob-b"}),
                FakeResponse(payload={"sha": "new-tree"}),
                FakeResponse(payload={"sha": "new-commit"}),
                FakeResponse(payload={}),
            ]
        )
        client = _make_app_client(tmp_path, monkeypatch, http)
        client.commit_files(
            repo="toby/demo",
            branch="warroom/incident-X",
            base_branch="main",
            files={"a.py": "aa\n", "b.py": "bb\n"},
            message="fix: x",
        )
        methods_urls = [(m, u.split("?")[0]) for m, u, _, _ in http.calls]
        assert methods_urls == [
            ("POST", "https://api.github.com/app/installations/123/access_tokens"),
            ("GET", "https://api.github.com/repos/toby/demo/git/ref/heads/main"),
            ("GET", "https://api.github.com/repos/toby/demo/git/commits/base-sha"),
            ("POST", "https://api.github.com/repos/toby/demo/git/blobs"),
            ("POST", "https://api.github.com/repos/toby/demo/git/blobs"),
            ("POST", "https://api.github.com/repos/toby/demo/git/trees"),
            ("POST", "https://api.github.com/repos/toby/demo/git/commits"),
            ("POST", "https://api.github.com/repos/toby/demo/git/refs"),
        ]
        # tree 가 base 위에 쌓이는지
        _, _, _, tree_body = http.calls[5]
        assert tree_body["base_tree"] == "base-tree"
        paths_in_tree = sorted(e["path"] for e in tree_body["tree"])
        assert paths_in_tree == ["a.py", "b.py"]
        # branch ref 가 새 commit 을 가리키는지
        _, _, _, ref_body = http.calls[7]
        assert ref_body["sha"] == "new-commit"
        assert ref_body["ref"] == "refs/heads/warroom/incident-X"

    def test_open_pr_returns_response_dict(self, tmp_path, monkeypatch):
        http = FakeHttp(
            [
                FakeResponse(payload={"token": "ghs"}),
                FakeResponse(payload={"html_url": "https://x/1", "number": 7}),
            ]
        )
        client = _make_app_client(tmp_path, monkeypatch, http)
        pr = client.open_pr(
            repo="toby/demo",
            branch="warroom/x",
            base_branch="main",
            title="t",
            body="b",
        )
        assert pr == {"html_url": "https://x/1", "number": 7}
        assert http.calls[1][0] == "POST"
        assert http.calls[1][1] == "https://api.github.com/repos/toby/demo/pulls"

    def test_close_pr_patches_then_deletes_branch(self, tmp_path, monkeypatch):
        http = FakeHttp(
            [
                FakeResponse(payload={"token": "ghs_install"}),
                FakeResponse(status_code=200, payload={}),
                FakeResponse(status_code=204, payload={}),
            ]
        )
        client = _make_app_client(tmp_path, monkeypatch, http)
        client.close_pr("toby/demo", 42, "warroom/incident-X-1")
        methods_urls = [(m, u) for m, u, _, _ in http.calls]
        assert methods_urls == [
            ("POST", "https://api.github.com/app/installations/123/access_tokens"),
            ("PATCH", "https://api.github.com/repos/toby/demo/pulls/42"),
            ("DELETE", "https://api.github.com/repos/toby/demo/git/refs/heads/warroom/incident-X-1"),
        ]
        _, _, _, patch_body = http.calls[1]
        assert patch_body == {"state": "closed"}

    def test_close_pr_tolerates_404(self, tmp_path, monkeypatch):
        http = FakeHttp(
            [
                FakeResponse(payload={"token": "ghs_install"}),
                FakeResponse(status_code=404, payload={}),
                FakeResponse(status_code=404, payload={}),
            ]
        )
        client = _make_app_client(tmp_path, monkeypatch, http)
        client.close_pr("toby/demo", 42, "x")  # 예외 없어야 한다

    def test_close_pr_401_raises_auth_error(self, tmp_path, monkeypatch):
        http = FakeHttp(
            [
                FakeResponse(payload={"token": "ghs_install"}),
                FakeResponse(status_code=401, payload={}),
            ]
        )
        client = _make_app_client(tmp_path, monkeypatch, http)
        with pytest.raises(GitHubAuthError) as exc:
            client.close_pr("toby/demo", 42, "x")
        assert exc.value.status_code == 401

    def test_close_pr_403_raises_auth_error(self, tmp_path, monkeypatch):
        http = FakeHttp(
            [
                FakeResponse(payload={"token": "ghs_install"}),
                FakeResponse(status_code=403, payload={}),
            ]
        )
        client = _make_app_client(tmp_path, monkeypatch, http)
        with pytest.raises(GitHubAuthError) as exc:
            client.close_pr("toby/demo", 42, "x")
        assert exc.value.status_code == 403

    def test_close_pr_5xx_raises_transient_error(self, tmp_path, monkeypatch):
        http = FakeHttp(
            [
                FakeResponse(payload={"token": "ghs_install"}),
                FakeResponse(status_code=502, payload={}),
            ]
        )
        client = _make_app_client(tmp_path, monkeypatch, http)
        with pytest.raises(GitHubTransientError) as exc:
            client.close_pr("toby/demo", 42, "x")
        assert exc.value.status_code == 502

    def test_close_pr_branch_delete_5xx_raises_transient_error(self, tmp_path, monkeypatch):
        """PR close 는 성공해도 branch DELETE 가 5xx 면 transient surface."""
        http = FakeHttp(
            [
                FakeResponse(payload={"token": "ghs_install"}),
                FakeResponse(status_code=200, payload={}),
                FakeResponse(status_code=503, payload={}),
            ]
        )
        client = _make_app_client(tmp_path, monkeypatch, http)
        with pytest.raises(GitHubTransientError) as exc:
            client.close_pr("toby/demo", 42, "x")
        assert exc.value.status_code == 503

    def test_delete_branch_calls_delete_ref(self, tmp_path, monkeypatch):
        http = FakeHttp(
            [
                FakeResponse(payload={"token": "ghs_install"}),
                FakeResponse(status_code=204, payload={}),
            ]
        )
        client = _make_app_client(tmp_path, monkeypatch, http)
        client.delete_branch("toby/demo", "warroom/orphan")
        method, url, _, _ = http.calls[1]
        assert method == "DELETE"
        assert url == "https://api.github.com/repos/toby/demo/git/refs/heads/warroom/orphan"

    def test_delete_branch_tolerates_404(self, tmp_path, monkeypatch):
        http = FakeHttp(
            [
                FakeResponse(payload={"token": "ghs_install"}),
                FakeResponse(status_code=404, payload={}),
            ]
        )
        client = _make_app_client(tmp_path, monkeypatch, http)
        client.delete_branch("toby/demo", "warroom/already-gone")  # silent

    def test_delete_branch_401_raises_auth_error(self, tmp_path, monkeypatch):
        http = FakeHttp(
            [
                FakeResponse(payload={"token": "ghs_install"}),
                FakeResponse(status_code=401, payload={}),
            ]
        )
        client = _make_app_client(tmp_path, monkeypatch, http)
        with pytest.raises(GitHubAuthError):
            client.delete_branch("toby/demo", "warroom/x")

    def test_github_error_hierarchy(self):
        """Auth/Transient 둘 다 GitHubError 상속 — 호출자가 broad except 가능."""
        assert issubclass(GitHubAuthError, GitHubError)
        assert issubclass(GitHubTransientError, GitHubError)

    def test_429_rate_limit_classified_as_transient(self, tmp_path, monkeypatch):
        """429 (primary rate-limit) 은 transient — 재시도 가치 있음 (cold review LOW 2)."""
        http = FakeHttp(
            [
                FakeResponse(payload={"token": "ghs_install"}),
                FakeResponse(status_code=429, payload={}),
            ]
        )
        client = _make_app_client(tmp_path, monkeypatch, http)
        with pytest.raises(GitHubTransientError) as exc:
            client.close_pr("toby/demo", 42, "x")
        assert exc.value.status_code == 429

    def test_open_pr_403_raises_auth_error(self, tmp_path, monkeypatch):
        """approve 경로 transport (open_pr) 도 close_pr 와 동일 분류 — cold review MEDIUM 1."""
        http = FakeHttp(
            [
                FakeResponse(payload={"token": "ghs_install"}),
                FakeResponse(status_code=403, payload={}),
            ]
        )
        client = _make_app_client(tmp_path, monkeypatch, http)
        with pytest.raises(GitHubAuthError) as exc:
            client.open_pr("toby/demo", "head", "main", "t", "b")
        assert exc.value.status_code == 403

    def test_commit_files_5xx_raises_transient_error(self, tmp_path, monkeypatch):
        """commit_files 의 Git Data API 단계도 분류된 예외 — MEDIUM 1 후속."""
        http = FakeHttp(
            [
                FakeResponse(payload={"token": "ghs_install"}),
                FakeResponse(status_code=502, payload={}),  # _base_sha 단계에서 502
            ]
        )
        client = _make_app_client(tmp_path, monkeypatch, http)
        with pytest.raises(GitHubTransientError) as exc:
            client.commit_files("toby/demo", "head", "main", {"a.py": "x"}, "msg")
        assert exc.value.status_code == 502

    def test_get_file_content_401_raises_auth_error(self, tmp_path, monkeypatch):
        """get_file_content non-404 도 분류 — 404 만 FileNotFoundError 유지."""
        http = FakeHttp(
            [
                FakeResponse(payload={"token": "ghs_install"}),
                FakeResponse(status_code=401, payload={}),
            ]
        )
        client = _make_app_client(tmp_path, monkeypatch, http)
        with pytest.raises(GitHubAuthError) as exc:
            client.get_file_content("toby/demo", "a.py", "main")
        assert exc.value.status_code == 401

    def test_token_is_cached_across_calls(self, tmp_path, monkeypatch):
        http = FakeHttp(
            [
                FakeResponse(payload={"token": "ghs_T1"}),
                FakeResponse(payload={"object": {"sha": "sha-1"}}),
                FakeResponse(payload={"object": {"sha": "sha-2"}}),
            ]
        )
        client = _make_app_client(tmp_path, monkeypatch, http)
        client._base_sha("toby/demo", "main", client._auth_headers())
        client._base_sha("toby/demo", "main", client._auth_headers())
        install_calls = [c for c in http.calls if "access_tokens" in c[1]]
        assert len(install_calls) == 1


# --- pr_builder 정책 검증 (FakeClient) -------------------------------------


class FakeClient(GitHubClient):
    """``GitHubClient`` Protocol 의 in-memory 구현.

    base_files 로 ``get_file_content`` 응답을 통제하고, ``commit_files`` /
    ``open_pr`` 호출 페이로드를 리스트에 기록한다.
    """

    is_dry_run = False

    def __init__(self, base_files: dict[str, str] | None = None, pr_number: int = 42):
        self._base_files = base_files or {}
        self._pr_number = pr_number
        self.commits: list[dict] = []
        self.prs: list[dict] = []
        self.closed: list[tuple[str, int, str]] = []

    def get_file_content(self, repo: str, path: str, ref: str) -> str:
        if path not in self._base_files:
            raise FileNotFoundError(path)
        return self._base_files[path]

    def commit_files(self, repo, branch, base_branch, files, message):
        self.commits.append(
            {"repo": repo, "branch": branch, "base": base_branch, "files": dict(files), "message": message}
        )

    def open_pr(self, repo, branch, base_branch, title, body):
        payload = {
            "repo": repo,
            "branch": branch,
            "base": base_branch,
            "title": title,
            "body": body,
        }
        self.prs.append(payload)
        return {"html_url": f"https://github.com/{repo}/pull/{self._pr_number}", "number": self._pr_number}

    def close_pr(self, repo: str, pr_number: int, branch: str) -> None:
        self.closed.append((repo, pr_number, branch))

    def delete_branch(self, repo: str, branch: str) -> None:
        self.closed.append((repo, None, branch))


class TestPrBuilderDiffPath:
    def test_applies_diff_and_includes_incident_markdown(self):
        diff_report = ResolutionReport(
            incident_id="INC-DIFF-001",
            severity=Severity.HIGH,
            triage_summary="결제 NPE",
            root_cause="charge() lazy init 누락",
            patch_suggestion=(
                "```diff\n--- a/foo.py\n+++ b/foo.py\n@@ -1,3 +1,4 @@\n line1\n+inserted\n line2\n line3\n```"
            ),
            post_mortem_draft="단/중/장기",
            is_approved=True,
            created_at=datetime(2026, 5, 16, 9, 0, 0, tzinfo=APP_TZ),
        )
        client = FakeClient(base_files={"foo.py": "line1\nline2\nline3\n"}, pr_number=99)
        result = build_patch_pr(client, diff_report, repo="toby/demo")

        assert result.pr_number == 99
        assert result.dry_run is False
        # 단일 commit_files 호출 — diff 적용 결과 + incidents/<id>.md 동봉
        assert len(client.commits) == 1
        commit = client.commits[0]
        assert commit["base"] == "main"
        files = commit["files"]
        assert files["foo.py"] == "line1\ninserted\nline2\nline3\n"
        assert "# Incident INC-DIFF-001" in files["incidents/INC-DIFF-001.md"]
        # open_pr 도 한 번
        assert len(client.prs) == 1
        assert client.prs[0]["title"].startswith("[warroom] HIGH")

    def test_new_file_skips_base_fetch(self):
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
        client = FakeClient()  # base_files 비어 있음 — 신규 파일이므로 fetch 시도 안 해야
        result = build_patch_pr(client, new_file_report, repo="toby/demo")
        assert result.pr_number == 42
        commit = client.commits[0]
        paths = sorted(commit["files"].keys())
        assert paths == ["app/new_module.py", "incidents/INC-NEW-001.md"]

    def test_redacts_secrets_before_commit(self):
        leak_report = ResolutionReport(
            incident_id="INC-LEAK-001",
            severity=Severity.HIGH,
            triage_summary="t",
            root_cause="r",
            patch_suggestion=(
                "```diff\n"
                "--- a/cfg.py\n"
                "+++ b/cfg.py\n"
                "@@ -1,2 +1,3 @@\n"
                " import os\n"
                '+API_KEY = "sk-abcdefghijklmnopqrstuvwxyzABCDEFGHIJKL12345678"\n'
                " VERSION = 1\n"
                "```"
            ),
            post_mortem_draft="pm",
            is_approved=True,
            created_at=datetime(2026, 5, 17, 9, 0, 0, tzinfo=APP_TZ),
        )
        client = FakeClient(base_files={"cfg.py": "import os\nVERSION = 1\n"})
        build_patch_pr(client, leak_report, repo="toby/demo")
        cfg = client.commits[0]["files"]["cfg.py"]
        assert "sk-abcdefghijklmnopqrstuvwxyzABCDEFGHIJKL12345678" not in cfg
        assert "[REDACTED:openai_api_key]" in cfg


class TestPrBuilderMarkdownFallback:
    def test_falls_back_when_no_diff(self, report):
        client = FakeClient(pr_number=77)
        result = build_patch_pr(client, report, repo="toby/demo")
        assert result.pr_number == 77
        # 단일 commit_files — markdown 만 동봉
        commit = client.commits[0]
        assert list(commit["files"].keys()) == [f"incidents/{report.incident_id}.md"]

    def test_falls_back_on_apply_context_mismatch(self):
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
        # base content 가 diff context 와 전혀 다름 → verify_apply 실패 → 폴백
        client = FakeClient(base_files={"foo.py": "completely different\nlines here\n"})
        build_patch_pr(client, bad_diff_report, repo="toby/demo")
        assert len(client.commits) == 1
        assert list(client.commits[0]["files"].keys()) == ["incidents/INC-BAD-001.md"]

    def test_falls_back_when_base_file_404(self):
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
        client = FakeClient()  # base_files 비어 있음 → 신규 파일 아님 → FileNotFound → 폴백
        build_patch_pr(client, missing_report, repo="toby/demo")
        assert len(client.commits) == 1
        assert list(client.commits[0]["files"].keys()) == ["incidents/INC-MISSING-001.md"]


class _CleanupRecordingClient(GitHubClient):
    """open_pr 실패 시 delete_branch 가 호출되는지 추적하는 stub."""

    is_dry_run = False

    def __init__(
        self,
        open_pr_exc: Exception,
        delete_branch_exc: Exception | None = None,
        base_files: dict[str, str] | None = None,
    ):
        self._open_pr_exc = open_pr_exc
        self._delete_branch_exc = delete_branch_exc
        self._base_files = base_files or {}
        self.commits: list[dict] = []
        self.delete_branch_calls: list[tuple[str, str]] = []

    def get_file_content(self, repo: str, path: str, ref: str) -> str:
        if path not in self._base_files:
            raise FileNotFoundError(path)
        return self._base_files[path]

    def commit_files(self, repo, branch, base_branch, files, message):
        self.commits.append({"repo": repo, "branch": branch})

    def open_pr(self, repo, branch, base_branch, title, body):
        raise self._open_pr_exc

    def close_pr(self, repo: str, pr_number: int, branch: str) -> None:
        pass

    def delete_branch(self, repo: str, branch: str) -> None:
        self.delete_branch_calls.append((repo, branch))
        if self._delete_branch_exc:
            raise self._delete_branch_exc


class TestPrBuilderOrphanCleanup:
    """open_pr 실패 시 commit_files 가 만든 branch 가 orphan 으로 남지 않게 한다 (#7)."""

    def test_markdown_path_deletes_branch_on_open_pr_failure(self, report):
        boom = GitHubTransientError("502 bad gateway", 502)
        client = _CleanupRecordingClient(open_pr_exc=boom)

        with pytest.raises(GitHubTransientError):
            build_patch_pr(client, report, repo="toby/demo")

        # commit_files 는 이미 호출됨 → orphan 가능성 발생 → delete_branch 로 회수
        assert len(client.commits) == 1
        assert len(client.delete_branch_calls) == 1
        cleaned_repo, cleaned_branch = client.delete_branch_calls[0]
        assert cleaned_repo == "toby/demo"
        assert cleaned_branch == client.commits[0]["branch"]

    def test_diff_path_deletes_branch_on_open_pr_failure(self):
        diff_report = ResolutionReport(
            incident_id="INC-DIFF-ORPHAN",
            severity=Severity.HIGH,
            triage_summary="t",
            root_cause="r",
            patch_suggestion=(
                "```diff\n--- a/foo.py\n+++ b/foo.py\n@@ -1,2 +1,3 @@\n line1\n+ins\n line2\n```"
            ),
            post_mortem_draft="pm",
            is_approved=True,
            created_at=datetime(2026, 5, 18, 9, 0, 0, tzinfo=APP_TZ),
        )
        boom = GitHubAuthError("token expired", 401)
        client = _CleanupRecordingClient(
            open_pr_exc=boom,
            base_files={"foo.py": "line1\nline2\n"},
        )

        with pytest.raises(GitHubAuthError):
            build_patch_pr(client, diff_report, repo="toby/demo")

        assert len(client.delete_branch_calls) == 1
        assert client.delete_branch_calls[0][1] == client.commits[0]["branch"]

    def test_reraises_original_when_cleanup_also_fails(self, report):
        """delete_branch 도 실패하면 둘 다 로그하고 원본 예외 re-raise."""
        original = GitHubAuthError("403", 403)
        cleanup_fail = GitHubTransientError("503", 503)
        client = _CleanupRecordingClient(
            open_pr_exc=original,
            delete_branch_exc=cleanup_fail,
        )

        with pytest.raises(GitHubAuthError) as exc:
            build_patch_pr(client, report, repo="toby/demo")
        assert exc.value.status_code == 403  # 원본 예외 보존

        # cleanup 도 시도는 했음
        assert len(client.delete_branch_calls) == 1
