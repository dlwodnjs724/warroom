"""common.diff helper 단위 테스트 (pure parsing)."""

from common.diff import changed_paths, extract_diff, is_new_file


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
            "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-a\n+b\n--- a/bar.py\n+++ b/bar.py\n@@ -1 +1 @@\n-x\n+y"
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
