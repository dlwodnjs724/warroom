"""GitHub App 기반 transport 클라이언트.

PR 생성 정책 (diff 적용 / markdown 폴백 / hybrid 동봉) 은 ``pr_builder``
모듈로 분리되어 있다. 이 파일은 인증 (JWT → installation token) + transport
primitive (``get_file_content`` / ``commit_files`` / ``open_pr`` / ``close_pr``)
만 책임진다.

인증 흐름:
    App ID + private key → JWT(RS256, 10분 TTL)
    → POST /app/installations/{id}/access_tokens → installation token (1h)
    → 이후 모든 REST 호출은 installation token
"""

import base64
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote

import httpx
import jwt
from common.clock import now as _clock_now

from ..base import GitHubAuthError, GitHubClient, GitHubError, GitHubTransientError

_API = "https://api.github.com"


def _parse_retry_after(value: str | None) -> float | None:
    """``Retry-After`` 헤더 → seconds.

    RFC 7231 의 두 형태 지원:
        - delay-seconds (정수): ``"120"`` → 120.0
        - HTTP-date: ``"Wed, 21 Oct 2026 07:28:00 GMT"`` → 현재 시각과의 차이

    파싱 실패 / 빈 값 → None. 음수는 0.0 으로 clamp (이미 지난 시점).
    """
    if not value:
        return None
    stripped = value.strip()
    if stripped.isdigit():
        return float(stripped)
    try:
        dt = parsedate_to_datetime(stripped)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    delta = (dt - _clock_now()).total_seconds()
    return max(0.0, delta)


def _classify_status(status_code: int, context: str, retry_after: float | None = None) -> GitHubError:
    """HTTP status → 운영 의미가 분리된 예외.

    호출자는 ``GitHubAuthError`` (토큰/권한 — 재시도 무의미) 와
    ``GitHubTransientError`` (5xx / 429 rate-limit — 재시도 가치 있음) 를
    구분해 surface 한다. ``retry_after`` 는 transient 의 ``Retry-After`` hint.

    참고: GitHub 의 *secondary* rate-limit 은 403 + ``X-RateLimit-Remaining: 0``
    헤더로 오는데 — 현 구현은 본문 status 만 보므로 그 케이스는 ``GitHubAuthError``
    로 분류된다. primary rate-limit (429) / 5xx 는 ``Retry-After`` 헤더가
    있으면 ``retry_after`` 로 surface.
    """
    if status_code in (401, 403):
        return GitHubAuthError(f"{context}: 인증/권한 실패 (status={status_code})", status_code)
    if status_code == 429 or 500 <= status_code < 600:
        return GitHubTransientError(
            f"{context}: GitHub 측 일시 장애 / rate-limit (status={status_code})",
            status_code,
            retry_after=retry_after,
        )
    return GitHubError(f"{context}: HTTP {status_code}", status_code)


def _check(resp: httpx.Response, context: str) -> None:
    """비-2xx 응답을 ``_classify_status`` 가 분리한 예외로 변환.

    bare ``resp.raise_for_status()`` 대체. 모든 transport primitive 가 이 한
    helper 만 거치면 호출자가 토큰 만료 / rate-limit / 일시 장애를 동일 패턴
    (``try/except GitHubAuthError / GitHubTransientError``) 으로 처리 가능.

    transient (429 / 5xx) 케이스는 ``Retry-After`` 헤더를 파싱해 hint 로
    예외에 attach — 호출자가 backoff 결정 시 사용.
    """
    if 200 <= resp.status_code < 300:
        return
    retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
    raise _classify_status(resp.status_code, context, retry_after=retry_after)


