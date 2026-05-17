"""github.patch helper 단위 테스트 (git apply 기반 검증/적용)."""

from github.patch import apply_diff, verify_apply


class TestVerifyApply:
    def test_applies_clean(self):
        original = "line1\nline2\nline3\n"
        diff = "--- a/foo.txt\n+++ b/foo.txt\n@@ -1,3 +1,4 @@\n line1\n+inserted\n line2\n line3\n"
        ok, err = verify_apply(diff, {"foo.txt": original})
        assert ok, f"verify_apply failed: {err}"

    def test_fails_on_context_mismatch(self):
        original = "completely different content\n"
        diff = "--- a/foo.txt\n+++ b/foo.txt\n@@ -1,2 +1,3 @@\n line1\n+inserted\n line2\n"
        ok, err = verify_apply(diff, {"foo.txt": original})
        assert not ok
        assert err  # non-empty stderr

    def test_fails_when_target_file_missing(self):
        diff = "--- a/missing.txt\n+++ b/missing.txt\n@@ -1 +1 @@\n-a\n+b\n"
        ok, _ = verify_apply(diff, {})
        assert not ok


class TestApplyDiff:
    def test_apply_simple_insert(self):
        original = "line1\nline2\n"
        diff = "--- a/foo.txt\n+++ b/foo.txt\n@@ -1,2 +1,3 @@\n line1\n+inserted\n line2\n"
        result = apply_diff(diff, {"foo.txt": original})
        assert result is not None
        assert result["foo.txt"] == "line1\ninserted\nline2\n"

    def test_apply_failure_returns_none(self):
        diff = "--- a/foo.txt\n+++ b/foo.txt\n@@ -1,2 +1,3 @@\n expected_line\n+inserted\n other_line\n"
        result = apply_diff(diff, {"foo.txt": "wrong\ncontent\n"})
        assert result is None

    def test_apply_multi_file(self):
        diff = (
            "--- a/a.txt\n"
            "+++ b/a.txt\n"
            "@@ -1 +1,2 @@\n"
            " a\n"
            "+inserted-a\n"
            "--- a/b.txt\n"
            "+++ b/b.txt\n"
            "@@ -1 +1,2 @@\n"
            " b\n"
            "+inserted-b\n"
        )
        result = apply_diff(diff, {"a.txt": "a\n", "b.txt": "b\n"})
        assert result is not None
        assert result["a.txt"] == "a\ninserted-a\n"
        assert result["b.txt"] == "b\ninserted-b\n"
