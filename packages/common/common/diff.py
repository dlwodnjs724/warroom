"""Unified diff 추출 / 검증 / 적용 helper.

LLM 이 생성한 패치 텍스트에서 unified diff 본문을 안전하게 뽑아내고,
대상 파일 컨텐츠에 `git apply` 적용 가능한지 검증한다.

Phase 4 의 핵심: Fixer agent 출력을 실제 코드 변경으로 변환하는 통로.
"""

import re
import subprocess
import tempfile
from pathlib import Path

_FENCE_DIFF = re.compile(r"```(?:diff|patch)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", re.MULTILINE)


def extract_diff(text: str) -> str | None:
    """Fixer 출력에서 unified diff 본문을 추출.

    1순위: ` ```diff ... ``` ` 또는 ` ```patch ... ``` ` 펜스 안쪽
    2순위: 펜스 없이 본문에 `--- a/...` 헤더 + `+++ ` + `@@ -X,Y +X,Y @@` hunk
           헤더가 함께 있을 때만 raw diff 로 간주 (prose 가 우연히 `--- a/X`
           표현을 포함했을 때의 false positive 차단)
    실패: None
    """
    for match in _FENCE_DIFF.finditer(text):
        body = match.group(1).strip()
        if "--- " in body and "+++ " in body:
            return body

    m = re.search(r"^--- [ab]?/.+$", text, re.MULTILINE)
    if m:
        candidate = text[m.start() :].strip()
        if "+++ " in candidate and _HUNK_HEADER.search(candidate):
            return candidate

    return None


def is_new_file(diff_text: str, target_path: str) -> bool:
    """Diff 안에서 해당 target path 가 ``--- /dev/null`` 로부터 생성되는지.

    `_apply_diff_pr` 가 기존 파일 fetch 와 신규 파일 (404 = 정상) 을
    구분하기 위해 사용한다.
    """
    lines = diff_text.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("+++ "):
            continue
        t = line[4:].strip()
        if t.startswith(("a/", "b/")):
            t = t[2:]
        if t != target_path:
            continue
        source = lines[i - 1] if i > 0 else ""
        return source.startswith("--- ") and "/dev/null" in source
    return False


def changed_paths(diff_text: str) -> list[str]:
    """Diff 가 수정하는 파일들의 repo-relative 경로 추출 (new path 기준).

    `+++ b/path/to/file.py` → `path/to/file.py`. `/dev/null` 은 제외 (삭제).
    """
    paths: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            p = line[4:].strip()
            if p.startswith(("a/", "b/")):
                p = p[2:]
            if p and p != "/dev/null":
                paths.append(p)
    return paths


def verify_apply(diff_text: str, files: dict[str, str]) -> tuple[bool, str]:
    """`git apply --check` 로 diff 가 주어진 파일 컨텐츠에 적용 가능한지 검증.

    `files` 는 ``{repo-relative-path: file content}`` 매핑.
    반환: ``(ok, stderr_message)``.
    """
    with tempfile.TemporaryDirectory() as tmp:
        _materialize(tmp, files, diff_text)
        result = subprocess.run(
            ["git", "apply", "--check", "_patch.diff"],
            cwd=tmp,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0, result.stderr.strip()


def apply_diff(diff_text: str, files: dict[str, str]) -> dict[str, str] | None:
    """Diff 적용 후 변경된 파일들의 새 컨텐츠 dict 를 반환.

    apply 실패 시 None. 변경되지 않은 파일은 결과에 포함되지 않는다.
    """
    with tempfile.TemporaryDirectory() as tmp:
        _materialize(tmp, files, diff_text)
        result = subprocess.run(
            ["git", "apply", "_patch.diff"],
            cwd=tmp,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        out: dict[str, str] = {}
        for path in changed_paths(diff_text):
            file_path = Path(tmp) / path
            if file_path.exists():
                out[path] = file_path.read_text()
        return out


def _materialize(tmp: str, files: dict[str, str], diff_text: str) -> None:
    tmp_path = Path(tmp)
    for path, content in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    diff_file = tmp_path / "_patch.diff"
    diff_file.write_text(diff_text if diff_text.endswith("\n") else diff_text + "\n")
