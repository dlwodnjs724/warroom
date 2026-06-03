"""Analyst Agent 의 GitHub Source Lookup tool.

``GITHUB_REPO`` + GitHub App credentials 가 설정되면 실 transport 재사용
(``make_github_client()`` 가 ``GitHubAppClient``), 미설정이면 ``DryRunGitHubClient``
가 반환되고 결과적으로 mock 텍스트 fallback.

caller (Analyst Agent) 가 분기할 의미 있는 예외 없음 — LLM tool 표면이라
broad except → mock fallback. (sentry tool 과 동일 패턴 — 룰 § 4b 면제)
"""

import os

from crewai.tools import tool
from github.clients.factory import make_github_client


def _mock_response(file_path: str) -> str:
    """token / repo 없거나 호출 실패 시 fallback. mock 파이프라인과 같은 톤."""
    return f"""
[GitHub] {file_path} — Mock 데이터 (GITHUB_REPO 미설정 또는 호출 실패)

커밋 abc1234 (2026-04-09 03:10 UTC) by dev-kim
  "feat: stripe client 초기화 방식 변경 (lazy loading 적용)"

변경 내용 (diff):
- self.client = stripe.Client(api_key=settings.STRIPE_KEY)
+ self.client = None  # lazy init

+ def _ensure_client(self):
+     if self.client is None:
+         self.client = stripe.Client(api_key=settings.STRIPE_KEY)

문제: charge() 메서드에서 _ensure_client() 호출 누락됨
"""


def _format_commits(file_path: str, commits: list[dict]) -> str:
    """list_commits 응답 → LLM 이 읽기 좋은 텍스트."""
    if not commits:
        return f"[GitHub] {file_path} — 최근 commit 이력 없음"
    lines = [f"[GitHub] {file_path} — 최근 commit {len(commits)}건"]
    for c in commits:
        sha = (c.get("sha") or "")[:7]
        commit = c.get("commit") or {}
        author = (commit.get("author") or {}).get("name") or "(unknown)"
        date = (commit.get("author") or {}).get("date") or "(unknown)"
        message = (commit.get("message") or "").split("\n", 1)[0]
        lines.append("")
        lines.append(f"커밋 {sha} ({date}) by {author}")
        lines.append(f'  "{message}"')
    return "\n".join(lines)


@tool("GitHub Source Lookup")
def github_source_lookup(file_path: str) -> str:
    """GitHub 에서 특정 파일의 최근 commit 이력 + 현재 파일 본문 (앞부분) 조회.

    GitHub App credentials 가 있으면 실 API, 없으면 dry-run client → mock 폴백.
    """
    repo = os.getenv("GITHUB_REPO")
    if not repo:
        return _mock_response(file_path)

    client = make_github_client()
    if client.is_dry_run:
        return _mock_response(file_path)

    try:
        commits = client.list_commits(repo, file_path, limit=5)
    except Exception as e:
        print(f"[orchestrator.tools.github] {file_path} list_commits 실패 — mock 폴백: {e}")
        return _mock_response(file_path)

    body = _format_commits(file_path, commits)

    # 현재 파일 본문도 첨부 — 단 길면 head 80줄로 자름 (LLM 컨텍스트 보호).
    try:
        content = client.get_file_content(repo, file_path, "HEAD")
        head = "\n".join(content.splitlines()[:80])
        body += f"\n\n현재 파일 내용 (head 80줄):\n```\n{head}\n```"
    except FileNotFoundError:
        body += f"\n\n파일 {file_path} 가 HEAD 에 존재하지 않음 (삭제됐거나 path 오타)."
    except Exception as e:
        print(f"[orchestrator.tools.github] {file_path} get_file_content 실패 (생략): {e}")

    return body
