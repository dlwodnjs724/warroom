"""Runner 헬퍼 함수 단위 테스트."""
from common.models import Severity
from orchestrator.runner import _extract_severity, _split_fixer_output


class TestSeverityExtraction:
    def test_extracts_critical(self):
        assert _extract_severity("- 심각도: critical") == Severity.CRITICAL

    def test_extracts_high(self):
        assert _extract_severity("- 심각도: HIGH") == Severity.HIGH

    def test_extracts_medium_case_insensitive(self):
        assert _extract_severity("Severity: Medium impact") == Severity.MEDIUM

    def test_extracts_low(self):
        assert _extract_severity("- 심각도: low") == Severity.LOW

    def test_critical_takes_precedence_over_high(self):
        # critical이 먼저 매칭되어야 함 (순서 의존 검증)
        text = "this is critical and high severity"
        assert _extract_severity(text) == Severity.CRITICAL

    def test_returns_medium_when_no_match(self):
        assert _extract_severity("문자열에 매칭 없음") == Severity.MEDIUM


class TestFixerOutputSplit:
    def test_splits_on_korean_marker(self):
        output = "패치 코드 부분\n## 포스트모템\n포스트모템 내용"
        patch, postmortem = _split_fixer_output(output)
        assert "패치 코드" in patch
        assert "포스트모템" in postmortem

    def test_splits_on_english_marker(self):
        output = "patch code\n## Post-mortem\npostmortem body"
        patch, postmortem = _split_fixer_output(output)
        assert "patch code" in patch
        assert "Post-mortem" in postmortem or "post-mortem" in postmortem.lower()

    def test_returns_full_output_as_patch_when_no_marker(self):
        output = "patch only, no marker"
        patch, postmortem = _split_fixer_output(output)
        assert patch == output
        assert "포함되어" in postmortem  # placeholder 메시지
