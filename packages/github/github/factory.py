"""GitHub 백엔드 선택 팩토리.

환경변수:
    GITHUB_APP_ID                  GitHub App ID (숫자)
    GITHUB_APP_PRIVATE_KEY_PATH    App private key (PEM) 파일 경로
    GITHUB_INSTALLATION_ID         App 이 install 된 installation ID

위 셋이 모두 있으면 GitHubAppClient, 하나라도 없으면 DryRunGitHubClient.
"""
import os

from .base import GitHubClient
from .dry_run import DryRunGitHubClient


def make_github_client() -> GitHubClient:
    app_id = os.getenv("GITHUB_APP_ID")
    pem_path = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH")
    installation_id = os.getenv("GITHUB_INSTALLATION_ID")

    if not (app_id and pem_path and installation_id):
        return DryRunGitHubClient()

    from .app import GitHubAppClient  # 지연 import (PyJWT/cryptography 비의존 경로 보존)

    return GitHubAppClient(
        app_id=app_id,
        private_key_path=pem_path,
        installation_id=installation_id,
    )
