"""GitHub App 기반 PR 생성 클라이언트.

두 경로:

1. **diff 경로** (Phase 4): patch_suggestion 에 unified diff 가 추출되면
   GitHub Contents API 로 원본 파일 fetch → `git apply --check` 검증 →
   tempdir 적용 → Git Data API (blob/tree/commit/ref) 로 단일 commit
   → PR open. 변경된 코드 파일 + `incidents/<id>.md` 분석 리포트가
   한 PR 에 함께 들어간다 (hybrid).

2. **markdown 폴백** (Phase 1 호환): diff 추출/검증/적용 어디서든 실패
   하면 기존 흐름 — `incidents/<id>.md` 만 commit 하고 PR open.

인증 흐름:
    App ID + private key → JWT(RS256, 10분 TTL)
    → POST /app/installations/{id}/access_tokens → installation token (1h)
    → 이후 모든 REST 호출은 installation token
"""

import base64
import time
from pathlib import Path
from urllib.parse import quote

import httpx
import jwt
from common.diff import changed_paths, extract_diff, is_new_file
from common.models import ResolutionReport
from common.redact import redact_secrets

from .base import GitHubClient, PullRequestResult
from .patch import apply_diff, verify_apply
from .report import branch_name, incident_markdown, pr_body, pr_title

_API = "https://api.github.com"


class DiffApplyError(RuntimeError):
    """unified diff 추출/검증/적용 단계에서 발생한 복구 가능한 실패.

    create_patch_pr 가 이 예외를 잡으면 markdown-only 폴백 (Phase 4.4) 으로 전환.
    """


