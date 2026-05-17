"""Unified diff 추출 / 검사 helper (pure parsing).

LLM 이 생성한 패치 텍스트에서 unified diff 본문을 안전하게 뽑아내고,
변경되는 경로와 신규 파일 여부를 식별한다.

여기는 ``common`` (leaf 도메인 패키지) 이므로 외부 IO/subprocess 의존 금지 —
``git apply`` 같은 실 적용은 ``github.patch`` 로 분리되어 있다.
"""

import re

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

    diff 적용자 (``github.patch.apply_diff``) 가 기존 파일 fetch 와
    신규 파일 (404 = 정상) 을 구분하기 위해 사용한다.
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
