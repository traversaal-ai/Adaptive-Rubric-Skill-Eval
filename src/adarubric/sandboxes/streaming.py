"""Live output streaming — run a subprocess and tee every line to a callback as it appears.

This is what powers ``--verbose``: the same commands the silent path runs, but each output line is
also handed to a sink (the terminal) the moment it is printed, instead of only surfacing minutes
later in the artifacts. The returned :class:`ShellResult` carries the FULL untrimmed output, so the
transcript and graders see exactly what they would have seen without the live view.
"""

from __future__ import annotations

import subprocess
import threading
from typing import Callable

from adarubric.core.models import ShellResult

#: Longest line shown live. Agent CLIs emit single-line JSON events that can run to megabytes;
#: showing the head keeps the terminal usable while the full line still lands in the artifacts.
_MAX_LINE = 400


def trim_line(line: str) -> str:
    """One displayable line: no trailing newline, capped at ``_MAX_LINE`` with a visible marker."""
    line = line.rstrip("\r\n")
    if len(line) > _MAX_LINE:
        return line[:_MAX_LINE] + f" … [+{len(line) - _MAX_LINE} chars]"
    return line


def stream_run(
    args: list[str] | str,
    *,
    shell: bool = False,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    echo: Callable[[str], None],
    timeout: int | None = None,
) -> ShellResult:
    """``subprocess.run(capture_output=True)``, except each stdout/stderr line is ALSO sent to
    ``echo`` live. Raises :class:`subprocess.TimeoutExpired` on timeout (the process is killed),
    matching what the silent path raises."""
    proc = subprocess.Popen(  # noqa: S602,S603 - identical commands to the silent capture path
        args, shell=shell, cwd=cwd, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    out: list[str] = []
    err: list[str] = []

    def _pump(pipe, sink: list[str]) -> None:
        for line in pipe:
            sink.append(line)
            shown = trim_line(line)
            if shown:
                try:
                    echo(shown)
                except Exception:  # noqa: BLE001 - the live view must never break the run
                    pass

    threads = [
        threading.Thread(target=_pump, args=(proc.stdout, out), daemon=True),
        threading.Thread(target=_pump, args=(proc.stderr, err), daemon=True),
    ]
    for t in threads:
        t.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise
    for t in threads:  # let the readers drain what the (finished) process buffered
        t.join(timeout=10)
    return ShellResult(stdout="".join(out), stderr="".join(err), exit_code=proc.returncode)
