"""common.redact_secrets 단위 테스트."""

from common.redact import redact_secrets


class TestRedactSecrets:
    def test_openai_key(self):
        # 실 OpenAI 키는 sk-[A-Za-z0-9]{48} — 본체에 하이픈/언더스코어 없음.
        text = "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyzABCDEFGHIJKL12345678"
        result = redact_secrets(text)
        assert "sk-abcdefghijklmnopqrstuvwxyzABCDEFGHIJKL12345678" not in result
        assert "[REDACTED:openai_api_key]" in result

    def test_openai_pattern_does_not_match_prose_identifier(self):
        # 본체에 하이픈 포함된 평범한 식별자는 매칭 안 됨 (false positive 차단)
        text = "deploy target: sk-prod-deployment-canary-v2-9999"
        result = redact_secrets(text)
        assert "[REDACTED" not in result

    def test_anthropic_key_more_specific(self):
        text = "ANTHROPIC=sk-ant-api03-abcdefghijklmnopqrstuv"
        result = redact_secrets(text)
        assert "[REDACTED:anthropic_api_key]" in result
        assert "[REDACTED:openai_api_key]" not in result

    def test_github_pat(self):
        text = "token: ghp_abcdefghijklmnopqrstuvwxyz0123456789"
        result = redact_secrets(text)
        assert "[REDACTED:github_pat]" in result
        assert "ghp_abcdefghij" not in result

    def test_github_app_token(self):
        text = "ghs_abcdefghijklmnopqrstuvwxyz0123456789"
        result = redact_secrets(text)
        assert "[REDACTED:github_app_token]" in result

    def test_slack_bot_token(self):
        text = "SLACK_BOT_TOKEN=xoxb-1234567890-abcdefghijk"
        result = redact_secrets(text)
        assert "[REDACTED:slack_token]" in result

    def test_slack_user_token(self):
        text = "xoxp-1234567890-abcdefghij"
        assert "[REDACTED:slack_token]" in redact_secrets(text)

    def test_aws_access_key(self):
        text = "AKIAIOSFODNN7EXAMPLE"
        result = redact_secrets(text)
        assert "[REDACTED:aws_access_key]" in result

    def test_google_api_key(self):
        text = "AIzaSyAbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
        assert "[REDACTED:google_api_key]" in redact_secrets(text)

    def test_gcp_service_account(self):
        text = "warroom-bot@warroom-prod.iam.gserviceaccount.com"
        assert "[REDACTED:gcp_service_account]" in redact_secrets(text)

    def test_private_key_block(self):
        text = (
            "before\n"
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEpAIBAAKCAQEAvZ7vF9z7XJzDqGqz8nLk7K7HfqJL\n"
            "-----END RSA PRIVATE KEY-----\n"
            "after"
        )
        result = redact_secrets(text)
        assert "[REDACTED:private_key]" in result
        assert "MIIEpAIB" not in result
        assert "before" in result and "after" in result

    def test_multiple_patterns_in_one_text(self):
        text = (
            "key1=sk-abcdefghijklmnopqrstuvwxyzABCDEFGHIJKL12345678 "
            "and pat=ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        )
        result = redact_secrets(text)
        assert "[REDACTED:openai_api_key]" in result
        assert "[REDACTED:github_pat]" in result

    def test_no_secrets_unchanged(self):
        text = "그냥 평범한 코드:\nif user is None:\n    raise ValueError('user required')"
        assert redact_secrets(text) == text

    def test_empty_string(self):
        assert redact_secrets("") == ""

    def test_within_code_block(self):
        # 가장 흔한 케이스 — LLM 이 예시 코드에 진짜 token 박는 경우
        text = (
            "```python\n" "client = OpenAI(api_key='sk-realonetencharsABCDEFGHIJKL0123456789012345')\n" "```"
        )
        result = redact_secrets(text)
        assert "sk-realonetencharsABCDEFGHIJKL0123456789012345" not in result
        assert "[REDACTED:openai_api_key]" in result
