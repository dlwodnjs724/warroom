"""웹훅 파서 단위 테스트."""
from gateway.parsers import datadog as datadog_parser
from gateway.parsers import sentry as sentry_parser


class TestSentryParser:
    def test_extracts_issue_id_and_title(self):
        payload = {
            "data": {
                "issue": {
                    "id": "sentry-1",
                    "title": "NullPointerException",
                    "culprit": "stripe.charge",
                }
            }
        }
        event = sentry_parser.parse(payload)
        assert event.incident_id == "sentry-1"
        assert event.source == "sentry"
        assert event.title == "NullPointerException"

    def test_falls_back_to_culprit_when_title_missing(self):
        payload = {"data": {"issue": {"id": "sentry-2", "culprit": "stripe.charge"}}}
        event = sentry_parser.parse(payload)
        assert event.title == "stripe.charge"

    def test_generates_uuid_when_id_missing(self):
        event = sentry_parser.parse({"data": {"issue": {"title": "X"}}})
        assert event.incident_id  # uuid 자동 생성
        assert event.title == "X"

    def test_preserves_raw_payload(self):
        payload = {"data": {"issue": {"id": "sentry-3", "title": "X"}}}
        event = sentry_parser.parse(payload)
        assert event.raw_payload is payload


class TestDatadogParser:
    def test_parses_monitor_webhook(self):
        payload = {
            "id": "1234567890",
            "title": "[Triggered] CPU > 90%",
            "alert_type": "error",
        }
        event = datadog_parser.parse(payload)
        assert event.incident_id == "1234567890"
        assert event.source == "datadog"
        assert event.title == "[Triggered] CPU > 90%"

    def test_parses_incident_webhook(self):
        payload = {
            "incident_public_id": "abc-123",
            "incident_severity": "SEV-2",
            "incident_title": "Payment service degradation",
        }
        event = datadog_parser.parse(payload)
        assert event.incident_id == "abc-123"
        assert event.source == "datadog"
        assert event.title == "Payment service degradation"

    def test_generates_uuid_when_no_identifier(self):
        event = datadog_parser.parse({"title": "Anonymous alert"})
        assert event.incident_id
        assert event.title == "Anonymous alert"

    def test_falls_back_to_default_title(self):
        event = datadog_parser.parse({"id": "x"})
        assert event.title == "Datadog alert"
