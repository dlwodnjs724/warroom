"""승인된 리포트 → PR 생성 usecase.

두 경로:

1. **diff 경로**: patch_suggestion 에서 unified diff 추출 → base 파일 fetch
   (신규 파일은 skip) → ``git apply --check`` 검증 → tempdir 적용 → 변경된
   파일 + ``incidents/<id>.md`` 분석 리포트 + redact → Git Data API 로 단일
   commit → PR open.

2. **markdown 폴백**: diff 추출/검증/적용 실패 시 ``incidents/<id>.md`` 만
   commit 하고 PR open.

이 모듈은 PR 생성의 *정책* 만 담당한다 — REST 호출, 인증, base64 인코딩 등
transport 디테일은 클라이언트 구현 (``GitHubAppClient``) 이 책임진다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from common.diff import changed_paths, extract_diff, is_new_file
from common.models import ResolutionReport
from common.redact import redact_secrets

from .base import PullRequestResult
from .patch import apply_diff, verify_apply
from .report import branch_name, incident_markdown

if TYPE_CHECKING:
    from .app import GitHubAppClient


class DiffApplyError(RuntimeError):
    """unified diff 추출/검증/적용 단계에서 발생한 복구 가능한 실패.

    build_patch_pr 가 이 예외를 잡으면 markdown-only 폴백으로 전환.
    """


def build_patch_pr(
    client: GitHubAppClient,
    report: ResolutionReport,
    repo: str,
    base_branch: str = "main",
) -> PullRequestResult:
    """승인된 리포트로 PR 생성. diff 우선, 실패 시 markdown 폴백."""
    headers = client._auth_headers()

    diff = extract_diff(report.patch_suggestion)
    if diff:
        try:
            return _build_diff_pr(client, report, diff, repo, base_branch, headers)
        except DiffApplyError as e:
            print(f"[pr_builder] diff 흐름 실패, markdown 폴백: {e}")

    return _build_markdown_only_pr(client, report, repo, base_branch, headers)


def _build_diff_pr(
    client: GitHubAppClient,
    report: ResolutionReport,
    diff: str,
    repo: str,
    base_branch: str,
    headers: dict,
) -> PullRequestResult:
    paths = changed_paths(diff)
    if not paths:
        raise DiffApplyError("diff 에 변경 파일 없음")

    base_sha = client._base_sha(repo, base_branch, headers)

    # 신규 파일 (--- /dev/null) 은 base_files 에서 제외 — pre-materialize 하면
    # git apply 가 "파일 이미 존재" 로 실패한다. apply_diff 가 git apply 가
    # 생성한 결과 파일을 그대로 결과 dict 에 포함시킨다.
    base_files: dict[str, str] = {}
    for path in paths:
        if is_new_file(diff, path):
            continue
        try:
            base_files[path] = client._get_file_content(repo, path, base_branch, headers)
        except FileNotFoundError as e:
            raise DiffApplyError(f"base 파일 없음 ({base_branch}): {e}") from None

    ok, err = verify_apply(diff, base_files)
    if not ok:
        raise DiffApplyError(f"git apply --check 실패: {err}")

    changed = apply_diff(diff, base_files)
    if changed is None:
        raise DiffApplyError("git apply 실패 (--check 통과했으나 본 적용 실패)")

    # hybrid — 분석 리포트 동봉 (incident_markdown 내부에서 patch redact 처리)
    changed[f"incidents/{report.incident_id}.md"] = incident_markdown(report)

    # git history 영구 박힘 직전 마지막 단속: 변경된 모든 파일 컨텐츠를
    # redact_secrets 로 통과. LLM 이 패치 + 라인에 token/key 박은 경우 차단.
    # diff 본문이 아닌 최종 파일 컨텐츠 단위라 라인 카운트 영향 없음.
    changed = {path: redact_secrets(content) for path, content in changed.items()}

    branch = branch_name(report)
    base_tree = client._get_tree_sha(repo, base_sha, headers)

    entries = []
    for path, content in changed.items():
        blob_sha = client._create_blob(repo, content, headers)
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob_sha})

    new_tree = client._create_tree(repo, base_tree, entries, headers)
    commit_sha = client._create_commit(
        repo=repo,
        parent_sha=base_sha,
        tree_sha=new_tree,
        message=(
            f"fix({report.incident_id}): AI agent unified diff 패치\n\n"
            f"승인된 분석 리포트와 함께 commit. 변경 파일: {', '.join(paths)}"
        ),
        headers=headers,
    )
    client._create_branch(repo, branch, commit_sha, headers)

    pr = client._create_pr(repo, branch, base_branch, report, headers)
    print(f"[pr_builder] diff PR 생성 완료: {pr['html_url']} (변경 {len(paths)} 파일)")
    return PullRequestResult(
        pr_url=pr["html_url"],
        pr_number=pr["number"],
        branch=branch,
        dry_run=False,
    )


def _build_markdown_only_pr(
    client: GitHubAppClient,
    report: ResolutionReport,
    repo: str,
    base_branch: str,
    headers: dict,
) -> PullRequestResult:
    base_sha = client._base_sha(repo, base_branch, headers)
    branch = branch_name(report)
    client._create_branch(repo, branch, base_sha, headers)
    client._put_file(
        repo=repo,
        branch=branch,
        path=f"incidents/{report.incident_id}.md",
        content=incident_markdown(report),
        message=f"docs(incident): {report.incident_id} AI 분석 리포트",
        headers=headers,
    )
    pr = client._create_pr(repo, branch, base_branch, report, headers)

    print(f"[pr_builder] markdown PR 생성 완료: {pr['html_url']}")
    return PullRequestResult(
        pr_url=pr["html_url"],
        pr_number=pr["number"],
        branch=branch,
        dry_run=False,
    )
