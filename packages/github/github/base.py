"""GitHub 연동 transport 인터페이스.

승인된 ResolutionReport → PR 생성 *usecase* 는 ``pr_builder`` 에 있고,
이 Protocol 은 그 usecase 가 의존하는 *transport primitives* 만 정의한다.

구현체:
- ``GitHubAppClient`` (운영): JWT + installation token + REST 호출
- ``DryRunGitHubClient`` (개발 폴백): JSONL 페이로드 + 로컬 파일 출력
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass
class PullRequestResult:
    pr_url: str  # dry-run 시에는 "dry-run://..." 형식
    pr_number: int | None
    branch: str
    dry_run: bool


class GitHubClient(Protocol):
    def get_file_content(self, repo: str, path: str, ref: str) -> str:
        """기존 파일의 raw 내용 fetch. 404 면 ``FileNotFoundError``.

        신규 파일 (--- /dev/null) 처리는 호출자가 ``common.diff.is_new_file``
        로 사전 분기. 404 가 떨어졌다는 것은 diff 가 가리키는 base 파일이
        실제 repo 에 없다는 의미 → pr_builder 가 DiffApplyError 로 변환해
        markdown 폴백으로 전환.
        """
        ...

    def commit_files(
        self,
        repo: str,
        branch: str,
        base_branch: str,
        files: dict[str, str],
        message: str,
    ) -> None:
        """``base_branch`` 위에 새 ``branch`` 를 만들고 ``files`` 를 단일 commit 으로 push.

        ``files`` 는 ``{repo-relative-path: file content}`` 매핑. 변경된 파일과
        ``incidents/<id>.md`` 분석 리포트를 한 commit 에 동봉하기 위해 단일
        호출로 묶었다 (hybrid bundle).
        """
        ...

    def open_pr(
        self,
        repo: str,
        branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> dict:
        """PR open. 반환은 ``{"html_url": ..., "number": ...}`` 형태."""
        ...

    def close_pr(self, repo: str, pr_number: int, branch: str) -> None:
        """반려된 인시던트의 PR 을 close 하고 branch 도 삭제.

        404 / 이미 닫힌 PR 등 멱등 처리. 호출자 (decisions service) 가
        예외에 의존하지 않도록 best-effort 로 구현.
        """
        ...
