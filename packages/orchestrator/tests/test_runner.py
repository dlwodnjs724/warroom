"""Runner 헬퍼 함수 단위 테스트."""

from common.models import IncidentCategory, IncidentEvent, Severity
from orchestrator.runner import (
    _build_triage_only_report,
    _extract_category,
    _extract_severity,
    _split_fixer_output,
)


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


class TestCategoryExtraction:
    def test_extracts_code(self):
        assert _extract_category("- 카테고리: code") == IncidentCategory.CODE

    def test_extracts_infra(self):
        assert _extract_category("- 카테고리: infra") == IncidentCategory.INFRA

    def test_extracts_external(self):
        assert _extract_category("- 카테고리: external") == IncidentCategory.EXTERNAL

    def test_extracts_operational(self):
        assert _extract_category("- 카테고리: operational") == IncidentCategory.OPERATIONAL

    def test_case_insensitive(self):
        assert _extract_category("- 카테고리: CODE") == IncidentCategory.CODE

    def test_english_label_fallback(self):
        # 라벨이 없어도 본문에 카테고리 단어가 단독으로 등장하면 매칭
        assert _extract_category("the failure is infra related") == IncidentCategory.INFRA

    def test_returns_code_when_no_match(self):
        # 안전한 기본값: 패치 시도
        assert _extract_category("no marker here") == IncidentCategory.CODE

    def test_invalid_value_falls_back(self):
        # 정의되지 않은 카테고리는 fallback
        assert _extract_category("- 카테고리: unknown_thing") == IncidentCategory.CODE


class TestTriageOnlyReport:
    def _event(self):
        return IncidentEvent(
            incident_id="INC-INFRA-1",
            source="datadog",
            title="DB connection pool exhausted",
            raw_payload={},
        )

    def test_infra_category_has_empty_patch(self):
        report = _build_triage_only_report(
            self._event(), "triage briefing", Severity.HIGH, IncidentCategory.INFRA
        )
        assert report.category == IncidentCategory.INFRA
        assert report.severity == Severity.HIGH
        assert report.patch_suggestion == ""
        assert "운영 대응" in report.post_mortem_draft

    def test_external_category_marks_skip_reason(self):
        report = _build_triage_only_report(
            self._event(), "briefing", Severity.MEDIUM, IncidentCategory.EXTERNAL
        )
        assert "external" in report.post_mortem_draft
        assert report.root_cause.startswith("(코드 외")


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
