"""승인된 리포트 → PR 생성 usecase.

두 경로:

1. **diff 경로**: patch_suggestion 에서 unified diff 추출 → base 파일 fetch
   (신규 파일은 skip) → ``git apply --check`` 검증 → tempdir 적용 → 변경된
   파일 + ``incidents/<id>.md`` 분석 리포트 + redact → 단일 commit → PR open.

2. **markdown 폴백**: diff 추출/검증/적용 실패 시 ``incidents/<id>.md`` 만
   commit 하고 PR open.

``GitHubClient`` Protocol (transport primitives) 만 의존한다 — 인증, REST
호출, base64 인코딩 등 transport 디테일은 클라이언트 구현이 책임진다.
"""

import logging

from common.diff import changed_paths, extract_diff, is_new_file
from common.models import ResolutionReport
from common.redact import redact_secrets

from .base import GitHubClient, PullRequestResult
from .patch import apply_diff, verify_apply
from .report import branch_name, incident_markdown, pr_body, pr_title

logger = logging.getLogger(__name__)


class DiffApplyError(RuntimeError):
    """unified diff 추출/검증/적용 단계에서 발생한 복구 가능한 실패.

    build_patch_pr 가 이 예외를 잡으면 markdown-only 폴백으로 전환.
    """


def build_patch_pr(
    client: GitHubClient,
    report: ResolutionReport,
    repo: str,
    base_branch: str = "main",
) -> PullRequestResult:
    """승인된 리포트로 PR 생성. diff 우선, 실패 시 markdown 폴백."""
    diff = extract_diff(report.patch_suggestion)
    if diff:
        try:
            return _build_diff_pr(client, report, diff, repo, base_branch)
        except DiffApplyError as e:
            logger.warning("diff 흐름 실패, markdown 폴백: %s", e)

    return _build_markdown_only_pr(client, report, repo, base_branch)


def _open_pr_or_cleanup(
    client: GitHubClient,
    repo: str,
    branch: str,
    base_branch: str,
    title: str,
    body: str,
) -> dict:
    """``open_pr`` 호출 + 실패 시 orphan branch best-effort 삭제 (#7).

    ``commit_files`` 가 이미 GitHub 에 branch + commit 을 만든 상태에서
    ``open_pr`` 가 raise 하면 branch 만 남고 PR 레코드는 없는 orphan 상태.
    이후 ``_close_pr_if_exists`` 는 DB 에 ``set_pr_info`` 가 안 된 상태라
    cleanup 시도조차 못한다. open_pr 실패 시점에 즉시 ``delete_branch`` 로
    회수하고, 회수마저 실패하면 운영자가 인지할 수 있게 명시 로그.

    원본 예외는 그대로 re-raise — 호출자가 status_code 기반 분기 가능.
    """
    try:
        return client.open_pr(
            repo=repo,
            branch=branch,
            base_branch=base_branch,
            title=title,
            body=body,
        )
    except Exception as open_err:
        try:
            client.delete_branch(repo, branch)
            logger.warning(
                "open_pr 실패 — orphan branch %s@%s 정리 완료: %s",
                repo,
                branch,
                open_err,
            )
        except Exception as cleanup_err:
            # 운영자가 반드시 인지해야 함 — GitHub 에 orphan branch 가 남는다.
            # 그대로 paste 해 수동 삭제 가능: gh api -X DELETE /repos/{repo}/git/refs/heads/{branch}
            logger.error(
                "ORPHAN open_pr 실패 + branch 정리 실패 — 수동 삭제 필요: "
                "gh api -X DELETE /repos/%s/git/refs/heads/%s (open_err=%s, cleanup_err=%s)",
                repo,
                branch,
                open_err,
                cleanup_err,
            )
        raise


def _build_diff_pr(
    client: GitHubClient,
    report: ResolutionReport,
    diff: str,
    repo: str,
    base_branch: str,
) -> PullRequestResult:
    paths = changed_paths(diff)
    if not paths:
        raise DiffApplyError("diff 에 변경 파일 없음")

    # 신규 파일 (--- /dev/null) 은 base_files 에서 제외 — pre-materialize 하면
    # git apply 가 "파일 이미 존재" 로 실패한다. apply_diff 가 git apply 가
    # 생성한 결과 파일을 그대로 결과 dict 에 포함시킨다.
    base_files: dict[str, str] = {}
    for path in paths:
        if is_new_file(diff, path):
            continue
        try:
            base_files[path] = client.get_file_content(repo, path, base_branch)
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
    message = (
        f"fix({report.incident_id}): AI agent unified diff 패치\n\n"
        f"승인된 분석 리포트와 함께 commit. 변경 파일: {', '.join(paths)}"
    )
    client.commit_files(
        repo=repo,
        branch=branch,
        base_branch=base_branch,
        files=changed,
        message=message,
    )
    pr = _open_pr_or_cleanup(
        client=client,
        repo=repo,
        branch=branch,
        base_branch=base_branch,
        title=pr_title(report),
        body=pr_body(report),
    )
    logger.info("diff PR 생성 완료: %s (변경 %d 파일)", pr["html_url"], len(paths))
    return PullRequestResult(
        pr_url=pr["html_url"],
        pr_number=pr["number"],
        branch=branch,
        dry_run=client.is_dry_run,
    )


def _build_markdown_only_pr(
    client: GitHubClient,
    report: ResolutionReport,
    repo: str,
    base_branch: str,
) -> PullRequestResult:
    branch = branch_name(report)
    client.commit_files(
        repo=repo,
        branch=branch,
        base_branch=base_branch,
        files={f"incidents/{report.incident_id}.md": incident_markdown(report)},
        message=f"docs(incident): {report.incident_id} AI 분석 리포트",
    )
    pr = _open_pr_or_cleanup(
        client=client,
        repo=repo,
        branch=branch,
        base_branch=base_branch,
        title=pr_title(report),
        body=pr_body(report),
    )
    logger.info("markdown PR 생성 완료: %s", pr["html_url"])
    return PullRequestResult(
        pr_url=pr["html_url"],
        pr_number=pr["number"],
        branch=branch,
        dry_run=client.is_dry_run,
    )
