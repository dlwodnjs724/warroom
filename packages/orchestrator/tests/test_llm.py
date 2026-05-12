"""LLM provider 추상화 단위 테스트.

실제 LLM 인스턴스를 만들지 않고, provider 분기·모델 선택·환경변수 오버라이드가
의도대로 동작하는지만 검증한다.
"""
import pytest

from orchestrator import llm as llm_mod


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "LLM_PROVIDER",
        "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY",
        "WARROOM_TRIAGE_MODEL",
        "WARROOM_ANALYST_MODEL",
        "WARROOM_FIXER_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


class TestProviderSelection:
    def test_default_provider_is_gemini(self):
        assert llm_mod.current_provider() == "gemini"

    def test_explicit_anthropic_provider(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        assert llm_mod.current_provider() == "anthropic"

    def test_invalid_provider_raises(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        with pytest.raises(ValueError, match="지원하지 않는"):
            llm_mod.current_provider()

    def test_required_api_key_env_per_provider(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "gemini")
        assert llm_mod.required_api_key_env() == "GEMINI_API_KEY"
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        assert llm_mod.required_api_key_env() == "ANTHROPIC_API_KEY"
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        assert llm_mod.required_api_key_env() is None


class TestModelSelection:
    def test_gemini_defaults_use_flash_lite_for_triage(self):
        models = llm_mod.selected_models()
        assert models["provider"] == "gemini"
        assert models["triage"] == "gemini/gemini-2.5-flash-lite"
        assert models["analyst"] == "gemini/gemini-2.5-flash"
        assert models["fixer"] == "gemini/gemini-2.5-flash"

    def test_anthropic_defaults_use_haiku_for_triage(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        models = llm_mod.selected_models()
        assert models["triage"] == "anthropic/claude-haiku-4-5-20251001"
        assert models["analyst"] == "anthropic/claude-sonnet-4-6"
        assert models["fixer"] == "anthropic/claude-sonnet-4-6"

    def test_env_var_overrides_default(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "gemini")
        monkeypatch.setenv("WARROOM_TRIAGE_MODEL", "gemini/gemini-1.5-pro")
        models = llm_mod.selected_models()
        assert models["triage"] == "gemini/gemini-1.5-pro"
        # Analyst/Fixer는 그대로 기본값
        assert models["analyst"] == "gemini/gemini-2.5-flash"


class TestApiKeyValidation:
    def test_missing_key_raises_runtime_error(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "gemini")
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
            llm_mod.triage_llm()

    def test_ollama_does_not_require_api_key(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        # ollama는 API 키 없어도 인스턴스 생성 단계에서 실패하지 않아야 함
        llm = llm_mod.triage_llm()
        assert llm is not None
