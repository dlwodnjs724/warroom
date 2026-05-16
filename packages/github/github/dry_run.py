"""App credentials 가 없을 때의 폴백.

생성될 PR 의 페이로드를 JSONL 로, 첨부 마크다운을 ./output/incidents/<id>.md 로
기록하고 dry-run URL 을 돌려준다. 데모/CI 환경에서 GitHub 호출 없이 전체
플로우를 검증하기 위한 용도.
"""

import json
import os
from pathlib import Path

from common.models import ResolutionReport

from .base import GitHubClient, PullRequestResult
from .report import branch_name, incident_markdown, pr_body, pr_title


class DryRunGitHubClient(GitHubClient):
    def __init__(
        self,
        payload_log: str | None = None,
        incidents_dir: str | None = None,
    ):
        self._payload_log = Path(
            payload_log or os.getenv("GITHUB_DRY_RUN_LOG", "./output/github_payloads.jsonl")
        )
        self._incidents_dir = Path(
            incidents_dir or os.getenv("GITHUB_DRY_RUN_INCIDENTS", "./output/incidents")
        )

    def create_patch_pr(
        self,
        report: ResolutionReport,
        repo: str,
        base_branch: str = "main",
    ) -> PullRequestResult:
        branch = branch_name(report)
        md = incident_markdown(report)

        self._incidents_dir.mkdir(parents=True, exist_ok=True)
        md_path = self._incidents_dir / f"{report.incident_id}.md"
        md_path.write_text(md, encoding="utf-8")

        payload = {
            "repo": repo,
            "base": base_branch,
            "head": branch,
            "title": pr_title(report),
            "body": pr_body(report),
            "files": [{"path": f"incidents/{report.incident_id}.md", "size": len(md)}],
        }
        self._payload_log.parent.mkdir(parents=True, exist_ok=True)
        with self._payload_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

        url = f"dry-run://github/{repo}/pull?branch={branch}"
        print(f"[GitHubClient:dry-run] {repo} ← PR 페이로드 기록 (md: {md_path})")
        return PullRequestResult(pr_url=url, pr_number=None, branch=branch, dry_run=True)

    def close_pr(self, repo: str, pr_number: int, branch: str) -> None:
        """dry-run — close 의도를 페이로드 로그에 append 한다."""
        payload = {"action": "close_pr", "repo": repo, "pr_number": pr_number, "branch": branch}
        self._payload_log.parent.mkdir(parents=True, exist_ok=True)
        with self._payload_log.open("a", encoding="utf-8") as f:
            import json as _json

            f.write(_json.dumps(payload, ensure_ascii=False) + "\n")
        print(f"[GitHubClient:dry-run] {repo} close_pr #{pr_number} branch={branch}")