class GitHubAppClient(GitHubClient):
    def __init__(
        self,
        app_id: str,
        private_key_path: str,
        installation_id: str,
        http_client: httpx.Client | None = None,
    ):
        self._app_id = app_id
        self._private_key = Path(private_key_path).read_text()
        self._installation_id = installation_id
        self._http = http_client or httpx.Client(timeout=20.0)
        self._token: str | None = None
        self._token_exp: float = 0.0

    def create_patch_pr(
        self,
        report: ResolutionReport,
        repo: str,
        base_branch: str = "main",
    ) -> PullRequestResult:
        """승인된 리포트로 PR 생성. diff 우선, 실패 시 markdown 폴백."""
        headers = self._auth_headers()

        diff = extract_diff(report.patch_suggestion)
        if diff:
            try:
                return self._apply_diff_pr(report, diff, repo, base_branch, headers)
            except DiffApplyError as e:
                print(f"[GitHubAppClient] diff 흐름 실패, markdown 폴백: {e}")

        return self._markdown_only_pr(report, repo, base_branch, headers)

    # ------- diff 경로 (Phase 4.3 / 4.6) ------------------------------------

    def _apply_diff_pr(
        self,
        report: ResolutionReport,
        diff: str,
        repo: str,
        base_branch: str,
        headers: dict,
    ) -> PullRequestResult:
        paths = changed_paths(diff)
        if not paths:
            raise DiffApplyError("diff 에 변경 파일 없음")

        base_sha = self._base_sha(repo, base_branch, headers)

        # 신규 파일 (--- /dev/null) 은 base_files 에서 제외 — pre-materialize 하면
        # git apply 가 "파일 이미 존재" 로 실패한다. apply_diff 가 git apply 가
        # 생성한 결과 파일을 그대로 결과 dict 에 포함시킨다.
        base_files: dict[str, str] = {}
        for path in paths:
            if is_new_file(diff, path):
                continue
            try:
                base_files[path] = self._get_file_content(repo, path, base_branch, headers)
            except FileNotFoundError as e:
                raise DiffApplyError(f"base 파일 없음 ({base_branch}): {e}") from None

        ok, err = verify_apply(diff, base_files)
        if not ok:
            raise DiffApplyError(f"git apply --check 실패: {err}")

        changed = apply_diff(diff, base_files)
        if changed is None:
            raise DiffApplyError("git apply 실패 (--check 통과했으나 본 적용 실패)")

        # 4.6 hybrid — 분석 리포트 동봉 (incident_markdown 내부에서 patch redact 처리)
        changed[f"incidents/{report.incident_id}.md"] = incident_markdown(report)

        # 4.7 — git history 영구 박힘 직전 마지막 단속: 변경된 모든 파일 컨텐츠를
        # redact_secrets 로 통과. LLM 이 패치 + 라인에 token/key 박은 경우 차단.
        # diff 본문이 아닌 최종 파일 컨텐츠 단위라 라인 카운트 영향 없음.
        changed = {path: redact_secrets(content) for path, content in changed.items()}

        branch = branch_name(report)
        base_tree = self._get_tree_sha(repo, base_sha, headers)

        entries = []
        for path, content in changed.items():
            blob_sha = self._create_blob(repo, content, headers)
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob_sha})

        new_tree = self._create_tree(repo, base_tree, entries, headers)
        commit_sha = self._create_commit(
            repo=repo,
            parent_sha=base_sha,
            tree_sha=new_tree,
            message=(
                f"fix({report.incident_id}): AI agent unified diff 패치\n\n"
                f"승인된 분석 리포트와 함께 commit. 변경 파일: {', '.join(paths)}"
            ),
            headers=headers,
        )
        self._create_branch(repo, branch, commit_sha, headers)

        pr = self._create_pr(repo, branch, base_branch, report, headers)
        print(f"[GitHubAppClient] diff PR 생성 완료: {pr['html_url']} (변경 {len(paths)} 파일)")
        return PullRequestResult(
            pr_url=pr["html_url"],
            pr_number=pr["number"],
            branch=branch,
            dry_run=False,
        )

    # ------- markdown-only 폴백 (Phase 1 호환) -------------------------------

    def _markdown_only_pr(
        self,
        report: ResolutionReport,
        repo: str,
        base_branch: str,
        headers: dict,
    ) -> PullRequestResult:
        base_sha = self._base_sha(repo, base_branch, headers)
        branch = branch_name(report)
        self._create_branch(repo, branch, base_sha, headers)
        self._put_file(
            repo=repo,
            branch=branch,
            path=f"incidents/{report.incident_id}.md",
            content=incident_markdown(report),
            message=f"docs(incident): {report.incident_id} AI 분석 리포트",
            headers=headers,
        )
        pr = self._create_pr(repo, branch, base_branch, report, headers)

        print(f"[GitHubAppClient] markdown PR 생성 완료: {pr['html_url']}")
        return PullRequestResult(
            pr_url=pr["html_url"],
            pr_number=pr["number"],
            branch=branch,
            dry_run=False,
        )

    # ------- auth ------------------------------------------------------------

    def _auth_headers(self) -> dict:
        token = self._installation_token()
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _installation_token(self) -> str:
        if self._token and time.time() < self._token_exp - 50:
            return self._token

        now = int(time.time())
        payload = {"iat": now - 60, "exp": now + 9 * 60, "iss": self._app_id}
        app_jwt = jwt.encode(payload, self._private_key, algorithm="RS256")

        resp = self._http.post(
            f"{_API}/app/installations/{self._installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["token"]
        self._token_exp = time.time() + 3600
        return self._token

    # ------- low-level REST helpers ------------------------------------------

    def _base_sha(self, repo: str, branch: str, headers: dict) -> str:
        resp = self._http.get(f"{_API}/repos/{repo}/git/ref/heads/{branch}", headers=headers)
        resp.raise_for_status()
        return resp.json()["object"]["sha"]

    def _get_file_content(self, repo: str, path: str, ref: str, headers: dict) -> str:
        """기존 파일의 raw 내용 fetch. 404 면 FileNotFoundError.

        신규 파일 케이스 (--- /dev/null) 는 호출자가 is_new_file() 로 사전
        분기해 이 메서드를 우회해야 한다. 호출되어 404 가 떨어졌다는 것은
        diff 가 가리키는 base 파일이 실제로는 없다는 의미 → DiffApplyError.

        path 는 URL-encode (공백/유니코드/`#` 안전 처리). 한국어 파일명, 공백
        포함 경로도 정상 동작.
        """
        encoded_path = quote(path, safe="/")
        encoded_ref = quote(ref, safe="/")
        resp = self._http.get(
            f"{_API}/repos/{repo}/contents/{encoded_path}?ref={encoded_ref}",
            headers=headers,
        )
        if resp.status_code == 404:
            raise FileNotFoundError(path)
        resp.raise_for_status()
        data = resp.json()
        return base64.b64decode(data["content"]).decode("utf-8")

    def _get_tree_sha(self, repo: str, commit_sha: str, headers: dict) -> str:
        resp = self._http.get(
            f"{_API}/repos/{repo}/git/commits/{commit_sha}",
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()["tree"]["sha"]

    def _create_blob(self, repo: str, content: str, headers: dict) -> str:
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        resp = self._http.post(
            f"{_API}/repos/{repo}/git/blobs",
            headers=headers,
            json={"content": encoded, "encoding": "base64"},
        )
        resp.raise_for_status()
        return resp.json()["sha"]

    def _create_tree(
        self,
        repo: str,
        base_tree: str,
        entries: list[dict],
        headers: dict,
    ) -> str:
        resp = self._http.post(
            f"{_API}/repos/{repo}/git/trees",
            headers=headers,
            json={"base_tree": base_tree, "tree": entries},
        )
        resp.raise_for_status()
        return resp.json()["sha"]

    def _create_commit(
        self,
        repo: str,
        parent_sha: str,
        tree_sha: str,
        message: str,
        headers: dict,
    ) -> str:
        resp = self._http.post(
            f"{_API}/repos/{repo}/git/commits",
            headers=headers,
            json={"message": message, "tree": tree_sha, "parents": [parent_sha]},
        )
        resp.raise_for_status()
        return resp.json()["sha"]

    def _create_branch(self, repo: str, branch: str, sha: str, headers: dict) -> None:
        resp = self._http.post(
            f"{_API}/repos/{repo}/git/refs",
            headers=headers,
            json={"ref": f"refs/heads/{branch}", "sha": sha},
        )
        resp.raise_for_status()

    def _put_file(
        self,
        repo: str,
        branch: str,
        path: str,
        content: str,
        message: str,
        headers: dict,
    ) -> None:
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        encoded_path = quote(path, safe="/")
        resp = self._http.put(
            f"{_API}/repos/{repo}/contents/{encoded_path}",
            headers=headers,
            json={"message": message, "content": encoded, "branch": branch},
        )
        resp.raise_for_status()

    def _create_pr(
        self,
        repo: str,
        branch: str,
        base_branch: str,
        report: ResolutionReport,
        headers: dict,
    ) -> dict:
        resp = self._http.post(
            f"{_API}/repos/{repo}/pulls",
            headers=headers,
            json={
                "title": pr_title(report),
                "head": branch,
                "base": base_branch,
                "body": pr_body(report),
            },
        )
        resp.raise_for_status()
        return resp.json()

    def close_pr(self, repo: str, pr_number: int, branch: str) -> None:
        """PR close + branch 삭제. 멱등 — 404 / 이미 닫힘은 silent skip."""
        headers = self._auth_headers()
        close_resp = self._http.patch(
            f"{_API}/repos/{repo}/pulls/{pr_number}",
            headers=headers,
            json={"state": "closed"},
        )
        if close_resp.status_code not in (200, 404, 422):
            close_resp.raise_for_status()

        del_resp = self._http.delete(
            f"{_API}/repos/{repo}/git/refs/heads/{branch}",
            headers=headers,
        )
        if del_resp.status_code not in (204, 404, 422):
            del_resp.raise_for_status()

        print(f"[GitHubAppClient] PR #{pr_number} closed + branch {branch} 삭제")
