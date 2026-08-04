"""EvalRunner — orchestrates a run using only the contracts.

Vocabulary: an **attempt** is one launch of the command; a **trial** is one repetition inside it.
Output layout:
    output/<harness>/<task>/attempt-<A>/
        eval.yaml                     # the run definition (written BEFORE trials; host-only)
        trial-<T>/
            run.json transcript.json changes.json grading.json prompt.md raw.log workspace/

Per trial:
  resolve env keys → prepare (docker build) → setup → snapshot → harness.run (with timeout) →
  snapshot (→ diff) → export_workspace → **grade** (after the agent is gone) → write artifacts →
  cleanup. ProgressEvents are emitted at each TrialStage.

Isolation: the grader/verifier is staged and run only AFTER export — the agent never sees it; and
`verifier/`/`oracle/`/the eval.yaml manifest are never placed in the sandbox workspace.
"""

from __future__ import annotations

import json
import os
import platform
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import yaml

from adarubric import __version__
from adarubric.core.contracts import Harness, ProgressReporter, Sandbox
from adarubric.core.errors import SandboxUnavailable
from adarubric.core.models import (
    EvalReport,
    EvalSpec,
    GraderResult,
    GraderSpec,
    ProgressEvent,
    RunMeta,
    RunOutput,
    ShellResult,
    SkillUsage,
    Timing,
    TranscriptEntry,
    Trial,
    TrialStage,
    Usage,
    WorkspaceChanges,
)
from adarubric.core.pricing import estimate_cost, is_specific_model
from adarubric.core.skill_depth import classify as classify_skill_depth
from adarubric.grading import create_grader
from adarubric.harnesses.oracle import ORACLE_DIR


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(name: str) -> str:
    return re.sub(r"[^\w.-]+", "-", name).strip("-") or "task"


