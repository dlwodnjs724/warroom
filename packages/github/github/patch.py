"""Unified diff 검증·적용 (subprocess + ``git apply``).

``common.diff`` 의 pure parsing 함수들과 분리된 infrastructure 레이어:
실제로 tempdir 에 파일을 materialize 하고 ``git`` CLI 를 호출한다.
이 의존은 ``common`` 에서 격리되어야 하므로 ``github`` 패키지 (이미 git
호출이 자연스러운 곳) 로 이동.
"""

import subprocess
import tempfile
from pathlib import Path

from common.diff import changed_paths


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