class GitHubAppClient(GitHubClient):
    is_dry_run = False

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

    # ------- transport primitives -------------------------------------------

    def get_file_content(self, repo: str, path: str, ref: str) -> str:
        """기존 파일의 raw 내용 fetch. 404 면 FileNotFoundError.

        path 는 URL-encode (공백/유니코드/`#` 안전 처리). 한국어 파일명, 공백
        포함 경로도 정상 동작.
        """
        headers = self._auth_headers()
        encoded_path = quote(path, safe="/")
        encoded_ref = quote(ref, safe="/")
        resp = self._http.get(
            f"{_API}/repos/{repo}/contents/{encoded_path}?ref={encoded_ref}",
            headers=headers,
        )
        if resp.status_code == 404:
            raise FileNotFoundError(path)
        _check(resp, f"GET /repos/{repo}/contents/{path}")
        data = resp.json()
        return base64.b64decode(data["content"]).decode("utf-8")

    def list_commits(self, repo: str, path: str, limit: int = 5) -> list[dict]:
        """특정 path 의 최근 commit ``limit`` 건. Analyst tool 의 GitHub source 이력."""
        headers = self._auth_headers()
        encoded_path = quote(path, safe="/")
        resp = self._http.get(
            f"{_API}/repos/{repo}/commits?path={encoded_path}&per_page={int(limit)}",
            headers=headers,
        )
        _check(resp, f"GET /repos/{repo}/commits?path={path}")
        return resp.json()

    def commit_files(
        self,
        repo: str,
        branch: str,
        base_branch: str,
        files: dict[str, str],
        message: str,
    ) -> None:
        """Git Data API 로 base_branch 위에 새 branch 를 만들고 files 를 단일 commit 으로 push."""
        headers = self._auth_headers()
        base_sha = self._base_sha(repo, base_branch, headers)
        base_tree = self._get_tree_sha(repo, base_sha, headers)

        entries = []
        for path, content in files.items():
            blob_sha = self._create_blob(repo, content, headers)
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob_sha})

        new_tree = self._create_tree(repo, base_tree, entries, headers)
        commit_sha = self._create_commit(repo, base_sha, new_tree, message, headers)
        self._create_branch(repo, branch, commit_sha, headers)

    def open_pr(
        self,
        repo: str,
        branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> dict:
        headers = self._auth_headers()
        resp = self._http.post(
            f"{_API}/repos/{repo}/pulls",
            headers=headers,
            json={"title": title, "head": branch, "base": base_branch, "body": body},
        )
        _check(resp, f"POST /repos/{repo}/pulls")
        return resp.json()

    def close_pr(self, repo: str, pr_number: int, branch: str) -> None:
        """PR close + branch 삭제.

        멱등 케이스 (404 = 이미 닫힘 / 422 = 이미 처리) 는 silent. 401/403 은
        ``GitHubAuthError`` (토큰 만료 / 권한 부족 — 재시도 무의미), 5xx 는
        ``GitHubTransientError`` (재시도 가치 있음) 로 분리해 호출자가 운영
        분기 가능하게 한다.
        """
        headers = self._auth_headers()
        close_resp = self._http.patch(
            f"{_API}/repos/{repo}/pulls/{pr_number}",
            headers=headers,
            json={"state": "closed"},
        )
        if close_resp.status_code not in (200, 404, 422):
            raise _classify_status(
                close_resp.status_code,
                f"PATCH /repos/{repo}/pulls/{pr_number}",
                retry_after=_parse_retry_after(close_resp.headers.get("Retry-After")),
            )

        self._delete_ref(repo, branch, headers)
        print(f"[GitHubAppClient] PR #{pr_number} closed + branch {branch} 삭제")

    def delete_branch(self, repo: str, branch: str) -> None:
        """branch 단독 삭제 — ``open_pr`` 실패 후 orphan cleanup 용."""
        headers = self._auth_headers()
        self._delete_ref(repo, branch, headers)

    def _delete_ref(self, repo: str, branch: str, headers: dict) -> None:
        """DELETE /git/refs/heads/{branch} 공통 처리.

        404 / 422 멱등 silent. 그 외는 ``_classify_status`` 가 분리한 예외.
        """
        del_resp = self._http.delete(
            f"{_API}/repos/{repo}/git/refs/heads/{branch}",
            headers=headers,
        )
        if del_resp.status_code not in (204, 404, 422):
            raise _classify_status(
                del_resp.status_code,
                f"DELETE /repos/{repo}/git/refs/heads/{branch}",
                retry_after=_parse_retry_after(del_resp.headers.get("Retry-After")),
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
        _check(resp, f"POST /app/installations/{self._installation_id}/access_tokens")
        data = resp.json()
        self._token = data["token"]
        self._token_exp = time.time() + 3600
        return self._token

    # ------- Git Data API low-level helpers ---------------------------------

    def _base_sha(self, repo: str, branch: str, headers: dict) -> str:
        resp = self._http.get(f"{_API}/repos/{repo}/git/ref/heads/{branch}", headers=headers)
        _check(resp, f"GET /repos/{repo}/git/ref/heads/{branch}")
        return resp.json()["object"]["sha"]

    def _get_tree_sha(self, repo: str, commit_sha: str, headers: dict) -> str:
        resp = self._http.get(
            f"{_API}/repos/{repo}/git/commits/{commit_sha}",
            headers=headers,
        )
        _check(resp, f"GET /repos/{repo}/git/commits/{commit_sha}")
        return resp.json()["tree"]["sha"]

    def _create_blob(self, repo: str, content: str, headers: dict) -> str:
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        resp = self._http.post(
            f"{_API}/repos/{repo}/git/blobs",
            headers=headers,
            json={"content": encoded, "encoding": "base64"},
        )
        _check(resp, f"POST /repos/{repo}/git/blobs")
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
        _check(resp, f"POST /repos/{repo}/git/trees")
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
        _check(resp, f"POST /repos/{repo}/git/commits")
        return resp.json()["sha"]

    def _create_branch(self, repo: str, branch: str, sha: str, headers: dict) -> None:
        resp = self._http.post(
            f"{_API}/repos/{repo}/git/refs",
            headers=headers,
            json={"ref": f"refs/heads/{branch}", "sha": sha},
        )
        _check(resp, f"POST /repos/{repo}/git/refs (ref={branch})")
