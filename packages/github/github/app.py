"""GitHub App 기반 PR 생성 클라이언트.

PR 생성 정책 (diff 적용 / markdown 폴백 / hybrid 동봉) 은 ``pr_builder``
모듈로 분리되어 있다. 이 파일은 인증 (JWT → installation token) + 저수준
REST helper (blob/tree/commit/ref, Contents API) 만 책임진다.

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
from common.models import ResolutionReport

from .base import GitHubClient, PullRequestResult
from .pr_builder import build_patch_pr
from .report import pr_body, pr_title

_API = "https://api.github.com"


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
        """승인된 리포트로 PR 생성. usecase 본체는 ``pr_builder`` 에 위임."""
        return build_patch_pr(self, report, repo, base_branch)

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
