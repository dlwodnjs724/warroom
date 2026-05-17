"""common.diff helper 단위 테스트."""

from common.diff import apply_diff, changed_paths, extract_diff, is_new_file, verify_apply


class TestExtractDiff:
    def test_fenced_diff_block(self):
        text = (
            "분석 결과:\n"
            "```diff\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -1,2 +1,3 @@\n"
            " line1\n"
            "+added\n"
            " line2\n"
            "```\n"
            "이후 텍스트"
        )
        result = extract_diff(text)
        assert result is not None
        assert result.startswith("--- a/foo.py")
        assert "+added" in result

    def test_patch_fence_also_accepted(self):
        text = "```patch\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n```"
        assert extract_diff(text) is not None

    def test_uppercase_fence(self):
        text = "```Diff\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n```"
        assert extract_diff(text) is not None

    def test_raw_diff_without_fence(self):
        text = "잡담\n--- a/foo.py\n+++ b/foo.py\n@@ -1 +1,2 @@\n a\n+b\n"
        result = extract_diff(text)
        assert result is not None
        assert result.startswith("--- a/foo.py")

    def test_no_diff_returns_none(self):
        assert extract_diff("그냥 텍스트, diff 없음") is None

    def test_fenced_python_block_ignored(self):
        # ```python 펜스는 diff 가 아니므로 무시. 안에 --- 가 있어도.
        text = "```python\ndef foo():\n    return 1\n```"
        assert extract_diff(text) is None

    def test_prose_with_dash_dash_dash_rejected(self):
        """본문에 `--- a/foo.py` 같은 표현만 있고 hunk 헤더 없으면 raw diff 로 인식 안 함."""
        text = (
            "분석 결과 `--- a/app/foo.py` 의 91번 라인이 의심됩니다.\n"
            "그리고 `+++ b/app/foo.py` 에서 검토를 권장합니다.\n"
            "(hunk 헤더 @@ 가 없으니 실제 diff 가 아님)"
        )
        assert extract_diff(text) is None

    def test_raw_diff_with_valid_hunk_accepted(self):
        text = "잡담\n--- a/foo.py\n+++ b/foo.py\n@@ -1,2 +1,3 @@\n line1\n+inserted\n line2\n"
        result = extract_diff(text)
        assert result is not None
        assert "@@" in result

    def test_picks_first_valid_fenced_block(self):
        text = (
            "```diff\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n```\n"
            "```diff\n--- a/y\n+++ b/y\n@@ -1 +1 @@\n-z\n+w\n```"
        )
        result = extract_diff(text)
        assert result is not None
        assert "a/x" in result
        assert "a/y" not in result


class TestChangedPaths:
    def test_single_file(self):
        diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-a\n+b"
        assert changed_paths(diff) == ["foo.py"]

    def test_multiple_files(self):
        diff = (
            "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-a\n+b\n"
            "--- a/bar.py\n+++ b/bar.py\n@@ -1 +1 @@\n-x\n+y"
        )
        assert changed_paths(diff) == ["foo.py", "bar.py"]

    def test_new_file_dev_null_source(self):
        diff = "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+hello"
        assert changed_paths(diff) == ["new.py"]

    def test_no_prefix(self):
        diff = "--- foo.py\n+++ foo.py\n@@ -1 +1 @@\n-a\n+b"
        assert changed_paths(diff) == ["foo.py"]

    def test_empty(self):
        assert changed_paths("") == []


class TestVerifyApply:
    def test_applies_clean(self):
        original = "line1\nline2\nline3\n"
        diff = (
            "--- a/foo.txt\n"
            "+++ b/foo.txt\n"
            "@@ -1,3 +1,4 @@\n"
            " line1\n"
            "+inserted\n"
            " line2\n"
            " line3\n"
        )
        ok, err = verify_apply(diff, {"foo.txt": original})
        assert ok, f"verify_apply failed: {err}"

    def test_fails_on_context_mismatch(self):
        original = "completely different content\n"
        diff = "--- a/foo.txt\n" "+++ b/foo.txt\n" "@@ -1,2 +1,3 @@\n" " line1\n" "+inserted\n" " line2\n"
        ok, err = verify_apply(diff, {"foo.txt": original})
        assert not ok
        assert err  # non-empty stderr

    def test_fails_when_target_file_missing(self):
        diff = "--- a/missing.txt\n+++ b/missing.txt\n@@ -1 +1 @@\n-a\n+b\n"
        ok, _ = verify_apply(diff, {})
        assert not ok


class TestIsNewFile:
    def test_dev_null_source_is_new(self):
        diff = "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+hello"
        assert is_new_file(diff, "new.py") is True

    def test_existing_source_is_not_new(self):
        diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-a\n+b"
        assert is_new_file(diff, "foo.py") is False

    def test_missing_target_returns_false(self):
        diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-a\n+b"
        assert is_new_file(diff, "other.py") is False

    def test_multi_file_only_one_is_new(self):
        diff = (
            "--- a/existing.py\n+++ b/existing.py\n@@ -1 +1 @@\n-a\n+b\n"
            "--- /dev/null\n+++ b/created.py\n@@ -0,0 +1 @@\n+hello"
        )
        assert is_new_file(diff, "existing.py") is False
        assert is_new_file(diff, "created.py") is True


class TestApplyDiff:
    def test_apply_simple_insert(self):
        original = "line1\nline2\n"
        diff = "--- a/foo.txt\n" "+++ b/foo.txt\n" "@@ -1,2 +1,3 @@\n" " line1\n" "+inserted\n" " line2\n"
        result = apply_diff(diff, {"foo.txt": original})
        assert result is not None
        assert result["foo.txt"] == "line1\ninserted\nline2\n"

    def test_apply_failure_returns_none(self):
        diff = (
            "--- a/foo.txt\n"
            "+++ b/foo.txt\n"
            "@@ -1,2 +1,3 @@\n"
            " expected_line\n"
            "+inserted\n"
            " other_line\n"
        )
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
