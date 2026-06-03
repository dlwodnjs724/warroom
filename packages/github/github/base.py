"""GitHub 연동 transport 인터페이스.

승인된 ResolutionReport → PR 생성 *usecase* 는 ``pr_builder`` 에 있고,
이 Protocol 은 그 usecase 가 의존하는 *transport primitives* 만 정의한다.

구현체:
- ``GitHubAppClient`` (운영): JWT + installation token + REST 호출
- ``DryRunGitHubClient`` (개발 폴백): JSONL 페이로드 + 로컬 파일 출력

에러 클래스는 호출자가 운영 대응을 분기할 수 있게 status 별 의미를 좁힌다 —
401/403 (auth) 은 토큰/권한 문제, 5xx (transient) 는 잠시 후 재시도 가능.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass
class PullRequestResult:
    pr_url: str  # dry-run 시에는 "dry-run://..." 형식
    pr_number: int | None
    branch: str
    dry_run: bool


class GitHubError(RuntimeError):
    """GitHub transport 호출에서 발생한 HTTP 실패.

    ``status_code`` 를 보존해 호출자가 운영 분기 (토큰 재발급 / 권한 점검 /
    재시도 큐) 를 결정할 수 있게 한다.
    """

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class GitHubAuthError(GitHubError):
    """401 / 403 — installation token 만료, 권한 부족, App 설정 문제.

    재시도해도 그대로 실패. 운영자가 토큰 회전 / App 권한 재발급 / repo
    접근 권한 확인을 해야 한다.
    """


class GitHubTransientError(GitHubError):
    """5xx / 429 — GitHub 측 일시 장애 / rate-limit. 잠시 후 재시도 가능.

    ``retry_after`` 는 ``Retry-After`` 헤더의 hint (초). 없으면 None —
    호출자가 자체 backoff 전략 (exponential 등) 사용.
    """

    def __init__(self, message: str, status_code: int, retry_after: float | None = None):
        super().__init__(message, status_code)
        self.retry_after = retry_after


class GitHubClient(Protocol):
    is_dry_run: bool
    """\
    구현이 실 GitHub 호출을 하지 않고 페이로드/파일 출력만 하는 dry-run 인지.

    pr_builder 는 PullRequestResult.dry_run 을 채울 때 이 속성을 본다 — open_pr
    return dict 에 ``dry_run`` 키를 끼워넣는 패턴은 Protocol 계약 밖이라 새 구
    현체가 누락하기 쉽다.
    """

    def get_file_content(self, repo: str, path: str, ref: str) -> str:
        """기존 파일의 raw 내용 fetch. 404 면 ``FileNotFoundError``.

        신규 파일 (--- /dev/null) 처리는 호출자가 ``common.diff.is_new_file``
        로 사전 분기. 404 가 떨어졌다는 것은 diff 가 가리키는 base 파일이
        실제 repo 에 없다는 의미 → pr_builder 가 DiffApplyError 로 변환해
        markdown 폴백으로 전환.
        """
        ...

    def list_commits(self, repo: str, path: str, limit: int = 5) -> list[dict]:
        """특정 파일의 최근 commit 이력. Analyst Agent 의 GitHub Source Lookup 용.

        반환 형식 (GitHub REST API ``GET /repos/{repo}/commits?path=``):
            [{
                "sha": "abc1234",
                "commit": {
                    "author": {"name": "...", "date": "ISO-8601"},
                    "message": "..."
                }
            }, ...]

        dry-run 구현은 empty list 를 반환 (실 데이터 없음 — caller 가 mock 텍스트
        폴백). 빈 list 와 실 데이터의 차이는 caller 책임.
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

        멱등 케이스 (404 = 이미 닫힘 / 422 = 이미 처리) 는 silent.
        401/403 은 ``GitHubAuthError``, 5xx 는 ``GitHubTransientError`` 로
        분류해 호출자가 운영 대응을 분기할 수 있게 한다.
        """
        ...

    def delete_branch(self, repo: str, branch: str) -> None:
        """branch 단독 삭제. ``open_pr`` 실패 후 orphan branch cleanup 용.

        멱등 — 404 / 422 는 silent. 401/403 / 5xx 는 ``GitHubError`` 분류.
        """
        ...
