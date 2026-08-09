"""Docker sandbox — runs each attempt in an isolated container, faithful to SkillsBench.

Two build paths (one image per task × harness, layer-cached across attempts):
  * **skillbench** — build the task's own ``environment/Dockerfile`` (context = ``environment/``),
    exactly as SkillsBench intends (assets staged at their real paths, deps installed), then an
    overlay layer installs the harness CLI.
  * **generic** — synthesize a Dockerfile from ``docker_base`` (+ optional ``docker_setup``),
    then the same harness overlay.

Per attempt: fresh container → stage generic workspace files → write the prompt file → inject
skills into the container **HOME** per the harness's ``skill_dirs`` (``/root/.claude/skills`` …,
SkillsBench-faithful, cwd-independent) → run → snapshot/diff → export (credentials scrubbed) →
remove container. ``verifier/``/``oracle/`` are never copied in.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from adarubric.core.contracts import PROMPT_RELPATH, SKILL_INJECT_IGNORE, Harness, Sandbox
from adarubric.core.errors import SandboxUnavailable
from adarubric.core.models import EvalSpec, ShellResult
from adarubric.sandboxes.staging import needs_normalising, normalized_source

_HOME = "/root"
_GENERIC_WORKDIR = "/workspace"
_DEFAULT_BASE = "python:3.12-slim"

# Substrings that mark "the daemon isn't reachable" rather than "your build/command was wrong".
# Docker phrases this differently per platform (npipe on Windows, unix socket elsewhere), and the
# message travels on stderr of whatever subcommand happened to run first.
_DAEMON_DOWN_MARKERS = (
    "cannot connect to the docker daemon",
    "error during connect",
    "the docker daemon is not running",
    "is the docker daemon running",
    "failed to connect to the docker api",
    "docker_host",
    "//./pipe/docker",
    "/var/run/docker.sock",
)


def _is_daemon_down(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in _DAEMON_DOWN_MARKERS)


def _raise_docker_failure(label: str, res: ShellResult) -> None:
    """Raise for a failed docker command, classified by *whose* fault it is.

    Daemon unreachable (it was up at preflight and died since, or Desktop restarted) →
    :class:`SandboxUnavailable`, which aborts the run without recording a trial. Anything else — a
    broken Dockerfile, a failing ``RUN`` step — is a genuine task/environment defect and stays a
    ``RuntimeError``, i.e. a real failed trial.
    """
    detail = res.stderr or res.stdout or ""
    if _is_daemon_down(detail):
        raise SandboxUnavailable(
            f"Docker stopped responding during {label} - the daemon went away mid-run. "
            "Start Docker Desktop and re-run."
        )
    raise RuntimeError(f"{label} failed:\n{detail[-3000:]}")

# Files that may hold credentials — removed from exports so keys never land in output/.
_EXPORT_SCRUB = (
    ".codex/auth.json",
    ".claude/.credentials.json",
    ".gemini/oauth_creds.json",
    ".config/gemini/oauth_creds.json",
)
# Bulky runtime junk pruned from exports.
_EXPORT_PRUNE = (".npm", ".cache", ".local/share", ".local/state")

_OVERLAY_PREREQS = (
    "apt-get update && apt-get install -y --no-install-recommends curl ca-certificates "
    "&& rm -rf /var/lib/apt/lists/*"
)


def _docker(*args: str, input_text: str | None = None, timeout: int = 1800) -> ShellResult:
    """Run a docker CLI command with list args (no shell quoting pitfalls)."""
    try:
        proc = subprocess.run(
            ["docker", *args], capture_output=True, text=True, input=input_text,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except FileNotFoundError as exc:  # docker CLI not installed — our environment, not the agent's
        raise SandboxUnavailable(
            "Docker CLI not found on PATH. Install Docker Desktop (or use --sandbox local)."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"docker {' '.join(args[:2])} timed out") from exc
    return ShellResult(stdout=proc.stdout, stderr=proc.stderr, exit_code=proc.returncode)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9.-]+", "-", text.lower()).strip("-") or "task"


class DockerSandbox(Sandbox):
    name = "docker"

    def __init__(self) -> None:
        self._images: dict[tuple[str, str], str] = {}  # (task, harness) -> image tag
        self._workdirs: dict[str, str] = {}  # container id -> workdir
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ preflight

    def preflight(self) -> None:
        """Ping the daemon before the run creates anything.

        A stopped Docker Desktop is the single most common way a run dies, and without this check it
        died *late* — after the output tree, eval.yaml and a ``success: false`` run.json had been
        written, which reads as "the agent failed" on the dashboard. Here it costs one fast
        ``docker info`` and fails with one actionable line, having written nothing.
        """
        res = _docker("info", "--format", "{{.ServerVersion}}", timeout=30)
        if res.exit_code == 0 and res.stdout.strip():
            return
        detail = (res.stderr or res.stdout or "").strip()
        if _is_daemon_down(detail) or not detail:
            raise SandboxUnavailable(
                "Docker isn't running - can't reach the daemon. Start Docker Desktop, wait until it "
                "reports running, then re-run. (Or use --sandbox local, which needs no Docker.)"
            )
        raise SandboxUnavailable(f"Docker is not usable: {detail[-500:]}")

    # ------------------------------------------------------------------ build

    def prepare(self, spec: EvalSpec, harness: Harness, env: dict[str, str] | None = None) -> str:
        """Build (or reuse) the task+harness image. Idempotent; docker layer cache makes reruns fast."""
        key = (spec.name, harness.name)
        with self._lock:
            if key in self._images:
                return self._images[key]

        if spec.mode == "skillbench" and spec.dockerfile:
            base_tag = f"adarubric/{_slug(spec.name)}:task"
            context = str(Path(spec.dockerfile).parent)  # environment/ — COPY paths resolve here
            self._note(f"building task image from {spec.dockerfile} (this can take a few minutes)…")
            res = _docker("build", "-t", base_tag, "-f", spec.dockerfile, context)
            if res.exit_code != 0:
                _raise_docker_failure("docker build (task)", res)
        else:
            base_tag = f"adarubric/{_slug(spec.name)}:task"
            self._note(f"building base image FROM {spec.docker_base or _DEFAULT_BASE}…")
            lines = [f"FROM {spec.docker_base or _DEFAULT_BASE}",
                     "ENV DEBIAN_FRONTEND=noninteractive"]
            if spec.docker_setup:
                setup = " && ".join(
                    s.strip() for s in spec.docker_setup.strip().splitlines() if s.strip()
                )
                lines.append(f"RUN {setup}")
            res = self._build_synth(base_tag, "\n".join(lines))
            if res.exit_code != 0:
                _raise_docker_failure("docker build (generic base)", res)

        # Overlay: prereqs + harness CLI. Skipped when the harness has nothing to install.
        tag = f"adarubric/{_slug(spec.name)}-{_slug(harness.name)}:latest"
        overlay = [f"FROM {base_tag}", "ENV DEBIAN_FRONTEND=noninteractive"]
        if harness.docker_install:
            self._note(f"installing the {harness.name} CLI into the image…")
            overlay.append(f"RUN {_OVERLAY_PREREQS} || true")
            overlay.append(f"RUN {harness.docker_install}")
        overlay.append('ENV PATH="/usr/local/bin:/root/.local/bin:${PATH}"')
        res = self._build_synth(tag, "\n".join(overlay))
        if res.exit_code != 0:
            _raise_docker_failure("docker build (harness overlay)", res)
        self._note(f"image ready: {tag}")

        with self._lock:
            self._images[key] = tag
        return tag

    @staticmethod
    def _build_synth(tag: str, dockerfile_text: str) -> ShellResult:
        with tempfile.TemporaryDirectory(prefix="adarubric-ctx-") as ctx:
            (Path(ctx) / "Dockerfile").write_text(dockerfile_text, encoding="utf-8")
            return _docker("build", "-t", tag, ctx)

    # ------------------------------------------------------------------ per-attempt

    def setup(self, spec: EvalSpec, harness: Harness, env: dict[str, str] | None = None) -> str:
        image = self.prepare(spec, harness, env)

        run_args = ["run", "-d"]
        for k, v in (env or {}).items():
            run_args += ["-e", f"{k}={v}"]
        run_args += [image, "sh", "-c", "sleep infinity"]
        self._note(f"starting container from {image}…")
        res = _docker(*run_args)
        if res.exit_code != 0:
            _raise_docker_failure("docker run", res)
        cid = res.stdout.strip()

        # Workdir: the task Dockerfile's WORKDIR when set, else /workspace.
        insp = _docker("image", "inspect", "-f", "{{.Config.WorkingDir}}", image)
        workdir = insp.stdout.strip() or ""
        if not workdir or workdir == "/":
            workdir = _GENERIC_WORKDIR
        _docker("exec", cid, "sh", "-c", f"mkdir -p '{workdir}'")
        self._workdirs[cid] = workdir

        # Stage generic workspace inputs (skillbench assets are already in the image via COPY).
        staging = {f: Path(f).name for f in spec.workspace_files}
        staging.update(spec.workspace_map)
        for src_s, dest_rel in staging.items():
            src = Path(src_s)
            if not src.exists():
                continue
            dest = f"{workdir}/{dest_rel}".replace("//", "/")
            parent = dest.rsplit("/", 1)[0]
            _docker("exec", cid, "sh", "-c", f"mkdir -p '{parent}'")
            self._note(f"copy → {dest} ({src.name})")
            _docker("cp", str(src), f"{cid}:{dest}")

        # Prompt file.
        with tempfile.TemporaryDirectory(prefix="adarubric-prompt-") as td:
            pfile = Path(td) / "prompt.md"
            pfile.write_text(spec.instruction, encoding="utf-8")
            _docker("exec", cid, "sh", "-c", f"mkdir -p '{workdir}/.adarubric'")
            _docker("cp", str(pfile), f"{cid}:{workdir}/{PROMPT_RELPATH}")

        # Inject skills into the container HOME per this harness's discovery dirs
        # (SkillsBench-faithful: /root/.claude/skills etc; cwd-independent).
        if not spec.inject_skills:
            withheld = ", ".join(Path(p).name for p in spec.skill_paths) or "none"
            self._note(f"skills NOT injected (--inject-skills no) — withheld: {withheld}")
        for d in harness.skill_dirs if spec.inject_skills else ():
            base = f"{_HOME}/{d}"
            _docker("exec", cid, "sh", "-c", f"mkdir -p '{base}'")
            for spath in spec.skill_paths:
                sp = Path(spath)
                if sp.is_dir():
                    self._note(f"inject skill '{sp.name}' → {base}/ (control files stripped)")
                    _docker("cp", str(sp), f"{cid}:{base}/")
                    # Strip AdaRubric control files from the injected skill (grader/task never leak).
                    victims = " ".join(f"'{base}/{sp.name}/{n}'" for n in SKILL_INJECT_IGNORE)
                    _docker("exec", cid, "sh", "-c", f"rm -rf {victims}")

        self._note("workspace ready — handing off to the agent")
        return cid

    def run_command(self, workspace: str, command: str, env: dict[str, str] | None = None) -> ShellResult:
        args = ["exec"]
        for k, v in (env or {}).items():
            args += ["-e", f"{k}={v}"]
        args += ["-w", self._workdirs.get(workspace, _GENERIC_WORKDIR), workspace, "sh", "-lc", command]
        return _docker(*args, timeout=3600)

    # ------------------------------------------------------------------ capture

    def _capture_roots(self, cid: str) -> list[str]:
        workdir = self._workdirs.get(cid, _GENERIC_WORKDIR)
        roots = [workdir]
        if workdir != _HOME and not workdir.startswith(_HOME + "/"):
            roots.append(_HOME)
        return roots

    # Hash strategies, tried in order per root — each runs SEPARATELY (never shell-chained with
    # `||`/`&&`, whose equal precedence once made all three run and pollute the snapshot).
    _HASHERS = (
        "find . -type f -print0 | xargs -0 -r sha256sum",
        "find . -type f -print0 | xargs -0 -r md5sum",
        # Last resort (no coreutils hashers): size+mtime as a pseudo-digest, tab-separated.
        "find . -type f -exec sh -c 'wc -c < \"$1\" | tr -d \"[:space:]\"; printf \"_\"; "
        "date -r \"$1\" +%s 2>/dev/null; printf \" %s\\n\" \"$1\"' _ {} \\;",
    )

    def list_files(self, workspace: str) -> dict[str, str]:
        snapshot: dict[str, str] = {}
        for root in self._capture_roots(workspace):
            for hasher in self._HASHERS:
                res = _docker("exec", workspace, "sh", "-c", f"cd '{root}' 2>/dev/null && {hasher}")
                lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
                if res.exit_code == 0 and lines:
                    for line in lines:
                        parts = line.strip().split(None, 1)
                        if len(parts) == 2:
                            digest, relpath = parts
                            snapshot[f"{root}/{relpath.lstrip('./')}"] = digest
                    break
                # Empty dir with exit 0 and no lines → nothing to record; stop trying hashers.
                if res.exit_code == 0:
                    break
        return snapshot

    def popen(
        self, workspace: str, command: str, env: dict[str, str] | None = None
    ) -> "subprocess.Popen[str]":
        """Start an interactive process INSIDE the container via ``docker exec -i``.

        This is the ACP bridge. ``-i`` keeps stdin open so the container process's stdin/stdout become
        the JSON-RPC channel; no ``-t``, because a TTY would inject control characters and corrupt the
        newline-delimited protocol. The agent therefore runs next to the task's real files, which is
        what makes SkillsBench tasks (docker-only) reachable over ACP at all.
        """
        args = ["docker", "exec", "-i", "-w", self._workdirs.get(workspace, _GENERIC_WORKDIR)]
        for k, v in (env or {}).items():
            args += ["-e", f"{k}={v}"]
        cmd = command if isinstance(command, str) else " ".join(command)
        args += [workspace, "sh", "-lc", cmd]
        self._note(f"starting interactive agent in the container: {cmd}")
        return subprocess.Popen(  # noqa: S603 - fixed docker argv, user command passed to the shell
            args,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )

    def stage(self, workspace: str, host_src: str, dest: str) -> None:
        # dest: absolute container path (e.g. /verifier), or workdir-relative (grader helper files
        # like `run: node graders/check.js`, which must land next to where the command runs).
        if not dest.startswith("/"):
            dest = f"{self._workdirs.get(workspace, _GENERIC_WORKDIR)}/{dest}"
        self._note(f"staging grader → {dest} (after the agent finished)")
        parent = dest.rsplit("/", 1)[0] or "/"
        _docker("exec", workspace, "sh", "-c", f"mkdir -p '{parent}'")
        # A grader checked out with Windows line endings is unrunnable by the container's Linux
        # shell and fails in ways that look like the agent scoring zero. Normalise on the way in.
        if needs_normalising(host_src):
            self._note("normalising Windows line endings in the grader (CRLF → LF)")
        with normalized_source(host_src) as staged_src:
            src = Path(staged_src)
            # `docker cp <dir>/. <cid>:<dest>` copies contents into dest (like the dir itself).
            srcarg = f"{staged_src}/." if src.is_dir() else staged_src
            _docker("exec", workspace, "sh", "-c", f"mkdir -p '{dest}'" if src.is_dir() else "true")
            _docker("cp", srcarg, f"{workspace}:{dest}")

    def export_workspace(self, workspace: str, dest_dir: str) -> None:
        self._note("exporting the agent's final workspace (credentials scrubbed)…")
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        for root in self._capture_roots(workspace):
            label = "home" if root == _HOME else (Path(root).name or "workspace")
            final = dest / label
            if final.exists():
                shutil.rmtree(final, ignore_errors=True)
            # Trailing "/." makes docker copy the directory's CONTENTS into an existing folder, so
            # there is no temp name and no rename afterwards. The old temp+rename dance is what hit
            # "[WinError 5] Access is denied": Windows refuses to rename a folder while anything
            # holds a file inside it, and Defender is always mid-scan on a tree docker just wrote.
            # Removing the rename removes the failure entirely — no OS-specific branch needed.
            final.mkdir(parents=True, exist_ok=True)
            _docker("cp", f"{workspace}:{root}/.", str(final))
        # Scrub credentials + prune runtime junk so secrets/bulk never land in output/.
        for rel in _EXPORT_SCRUB:
            f = dest / "home" / rel
            if f.is_file():
                f.unlink()
        for rel in _EXPORT_PRUNE:
            d = dest / "home" / rel
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)

    # ------------------------------------------------------------------ teardown

    def cleanup(self, workspace: str) -> None:
        _docker("rm", "-f", workspace)
        self._workdirs.pop(workspace, None)

    def diagnose(self, workspace: str) -> str:
        logs = _docker("logs", "--tail", "50", workspace)
        return (logs.stdout + logs.stderr)[-2000:]
