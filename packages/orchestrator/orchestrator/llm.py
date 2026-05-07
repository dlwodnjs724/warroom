"""LLM provider 추상화.

CrewAI 에이전트(Triage/Analyst/Fixer)에 서로 다른 모델을 주입할 수 있도록
provider/model 선택 로직을 한곳에 모은다.

환경변수:
    LLM_PROVIDER            gemini | anthropic | ollama  (기본: gemini)
    GEMINI_API_KEY          provider=gemini 일 때 필수
    ANTHROPIC_API_KEY       provider=anthropic 일 때 필수
    OLLAMA_BASE_URL         provider=ollama 일 때 사용 (기본: http://localhost:11434)

    WARROOM_TRIAGE_MODEL    Triage 에이전트 모델 오버라이드
    WARROOM_ANALYST_MODEL   Analyst 에이전트 모델 오버라이드
    WARROOM_FIXER_MODEL     Fixer 에이전트 모델 오버라이드
"""
import os

from crewai import LLM


_DEFAULTS: dict[str, dict[str, str]] = {
    "gemini": {
        "triage": "gemini/gemini-2.5-flash-lite",
        "analyst": "gemini/gemini-2.5-flash",
        "fixer": "gemini/gemini-2.5-flash",
    },
    "anthropic": {
        "triage": "anthropic/claude-haiku-4-5-20251001",
        "analyst": "anthropic/claude-sonnet-4-6",
        "fixer": "anthropic/claude-sonnet-4-6",
    },
    "ollama": {
        "triage": "ollama/llama3.2",
        "analyst": "ollama/qwen2.5-coder",
        "fixer": "ollama/qwen2.5-coder",
    },
}

_API_KEY_ENV: dict[str, str | None] = {
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "ollama": None,
}

_ROLE_ENV = {
    "triage": "WARROOM_TRIAGE_MODEL",
    "analyst": "WARROOM_ANALYST_MODEL",
    "fixer": "WARROOM_FIXER_MODEL",
}


def current_provider() -> str:
    provider = os.getenv("LLM_PROVIDER", "gemini").lower()
    if provider not in _DEFAULTS:
        raise ValueError(
            f"지원하지 않는 LLM_PROVIDER: {provider!r}. "
            f"사용 가능: {', '.join(_DEFAULTS)}"
        )
    return provider


def required_api_key_env() -> str | None:
    return _API_KEY_ENV[current_provider()]


def _model_for(role: str) -> str:
    provider = current_provider()
    return os.getenv(_ROLE_ENV[role], _DEFAULTS[provider][role])


def _build_llm(model: str) -> LLM:
    provider = current_provider()
    kwargs: dict = {"model": model, "temperature": 0.2}

    api_key_env = _API_KEY_ENV[provider]
    if api_key_env:
        api_key = os.getenv(api_key_env)
        if not api_key:
            raise RuntimeError(f"{api_key_env} 가 설정되지 않았습니다. .env 를 확인하세요.")
        kwargs["api_key"] = api_key
    elif provider == "ollama":
        kwargs["base_url"] = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    return LLM(**kwargs)


def triage_llm() -> LLM:
    return _build_llm(_model_for("triage"))


def analyst_llm() -> LLM:
    return _build_llm(_model_for("analyst"))


def fixer_llm() -> LLM:
    return _build_llm(_model_for("fixer"))


def selected_models() -> dict[str, str]:
    """현재 선택된 provider와 에이전트별 모델명 반환 (로깅 용도)."""
    return {
        "provider": current_provider(),
        "triage": _model_for("triage"),
        "analyst": _model_for("analyst"),
        "fixer": _model_for("fixer"),
    }
