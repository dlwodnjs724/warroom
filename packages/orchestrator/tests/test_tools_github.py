"""GitHub Source Lookup tool 단위 테스트.

Sentry tool 과 동일 패턴 — broad except → mock fallback. 추가로 GITHUB_REPO /
client.is_dry_run / list_commits 결과별 분기 검증.
"""

import pytest
from github.base import GitHubClient
from orchestrator.tools.github import _format_commits, _mock_response, github_source_lookup


def _call(file_path: str) -> str:
    return github_source_lookup.run(file_path=file_path)


class _FakeClient(GitHubClient):
    """전형적인 dry-run / 실 client 양쪽을 시뮬레이션."""

    def __init__(
        self,
        is_dry_run: bool = False,
        commits: list[dict] | None = None,
        file_content: str | Exception = "print('hello')",
    ):
        self.is_dry_run = is_dry_run
        self._commits = commits or []
        self._file_content = file_content

    def list_commits(self, repo, path, limit=5):
        return list(self._commits)

    def get_file_content(self, repo, path, ref):
        if isinstance(self._file_content, Exception):
            raise self._file_content
        return self._file_content

    # 사용 안 하는 메서드들 — 호출되면 fail
    def commit_files(self, *a, **kw):
        raise NotImplementedError

    def open_pr(self, *a, **kw):
        raise NotImplementedError

    def close_pr(self, *a, **kw):
        raise NotImplementedError

    def delete_branch(self, *a, **kw):
        raise NotImplementedError


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    monkeypatch.delenv("GITHUB_REPO", raising=False)
    yield


class TestMockFallback:
    def test_no_repo_returns_mock(self, monkeypatch):
        result = _call("app/stripe.py")
        assert "Mock 데이터" in result
        assert "app/stripe.py" in result

    def test_dry_run_client_returns_mock(self, monkeypatch):
        monkeypatch.setenv("GITHUB_REPO", "owner/demo")
        fake = _FakeClient(is_dry_run=True)
        monkeypatch.setattr("orchestrator.tools.github.make_github_client", lambda: fake)
        result = _call("app/x.py")
        assert "Mock 데이터" in result

    def test_list_commits_exception_falls_back_to_mock(self, monkeypatch):
        monkeypatch.setenv("GITHUB_REPO", "owner/demo")

        class _Boom(_FakeClient):
            def list_commits(self, repo, path, limit=5):
                raise RuntimeError("boom")

        monkeypatch.setattr("orchestrator.tools.github.make_github_client", lambda: _Boom())
        result = _call("app/x.py")
        assert "Mock 데이터" in result


class TestRealApi:
    def test_commits_only(self, monkeypatch):
        monkeypatch.setenv("GITHUB_REPO", "owner/demo")
        commits = [
            {
                "sha": "abc1234deadbeef",
                "commit": {
                    "author": {"name": "kim", "date": "2026-06-01T03:10:00Z"},
                    "message": "feat: lazy init\n\n자세한 설명...",
                },
            },
            {
                "sha": "def5678feedcafe",
                "commit": {
                    "author": {"name": "lee", "date": "2026-05-30T10:00:00Z"},
                    "message": "refactor: 코드 정리",
                },
            },
        ]
        fake = _FakeClient(commits=commits, file_content="line1\nline2\nline3\n")
        monkeypatch.setattr("orchestrator.tools.github.make_github_client", lambda: fake)
        result = _call("app/stripe.py")
        assert "Mock 데이터" not in result
        assert "abc1234" in result
        assert "kim" in result
        assert "feat: lazy init" in result  # 첫 줄만
        assert "자세한 설명" not in result  # 본문 라인 제외
        assert "line1\nline2\nline3" in result  # 파일 head 첨부

    def test_file_content_404_silent(self, monkeypatch):
        """get_file_content 404 는 silent — commit 이력만으로도 응답."""
        monkeypatch.setenv("GITHUB_REPO", "owner/demo")
        fake = _FakeClient(
            commits=[{"sha": "a1b2c3d", "commit": {"author": {"name": "x", "date": "d"}, "message": "m"}}],
            file_content=FileNotFoundError("missing"),
        )
        monkeypatch.setattr("orchestrator.tools.github.make_github_client", lambda: fake)
        result = _call("removed/file.py")
        assert "Mock 데이터" not in result
        assert "a1b2c3d" in result
        assert "HEAD 에 존재하지 않음" in result

    def test_file_content_truncated_to_80_lines(self, monkeypatch):
        monkeypatch.setenv("GITHUB_REPO", "owner/demo")
        body = "\n".join(f"line {i}" for i in range(200))
        fake = _FakeClient(commits=[], file_content=body)
        monkeypatch.setattr("orchestrator.tools.github.make_github_client", lambda: fake)
        result = _call("app/big.py")
        # head 80 라인만 — line 79 (0-indexed) 까지 포함, line 80+ 제외
        assert "line 79" in result
        assert "line 80" not in result


class TestFormatHelper:
    def test_format_empty_commits(self):
        assert "최근 commit 이력 없음" in _format_commits("a.py", [])

    def test_format_single_commit_uses_short_sha(self):
        result = _format_commits(
            "a.py",
            [{"sha": "1234567abcdef", "commit": {"author": {"name": "n", "date": "d"}, "message": "m"}}],
        )
        assert "1234567" in result
        assert "abcdef" not in result  # 7자만

    def test_mock_response_contains_path(self):
        assert "x/y.py" in _mock_response("x/y.py")
