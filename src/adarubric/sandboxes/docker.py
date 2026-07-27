"""Docker sandbox — runs each attempt in an isolated container, faithful to SkillsBench.

Two build paths (one image per task × harness, layer-cached across attempts):
  * **skillbench** — build the task's own ``environment/Dockerfile`` (context = ``environment/``),
    exactly as SkillsBench intends (assets staged at their real paths, deps installed), then an
    overlay layer installs the harness CLI.
  * **generic** — synthesize a Dockerfile from ``docker_base`` (+ optional ``docker_setup``,
    skillgrade-style), then the same harness overlay.

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

from adarubric.core.contracts import PROMPT_RELPATH, Harness, Sandbox
from adarubric.core.models import EvalSpec, ShellResult

_HOME = "/root"
_GENERIC_WORKDIR = "/workspace"
_DEFAULT_BASE = "python:3.12-slim"

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
    except FileNotFoundError as exc:  # docker CLI not installed
        raise RuntimeError("docker CLI not found — is Docker installed and on PATH?") from exc
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
            res = _docker("build", "-t", base_tag, "-f", spec.dockerfile, context)
            if res.exit_code != 0:
                raise RuntimeError(f"docker build (task) failed:\n{res.stderr[-3000:]}")
        else:
            base_tag = f"adarubric/{_slug(spec.name)}:task"
            lines = [f"FROM {spec.docker_base or _DEFAULT_BASE}",
                     "ENV DEBIAN_FRONTEND=noninteractive"]
            if spec.docker_setup:
                setup = " && ".join(
                    s.strip() for s in spec.docker_setup.strip().splitlines() if s.strip()
                )
                lines.append(f"RUN {setup}")
            res = self._build_synth(base_tag, "\n".join(lines))
            if res.exit_code != 0:
                raise RuntimeError(f"docker build (generic base) failed:\n{res.stderr[-3000:]}")

        # Overlay: prereqs + harness CLI. Skipped when the harness has nothing to install.
        tag = f"adarubric/{_slug(spec.name)}-{_slug(harness.name)}:latest"
        overlay = [f"FROM {base_tag}", "ENV DEBIAN_FRONTEND=noninteractive"]
        if harness.docker_install:
            overlay.append(f"RUN {_OVERLAY_PREREQS} || true")
            overlay.append(f"RUN {harness.docker_install}")
        overlay.append('ENV PATH="/usr/local/bin:/root/.local/bin:${PATH}"')
        res = self._build_synth(tag, "\n".join(overlay))
        if res.exit_code != 0:
            raise RuntimeError(f"docker build (harness overlay) failed:\n{res.stderr[-3000:]}")

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
        res = _docker(*run_args)
        if res.exit_code != 0:
            raise RuntimeError(f"docker run failed:\n{res.stderr[-2000:]}")
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
            _docker("cp", str(src), f"{cid}:{dest}")

        # Prompt file.
        with tempfile.TemporaryDirectory(prefix="adarubric-prompt-") as td:
            pfile = Path(td) / "prompt.md"
            pfile.write_text(spec.instruction, encoding="utf-8")
            _docker("exec", cid, "sh", "-c", f"mkdir -p '{workdir}/.adarubric'")
            _docker("cp", str(pfile), f"{cid}:{workdir}/{PROMPT_RELPATH}")

        # Inject skills into the container HOME per this harness's discovery dirs
        # (SkillsBench-faithful: /root/.claude/skills etc; cwd-independent).
        for d in harness.skill_dirs:
            base = f"{_HOME}/{d}"
            _docker("exec", cid, "sh", "-c", f"mkdir -p '{base}'")
            for spath in spec.skill_paths:
                sp = Path(spath)
                if sp.is_dir():
                    _docker("cp", str(sp), f"{cid}:{base}/")

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

    def stage(self, workspace: str, host_src: str, dest: str) -> None:
        # dest is an absolute container path (e.g. /verifier). Ensure parent exists, then docker cp.
        parent = dest.rsplit("/", 1)[0] or "/"
        _docker("exec", workspace, "sh", "-c", f"mkdir -p '{parent}'")
        src = Path(host_src)
        # `docker cp <dir>/. <cid>:<dest>` copies contents into dest (like the dir itself).
        srcarg = f"{host_src}/." if src.is_dir() else host_src
        _docker("exec", workspace, "sh", "-c", f"mkdir -p '{dest}'" if src.is_dir() else f"true")
        _docker("cp", srcarg, f"{workspace}:{dest}")

    def export_workspace(self, workspace: str, dest_dir: str) -> None:
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        for root in self._capture_roots(workspace):
            label = "home" if root == _HOME else (Path(root).name or "workspace")
            _docker("cp", f"{workspace}:{root}", str(dest / f"__tmp_{label}"))
            tmp = dest / f"__tmp_{label}"
            final = dest / label
            if final.exists():
                shutil.rmtree(final, ignore_errors=True)
            tmp.rename(final)
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