def _write_json(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")


def _diff(before: dict[str, str], after: dict[str, str]) -> WorkspaceChanges:
    return WorkspaceChanges(
        created=sorted(set(after) - set(before)),
        deleted=sorted(set(before) - set(after)),
        modified=sorted(k for k in before if k in after and before[k] != after[k]),
    )


class EvalRunner:
    def __init__(
        self,
        sandbox: Sandbox,
        output_root: str = "output",
        reporter: ProgressReporter | None = None,
        grade: bool = True,
    ) -> None:
        self.sandbox = sandbox
        self.output_root = output_root
        self.reporter = reporter  # None → no live tracking; guarded at every emit
        self.grade = grade

    # ------------------------------------------------------------------ public

    def run(
        self,
        harness: Harness,
        spec: EvalSpec,
        trials: int = 1,
        env: dict[str, str] | None = None,
    ) -> EvalReport:
        """One attempt (launch): create attempt-<A>/, write eval.yaml, run `trials` trials."""
        task = _safe(spec.name)
        base = Path(self.output_root) / _safe(harness.name) / task
        attempt = 1
        while (base / f"attempt-{attempt}").exists():
            attempt += 1
        attempt_dir = base / f"attempt-{attempt}"
        attempt_dir.mkdir(parents=True, exist_ok=True)

        self._emit("attempt_started", harness.name, task, attempt, None)
        # Definition of THIS attempt, written before any trial runs (host-only; never enters sandbox).
        manifest = self._manifest(spec, harness)
        eval_yaml = attempt_dir / "eval.yaml"
        eval_yaml.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

        results: list[Trial] = []
        try:
            for i in range(trials):
                results.append(self._run_trial(harness, spec, attempt, i + 1, attempt_dir, env))
        except SandboxUnavailable:
            # The environment died on us (daemon stopped mid-run). Don't leave a half-written
            # attempt behind pretending to be a result — if nothing completed, erase it entirely so
            # the next run reuses attempt-N and the dashboard shows no phantom failure.
            if not results:
                import shutil

                shutil.rmtree(attempt_dir, ignore_errors=True)
            raise

        # Record the model(s) the harness ACTUALLY reported across the trials (the configured
        # `model` above is what we requested; this is what the CLI really ran — useful when we let
        # the CLI pick its own default, and to confirm a pin took effect).
        observed = sorted({r.meta.model for r in results if r.meta.model})
        if observed:
            manifest["harness"]["model_observed"] = observed
            eval_yaml.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

        report = EvalReport(
            task=spec.name,
            harness=harness.name,
            attempt=attempt,
            trials=results,
            skills_used=[Path(p).name for p in spec.skill_paths],
            output_dir=str(attempt_dir),
        )
        self._emit("attempt_finished", harness.name, task, attempt, None)
        return report

    # ------------------------------------------------------------------ one trial

    def _run_trial(
        self,
        harness: Harness,
        spec: EvalSpec,
        attempt: int,
        trial_id: int,
        attempt_dir: Path,
        env: dict[str, str] | None,
    ) -> Trial:
        out_dir = attempt_dir / f"trial-{trial_id}"

        run_env = dict(env or {})
        for key in getattr(harness, "env_keys", ()) or ():
            if key in os.environ:
                run_env[key] = os.environ[key]
        env_key_used = next((k for k in (getattr(harness, "env_keys", ()) or ()) if k in run_env), None)
        secrets = [v for v in run_env.values() if v and len(v) > 5]

        def redact(text: str) -> str:
            for s in secrets:
                text = text.replace(s, "[REDACTED]")
            return text

        transcript: list[TranscriptEntry] = [
            TranscriptEntry(type="run_start", timestamp=_now(), instruction=spec.instruction)
        ]
        command_count = 0
        started_at = _now()
        t_start = time.perf_counter()

        self._emit("trial_started", harness.name, _safe(spec.name), attempt, TrialStage.QUEUED, trial=trial_id)

        # --- prepare (docker build; no-op for local; idempotent) ---
        self._emit("stage_changed", harness.name, _safe(spec.name), attempt, TrialStage.PREPARING, trial=trial_id)
        try:
            self.sandbox.prepare(spec, harness, run_env)
        except SandboxUnavailable:
            raise  # our infrastructure, not the agent — never scored as a failed trial
        except Exception as exc:  # noqa: BLE001
            return self._failed_trial(harness, spec, trial_id, out_dir, started_at, transcript,
                                      str(exc), t_start, attempt)

        # --- setup ---
        self._emit("stage_changed", harness.name, _safe(spec.name), attempt, TrialStage.SETTING_UP, trial=trial_id)
        t = time.perf_counter()
        workspace = self.sandbox.setup(spec, harness, run_env)
        setup_ms = (time.perf_counter() - t) * 1000

        run_ms: float | None = None
        export_ms: float | None = None
        timed_out = False
        error: str | None = None
        export_error: str | None = None
        success = False
        run_output = RunOutput(output="")
        changes = WorkspaceChanges()
        grader_results: list[GraderResult] = []
        reward = 0.0
        graded = False
        grading_error: str | None = None

        # The oracle harness needs the task's reference solution inside the sandbox. Staged ONLY for
        # that harness, before the snapshot so its own files aren't counted as the agent's changes.
        # A real agent never reaches this branch, so a worked answer cannot leak into a scored run.
        # Deliberately OUTSIDE the try below: asking for the oracle on a task that has none is a
        # setup mistake, and must stop rather than be filed as a trial the oracle "failed".
        if getattr(harness, "runs_oracle", False):
            if not spec.oracle_path:
                self.sandbox.cleanup(workspace)
                raise RuntimeError(
                    "harness 'oracle' needs the task's oracle/solve.sh, but this task has none"
                )
            self.sandbox.stage(workspace, str(Path(spec.oracle_path).parent), ORACLE_DIR)

        try:
            before = self.sandbox.list_files(workspace)

            def logged(command: str, cmd_env: dict[str, str] | None = None) -> ShellResult:
                nonlocal command_count
                res = self.sandbox.run_command(workspace, command, {**run_env, **(cmd_env or {})})
                command_count += 1
                transcript.append(TranscriptEntry(
                    type="command", timestamp=_now(), command=redact(command),
                    stdout=redact(res.stdout), stderr=redact(res.stderr), exit_code=res.exit_code))
                return res

            self._emit("stage_changed", harness.name, _safe(spec.name), attempt, TrialStage.RUNNING, trial=trial_id)
            t = time.perf_counter()
            run_output = _run_with_timeout(
                lambda: harness.run(spec.instruction, workspace, logged), spec.timeout_sec)
            run_ms = (time.perf_counter() - t) * 1000
            transcript.append(TranscriptEntry(type="run_output", timestamp=_now(),
                                               output=redact(run_output.output)))

            # Snapshot + export the AGENT'S state BEFORE any grader touches the workspace.
            self._emit("stage_changed", harness.name, _safe(spec.name), attempt, TrialStage.EXPORTING, trial=trial_id)
            after = self.sandbox.list_files(workspace)
            changes = _diff(before, after)
            t = time.perf_counter()
            try:
                self.sandbox.export_workspace(workspace, str(out_dir / "workspace"))
            except Exception as xexc:  # noqa: BLE001
                # Exporting is archival: it copies the agent's files out for later inspection. If it
                # fails (a Windows file lock, a full disk) the RUN still happened and can still be
                # graded — the grader reads the live container, not the export. Losing minutes of
                # work and real API spend because a copy failed is never the right trade.
                export_error = str(xexc)
                self._emit("stage_changed", harness.name, _safe(spec.name), attempt,
                           TrialStage.EXPORTING, trial=trial_id)
            export_ms = (time.perf_counter() - t) * 1000

            if run_output.error:
                error = run_output.error
            else:
                success = True
                # --- grade (only now; the agent has finished and its state is captured) ---
                specs = self._grader_specs(spec)
                if self.grade and specs:
                    self._emit("stage_changed", harness.name, _safe(spec.name), attempt, TrialStage.GRADING, trial=trial_id)
                    for gs in specs:
                        try:
                            grader_results.append(
                                create_grader(gs.type).grade(workspace, self.sandbox, gs, spec, transcript, run_env))
                        except Exception as gexc:  # noqa: BLE001 - a crashing grader must not kill the run
                            grader_results.append(GraderResult(
                                gs.type, 0.0, gs.weight, f"grader error: {gexc}",
                                error=f"grader crashed: {gexc}"))
                    # Only results that reached a verdict count toward the reward. If NONE did, the
                    # run is unscored — reporting reward 0.0 here would blame the agent for a check
                    # script that never ran (a broken verifier, the wrong sandbox, a crash).
                    scored = [g for g in grader_results if g.error is None]
                    problems = [g.error for g in grader_results if g.error]
                    grading_error = "; ".join(problems) if problems else None
                    if scored:
                        reward = _weighted(scored)
                        graded = True
                    else:
                        reward, graded = 0.0, False
        except FuturesTimeout:
            timed_out = True
            error = f"harness timed out after {spec.timeout_sec}s"
        except SandboxUnavailable:
            raise  # abort the run; a dead sandbox is not an agent failure (finally still cleans up)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
        finally:
            self.sandbox.cleanup(workspace)

        total_ms = (time.perf_counter() - t_start) * 1000
        stage = TrialStage.DONE if success else (TrialStage.TIMED_OUT if timed_out else TrialStage.FAILED)

        meta = self._build_meta(
            harness=harness, spec=spec, run_output=run_output, env_key_used=env_key_used,
            started_at=started_at, success=success, timed_out=timed_out, error=error,
            command_count=command_count, changes=changes,
            timing=Timing(total_ms=total_ms, setup_ms=setup_ms, run_ms=run_ms, export_ms=export_ms),
            reward=reward, graded=graded, grading_error=grading_error, export_error=export_error)

        raw = redact(run_output.raw_output or run_output.output or "")
        out_dir.mkdir(parents=True, exist_ok=True)
        _write_json(out_dir / "run.json", asdict(meta))
        _write_json(out_dir / "transcript.json", [asdict(e) for e in transcript])
        _write_json(out_dir / "changes.json", asdict(changes))
        # Written whenever grading was ATTEMPTED — including when it failed, so the evidence for
        # *why* there's no score survives in the artifacts instead of vanishing.
        if grader_results:
            _write_json(out_dir / "grading.json",
                        {"reward": reward if graded else None, "graded": graded,
                         "grading_error": grading_error,
                         "grader_results": [asdict(g) for g in grader_results]})
        (out_dir / "prompt.md").write_text(redact(spec.instruction), encoding="utf-8")
        (out_dir / "raw.log").write_text(raw, encoding="utf-8")

        trial = Trial(trial_id=trial_id, meta=meta, changes=changes, transcript=transcript,
                      raw_log=raw, reward=reward, graded=graded, grader_results=grader_results,
                      output_dir=str(out_dir))
        self._emit("trial_finished", harness.name, _safe(spec.name), attempt, stage,
                   reward=(reward if graded else None), meta=meta, trial=trial_id)
        return trial

    # ------------------------------------------------------------------ helpers

    def _grader_specs(self, spec: EvalSpec) -> list[GraderSpec]:
        """The graders to apply: the SkillsBench verifier (skillbench) or the config's graders."""
        if spec.mode == "skillbench" and spec.verifier_path:
            return [GraderSpec(type="skillbench_verifier", weight=1.0)]
        return list(spec.graders)

    def _manifest(self, spec: EvalSpec, harness: Harness) -> dict:
        """The eval.yaml definition written into the attempt folder (host-only; no secret values)."""
        return {
            "name": spec.name,
            "mode": spec.mode,
            "instruction": spec.instruction,
            "harness": {
                "id": harness.name,
                "cli": harness.cli,
                # Two distinct facts, never collapsed into one prose string: what we pinned, and
                # (added after the trials) what the CLI reported actually running. `null` here means
                # "we pinned nothing" — read `model_observed` for the name that was really used.
                "model_requested": harness.model,
                "skill_dirs": list(harness.skill_dirs),
                "env_keys": list(harness.env_keys),  # names only, never values
            },
            "environment": {
                "sandbox": self.sandbox.name,
                "dockerfile": spec.dockerfile,
                "docker_base": spec.docker_base,
                "docker_setup": spec.docker_setup,
                "workspace_files": [Path(p).name for p in spec.workspace_files]
                + list(spec.workspace_map.values()),
            },
            "skills": [Path(p).name for p in spec.skill_paths],
            # False = the skills above existed but were deliberately withheld (--inject-skills no).
            # Without this line a control run is indistinguishable from a task that has no skills.
            "skills_injected": spec.inject_skills,
            "grading": {
                "verifier": spec.verifier_path,
                "oracle": spec.oracle_path,
                "graders": [g.type for g in spec.graders],
            },
            "timeout_sec": spec.timeout_sec,
            "adarubric_version": __version__,
            "written_at": _now(),
        }

    def _failed_trial(self, harness, spec, trial_id, out_dir, started_at, transcript, error,
                      t_start, attempt) -> Trial:
        total_ms = (time.perf_counter() - t_start) * 1000
        meta = self._build_meta(
            harness=harness, spec=spec, run_output=RunOutput(output=""), env_key_used=None,
            started_at=started_at, success=False, timed_out=False, error=error, command_count=0,
            changes=WorkspaceChanges(), timing=Timing(total_ms=total_ms), reward=0.0, graded=False)
        out_dir.mkdir(parents=True, exist_ok=True)
        _write_json(out_dir / "run.json", asdict(meta))
        _write_json(out_dir / "transcript.json", [asdict(e) for e in transcript])
        (out_dir / "prompt.md").write_text(spec.instruction, encoding="utf-8")
        trial = Trial(trial_id=trial_id, meta=meta, transcript=transcript, output_dir=str(out_dir))
        self._emit("trial_finished", harness.name, _safe(spec.name), attempt, TrialStage.FAILED,
                   trial=trial_id, meta=meta)
        return trial

    def _build_meta(self, *, harness, spec, run_output, env_key_used, started_at, success,
                    timed_out, error, command_count, changes, timing, reward, graded,
                    grading_error=None, export_error=None) -> RunMeta:
        in_tok, out_tok = run_output.input_tokens, run_output.output_tokens
        total_tok = run_output.total_tokens
        if total_tok is None and in_tok is not None and out_tok is not None:
            total_tok = in_tok + out_tok
        # Price against the model we PINNED when the CLI doesn't name one. codex reports no model at
        # all, so pricing only the observed name left every codex run showing no cost despite real
        # token spend — and we do know what ran, because we passed it on the command line.
        # ...and a reported name like "auto" or "Default (recommended)" is a routing MODE, not a model,
        # so it can't be priced either — fall back to the pin in that case too.
        reported = run_output.model if is_specific_model(run_output.model) else None
        priced_model = reported or getattr(harness, "model", None)
        est = estimate_cost(priced_model, in_tok, out_tok)
        cost_source = ("reported" if run_output.cost_usd is not None
                       else ("estimated" if est is not None else None))
        num_tool_calls = sum(run_output.tool_counts.values()) or (len(run_output.tools_used) or None)

        usage = Usage(
            input_tokens=in_tok, output_tokens=out_tok, total_tokens=total_tok,
            cached_input_tokens=run_output.cached_input_tokens,
            num_turns=run_output.num_turns, num_turns_reported=run_output.num_turns_reported,
            num_tool_calls=num_tool_calls, num_commands=command_count,
            cost_usd=run_output.cost_usd, estimated_cost_usd=est, cost_source=cost_source,
            tools_used=list(run_output.tools_used), tool_counts=dict(run_output.tool_counts))
        if run_output.skill_opened is not None:
            skill_opened = run_output.skill_opened
        else:
            skill_opened = True if run_output.skills_triggered else None
        skill_usage = SkillUsage(
            skill_opened=skill_opened,
            skill_depth=classify_skill_depth(skill_opened, list(run_output.skills_triggered),
                                             spec.skill_paths),
            skills_triggered=list(run_output.skills_triggered),
            skill_files_read=list(run_output.skill_files_read),
            num_skill_files_read=len(run_output.skill_files_read))
        meta = RunMeta(
            harness=harness.name, sandbox=self.sandbox.name, task=spec.name,
            env_key_used=env_key_used, model=run_output.model,
            model_requested=getattr(harness, "model", None), base_image=spec.docker_base,
            platform=platform.platform(), adarubric_version=__version__, started_at=started_at,
            ended_at=_now(), success=success, timed_out=timed_out, error=error,
            graded=graded, reward=reward, grading_error=grading_error, export_error=export_error,
            skills_injected=spec.inject_skills,
            usage=usage, timing=timing, skill_usage=skill_usage,
            files_created=len(changes.created), files_modified=len(changes.modified),
            files_deleted=len(changes.deleted))
        return meta

    def _emit(self, type_: str, harness_name: str, task: str, attempt: int,
              stage: TrialStage | None, trial: int | None = None, reward: float | None = None,
              meta: RunMeta | None = None) -> None:
        if self.reporter is None:
            return
        self.reporter.emit(ProgressEvent(
            type=type_, timestamp=_now(), harness=harness_name, task=task, attempt=attempt,
            trial=trial, stage=stage, reward=reward, meta=meta))


def _weighted(results: list[GraderResult]) -> float:
    total_w = sum(r.weight for r in results)
    if total_w <= 0:
        return 0.0
    return sum(r.score * r.weight for r in results) / total_w


def _run_with_timeout(fn: Callable[[], RunOutput], timeout_sec: int) -> RunOutput:
    """Run ``fn`` with an overall timeout. On timeout the worker thread is abandoned (not killed)."""
    ex = ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(fn)
    try:
        return fut.result(timeout=timeout_sec)
    finally:
        ex.shutdown(wait=False)
