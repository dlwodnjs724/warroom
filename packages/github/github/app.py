"""GitHub App 기반 PR 생성 클라이언트.

인증 흐름:
    1. App ID + private key 로 JWT(RS256, 10분 TTL) 생성
    2. POST /app/installations/{id}/access_tokens → installation token (1h TTL)
    3. 이후 모든 REST 호출은 installation token 사용

PR 생성 흐름:
    1. base branch SHA 조회
    2. 새 ref(branch) 생성
    3. incidents/<id>.md 파일을 PUT contents 로 commit
    4. PR open
"""
import base64
import time
from pathlib import Path

import httpx
import jwt

from common.models import ResolutionReport

from .base import GitHubClient, PullRequestResult
from .report import branch_name, incident_markdown, pr_body, pr_title


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
        token = self._installation_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

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

        print(f"[GitHubAppClient] PR 생성 완료: {pr['html_url']}")
        return PullRequestResult(
            pr_url=pr["html_url"],
            pr_number=pr["number"],
            branch=branch,
            dry_run=False,
        )

    def _installation_token(self) -> str:
        # 50초 여유로 캐시
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
        # expires_at 은 ISO 문자열이지만 1시간 고정이므로 단순 처리
        self._token_exp = time.time() + 3600
        return self._token

    def _base_sha(self, repo: str, branch: str, headers: dict) -> str:
        resp = self._http.get(
            f"{_API}/repos/{repo}/git/ref/heads/{branch}", headers=headers
        )
        resp.raise_for_status()
        return resp.json()["object"]["sha"]

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
        resp = self._http.put(
            f"{_API}/repos/{repo}/contents/{path}",
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
