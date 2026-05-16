"""common.diff helper 단위 테스트."""

from common.diff import apply_diff, changed_paths, extract_diff, verify_apply


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
