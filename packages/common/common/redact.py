"""Secret 패턴 redaction.

LLM 출력에 의도치 않게 들어간 token / key / private key 형태 문자열을
``[REDACTED:<type>]`` 로 치환. PR commit / Slack 메시지 / DB 영속화 직전
에 호출하는 것이 원칙 — secrets.md § 3 참조.

원본 LLM 출력이 그대로 git history 에 박히면 사후 회수가 매우 비싸다
(rebase + force push + 토큰 rotation). 이 단일 통과 지점에서 한 번에 차단.
"""

import re

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]*?-----END [A-Z ]+PRIVATE KEY-----"),
        "private_key",
    ),
    (re.compile(r"\bsk-ant-[a-zA-Z0-9_-]{20,}"), "anthropic_api_key"),
    (re.compile(r"\bsk-[a-zA-Z0-9_-]{20,}"), "openai_api_key"),
    (re.compile(r"\bghp_[a-zA-Z0-9]{36}\b"), "github_pat"),
    (re.compile(r"\bghs_[a-zA-Z0-9]{36}\b"), "github_app_token"),
    (re.compile(r"\bxox[baprs]-[a-zA-Z0-9-]{10,}"), "slack_token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "aws_access_key"),
    (
        re.compile(r"\b[a-zA-Z0-9-]+@[a-zA-Z0-9-]+\.iam\.gserviceaccount\.com\b"),
        "gcp_service_account",
    ),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"), "google_api_key"),
]


def redact_secrets(text: str) -> str:
    """Token / key 형태 문자열을 ``[REDACTED:<type>]`` 로 일괄 치환.

    빈 문자열 / None-falsy 입력은 그대로 반환. 패턴 우선순위는 _PATTERNS
    순서 — 더 구체적인 패턴 (예: ``sk-ant-``) 을 일반 패턴 (``sk-``) 보다
    위에 두어 정확한 라벨이 붙도록.
    """
    if not text:
        return text
    for pattern, label in _PATTERNS:
        text = pattern.sub(f"[REDACTED:{label}]", text)
    return text
