"""GitHub 연동 인터페이스.

승인된 ResolutionReport 를 받아 PR 을 생성한다. 실제 백엔드는
GitHubAppClient (운영) / DryRunGitHubClient (개발 폴백) 두 종류.
"""

from dataclasses import dataclass
from typing import Protocol

from common.models import ResolutionReport


@dataclass
class PullRequestResult:
    pr_url: str  # dry-run 시에는 "dry-run://..." 형식
    pr_number: int | None
    branch: str
    dry_run: bool


class GitHubClient(Protocol):
    def create_patch_pr(
        self,
        report: ResolutionReport,
        repo: str,
        base_branch: str = "main",
    ) -> PullRequestResult:
        """승인된 리포트로 PR 생성.

        Args:
            report: 승인된 ResolutionReport (is_approved=True)
            repo: "owner/repo" 형식
            base_branch: PR target branch
        """
        ...
