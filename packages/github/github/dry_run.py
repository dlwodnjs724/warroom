"""App credentials 가 없을 때의 폴백.

transport primitive 호출을 JSONL 페이로드로 기록하고, ``commit_files`` 에
포함된 ``incidents/<id>.md`` 는 ``./output/incidents/<id>.md`` 로 실제
파일로도 떨어뜨린다 (시연/디버깅용).

``get_file_content`` 는 항상 FileNotFoundError 를 던져 pr_builder 가 markdown
폴백 경로로 빠지게 한다 — dry-run 은 실제 base 파일을 보유하지 않으므로
``git apply`` 검증이 무의미.
"""

import json
import os
from pathlib import Path

from .base import GitHubClient


class DryRunGitHubClient(GitHubClient):
    is_dry_run = True

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

    def get_file_content(self, repo: str, path: str, ref: str) -> str:
        """dry-run 은 base 파일을 보유하지 않으므로 항상 FileNotFoundError.

        pr_builder 가 이를 잡아 DiffApplyError 로 변환 → markdown 폴백.
        """
        raise FileNotFoundError(path)

    def commit_files(
        self,
        repo: str,
        branch: str,
        base_branch: str,
        files: dict[str, str],
        message: str,
    ) -> None:
        # incidents/<id>.md 는 별도 파일로도 저장 (사람이 직접 열어보기 편하게)
        self._incidents_dir.mkdir(parents=True, exist_ok=True)
        for path, content in files.items():
            if path.startswith("incidents/") and path.endswith(".md"):
                md_path = self._incidents_dir / Path(path).name
                md_path.write_text(content, encoding="utf-8")

        payload = {
            "action": "commit_files",
            "repo": repo,
            "branch": branch,
            "base": base_branch,
            "message": message,
            "files": [{"path": p, "size": len(c)} for p, c in files.items()],
        }
        self._append_payload(payload)

    def open_pr(
        self,
        repo: str,
        branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> dict:
        payload = {
            "action": "open_pr",
            "repo": repo,
            "base": base_branch,
            "head": branch,
            "title": title,
            "body": body,
        }
        self._append_payload(payload)
        url = f"dry-run://github/{repo}/pull?branch={branch}"
        print(f"[GitHubClient:dry-run] {repo} ← PR 페이로드 기록")
        return {"html_url": url, "number": None}

    def close_pr(self, repo: str, pr_number: int, branch: str) -> None:
        payload = {"action": "close_pr", "repo": repo, "pr_number": pr_number, "branch": branch}
        self._append_payload(payload)
        print(f"[GitHubClient:dry-run] {repo} close_pr #{pr_number} branch={branch}")

    def delete_branch(self, repo: str, branch: str) -> None:
        payload = {"action": "delete_branch", "repo": repo, "branch": branch}
        self._append_payload(payload)
        print(f"[GitHubClient:dry-run] {repo} delete_branch {branch}")

    def _append_payload(self, payload: dict) -> None:
        self._payload_log.parent.mkdir(parents=True, exist_ok=True)
        with self._payload_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
