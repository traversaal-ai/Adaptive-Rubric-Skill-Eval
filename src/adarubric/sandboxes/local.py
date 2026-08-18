"""Local sandbox — runs attempts directly on the host in an OS temp workspace.

Cross-platform: the workspace is an OS temp dir (``tempfile.mkdtemp``), not a hardcoded ``/tmp``,
and commands run through the platform shell. Skills are injected into both agent discovery
locations so whichever harness runs can find them.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from adarubric.core.contracts import PROMPT_RELPATH, SKILL_INJECT_IGNORE, Harness, Sandbox
from adarubric.core.models import EvalSpec, ShellResult
from adarubric.sandboxes.staging import normalized_source


class LocalSandbox(Sandbox):
    name = "local"

    def setup(self, spec: EvalSpec, harness: Harness, env: dict[str, str] | None = None) -> str:
        workspace = tempfile.mkdtemp(prefix="adarubric-")
        root = Path(workspace)
        self._note(f"created local workspace {workspace}")

        # 1. Copy the task's workspace inputs in (plain files → basename; map → explicit dest).
        staging = {f: Path(f).name for f in spec.workspace_files}
        staging.update(spec.workspace_map)
        for src_s, dest_rel in staging.items():
            src = Path(src_s)
            if not src.exists():
                continue
            dst = root / dest_rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)

        # 2. Write the canonical prompt file (harnesses read it via stdin redirection).
        prompt = root / PROMPT_RELPATH
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text(spec.instruction, encoding="utf-8")

        # 3. Inject each skill into the RUNNING harness's discovery dir(s) — relative to the
        #    workspace root (local runs the agent with cwd=workspace, so project discovery finds it).
        #    Skipped entirely for `--no-skill`, the control half of "did the skill help?".
        if not spec.inject_skills:
            withheld = ", ".join(Path(p).name for p in spec.skill_paths) or "none"
            self._note(f"skills NOT injected (--no-skill) — withheld: {withheld}")
        for discovery in harness.skill_dirs if spec.inject_skills else ():
            base = root / discovery
            base.mkdir(parents=True, exist_ok=True)
            for spath in spec.skill_paths:
                sp = Path(spath)
                if sp.is_dir():
                    self._note(f"inject skill '{sp.name}' → {discovery}/ (control files stripped)")
                    # Strip AdaRubric control files so the grader/task never leak into the skill.
                    shutil.copytree(sp, base / sp.name, dirs_exist_ok=True,
                                    ignore=shutil.ignore_patterns(*SKILL_INJECT_IGNORE))

        return workspace

    def run_command(
        self, workspace: str, command: str, env: dict[str, str] | None = None
    ) -> ShellResult:
        if self.log is not None:
            # --verbose: same command, but every output line also reaches the terminal live —
            # this is the agent CLI actually working, instead of a silent ". running" for minutes.
            from adarubric.sandboxes.streaming import stream_run

            return stream_run(command, shell=True, cwd=workspace,
                              env={**os.environ, **(env or {})}, echo=self._log)
        proc = subprocess.run(  # noqa: S602 - shell is intentional (agent CLIs are shell commands)
            command,
            shell=True,
            cwd=workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",  # agent CLIs emit UTF-8; never crash on Windows locale codecs
            env={**os.environ, **(env or {})},
        )
        return ShellResult(stdout=proc.stdout, stderr=proc.stderr, exit_code=proc.returncode)

    def popen(
        self, workspace: str, command: str, env: dict[str, str] | None = None
    ) -> "subprocess.Popen[str]":
        """Start an interactive process with the workspace as its working directory."""
        args = list(command) if isinstance(command, (list, tuple)) else shlex.split(
            command, posix=(os.name != "nt")  # keep Windows backslash paths intact
        )
        self._note(f"starting interactive agent: {command}")
        return subprocess.Popen(  # noqa: S603 - launching the user-specified agent is the point
            args, cwd=workspace,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            env={**os.environ, **(env or {})},
        )

    def stage(self, workspace: str, host_src: str, dest: str) -> None:
        # Same CRLF normalisation as the docker sandbox: on a POSIX host, a grader checked out with
        # Windows line endings is equally unrunnable and would look like the agent scoring zero.
        with normalized_source(host_src) as staged_src:
            src = Path(staged_src)
            dst = Path(workspace) / dest.lstrip("/\\")
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            elif src.exists():
                shutil.copy2(src, dst)

    def export_workspace(self, workspace: str, dest_dir: str) -> None:
        dest = Path(dest_dir)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(workspace, dest, dirs_exist_ok=True)

    def list_files(self, workspace: str) -> dict[str, str]:
        root = Path(workspace)
        snapshot: dict[str, str] = {}
        for p in root.rglob("*"):
            if p.is_file():
                snapshot[p.relative_to(root).as_posix()] = _sha256(p)
        return snapshot

    def cleanup(self, workspace: str) -> None:
        shutil.rmtree(workspace, ignore_errors=True)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
