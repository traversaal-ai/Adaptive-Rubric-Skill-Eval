"""Line-ending normalisation for files staged INTO a sandbox.

Why this exists: SkillsBench graders are shell scripts, and the dataset is usually cloned with
``git config core.autocrlf=true`` on Windows, which rewrites every line to end ``\\r\\n``. Linux
inside the container then reads those carriage returns as literal text, and the grader silently
falls apart:

* ``mkdir -p /logs/verifier`` creates a directory literally named ``verifier\\r``
* results are written to a path nothing can read back
* the closing ``exit 0`` becomes ``exit 0\\r`` → *numeric argument required* → **exit code 2**

Nothing errors loudly; the run just scores 0. Every task, every harness, every time — an eval
result that measures the user's git config instead of the model. Normalising here makes grading
independent of how the dataset was checked out.

Only known text types are touched, and only when a CRLF is actually present, so binary fixtures and
already-clean trees pass through untouched (no copy at all in the common case).
"""

from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

#: Extensions we will rewrite. Deliberately conservative — a stray ``\r`` inside a PDF or an image
#: fixture would corrupt it, and only executable/config text can break a grader.
_TEXT_SUFFIXES = frozenset({
    ".sh", ".bash", ".zsh", ".py", ".rb", ".pl", ".js", ".ts",
    ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".md", ".csv", ".env",
})
#: Extension-less files that are still scripts (``run``, ``entrypoint``, …) are detected by shebang.
_SHEBANG = b"#!"
_READ_SNIFF = 2048


def _is_text(path: Path) -> bool:
    if path.suffix.lower() in _TEXT_SUFFIXES:
        return True
    if path.suffix:  # a suffix we don't recognise → leave it alone
        return False
    try:
        return path.open("rb").read(len(_SHEBANG)) == _SHEBANG
    except OSError:
        return False


def _has_crlf(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            while chunk := fh.read(65536):
                if b"\r\n" in chunk:
                    return True
    except OSError:
        return False
    return False


def _candidates(root: Path) -> Iterator[Path]:
    if root.is_file():
        yield root
        return
    for p in root.rglob("*"):
        if p.is_file():
            yield p


def needs_normalising(host_src: str) -> bool:
    """True if any text file under ``host_src`` has Windows line endings."""
    root = Path(host_src)
    return any(_is_text(p) and _has_crlf(p) for p in _candidates(root))


def _rewrite(path: Path) -> None:
    data = path.read_bytes()
    path.write_bytes(data.replace(b"\r\n", b"\n"))


@contextmanager
def normalized_source(host_src: str) -> Iterator[str]:
    """Yield a path to ``host_src`` guaranteed to have LF line endings in its text files.

    The original is never modified — a rewritten copy is made in a temp dir and cleaned up on exit.
    When nothing needs fixing (the overwhelmingly common case on Linux/macOS) the original path is
    yielded unchanged and no copy happens at all.
    """
    src = Path(host_src)
    if not src.exists() or not needs_normalising(host_src):
        yield host_src
        return

    with tempfile.TemporaryDirectory(prefix="adarubric-stage-") as td:
        target = Path(td) / src.name
        if src.is_file():
            shutil.copy2(src, target)
        else:
            shutil.copytree(src, target)
        for p in _candidates(target):
            if _is_text(p) and _has_crlf(p):
                _rewrite(p)
        yield str(target)
