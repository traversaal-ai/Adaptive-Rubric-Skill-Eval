"""AdaRubric command-line interface (Typer).

Thin edge: parse flags, build objects via the registries, delegate to the runner. No business
logic lives here.
"""

from __future__ import annotations

import typer

from adarubric import __version__

app = typer.Typer(
    help="AdaRubric - evaluate how coding agents discover and use Agent Skills.",
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"adarubric {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """AdaRubric skill evaluation harness."""


@app.command()
def run(
    path: str = typer.Argument(
        ..., help="Path to a skill folder or a SkillsBench task package."
    ),
    harness: str = typer.Option(
        ...,
        "--harness",
        help="Harness(es) to test, comma-separated: claude-code | gemini-cli | codex. "
        "Explicit - no auto-detection. Multiple runs the same task on each. Pin a model per "
        "harness with 'name:model', e.g. 'claude-code:claude-opus-4-8,codex:gpt-5-codex' "
        "(falls back to --model, else the CLI's own default).",
    ),
    sandbox: str = typer.Option(
        "local", "--sandbox", help="Where to run: local | docker."
    ),
    model: str = typer.Option(
        None, "--model",
        help="Default model for all harnesses (e.g. claude-opus-4-8, gpt-5-codex, gemini-2.5-pro). "
        "Overridden per harness by 'name:model' in --harness. Passed to the CLI as --model/-m and "
        "recorded in eval.yaml. Omit to use each CLI's own default.",
    ),
    dataset: str = typer.Option(
        "auto",
        "--dataset",
        help="Task pipeline: 'auto' detects from the folder shape (default); "
        "'skillbench' = benchmark mode — the task's own environment/Dockerfile is built, skills go "
        "to per-harness home dirs, verifier/oracle wired for grading (errors if the folder isn't a "
        "SkillsBench task); 'generic' = your own skill/task — shared recipe (docker base+setup from "
        "adarubric.yaml/eval.yaml, or plain local).",
    ),
    instruction: str = typer.Option(
        None, "--instruction",
        help="Task instruction. Overrides task.md / the config file; required for a bare skill folder.",
    ),
    task: str = typer.Option(
        None, "--task", help="Task name to pick from a multi-task eval.yaml (default: first)."
    ),
    output: str = typer.Option(
        "output", "--output",
        help="Output root. Results land in <output>/<harness>/<task>/attempt-N/.",
    ),
    trials: int = typer.Option(
        1, "--trials", help="Repetitions inside this attempt (agents are non-deterministic)."
    ),
    timeout: int = typer.Option(
        None, "--timeout",
        help="Per-harness timeout in seconds (default: config file value, else 300).",
    ),
    grade: bool = typer.Option(
        True, "--grade/--no-grade",
        help="Run deterministic graders after the agent (skillbench verifier / config graders).",
    ),
    env_file: str = typer.Option(
        None, "--env-file", help="Load KEY=VALUE env vars (e.g. API keys) from a file."
    ),
    acp_cmd: str = typer.Option(
        None, "--acp-cmd",
        help="For --harness acp: the ACP agent launch command, e.g. 'gemini --acp'. Required for acp.",
    ),
    acp_env_key: str = typer.Option(
        None, "--acp-env-key",
        help="For --harness acp: comma-separated env var(s) the ACP agent needs (declares them so "
        "they're injected from --env-file and checked up-front). Optional; the agent also inherits "
        "your shell environment.",
    ),
    acp_skill_dir: str = typer.Option(
        None, "--acp-skill-dir",
        help="For --harness acp: the wrapped agent's skill-discovery dir (default '.agents/skills'). "
        "Set it to match the agent (e.g. '.gemini/skills') for a valid skill_opened signal.",
    ),
) -> None:
    """Run a skill/task on one or more harnesses inside an isolated sandbox.

    Works for both a SkillsBench benchmark task (auto-detected: task.md + environment/) and any
    user skill/task (SKILL.md folder, optionally with an adarubric.yaml/eval.yaml supplying the
    instruction, workspace files, and docker recipe). The chosen harness declares which env var it
    needs (e.g. claude-code -> ANTHROPIC_API_KEY); only that key is injected. Missing key -> fail fast.
    """
    import os

    from adarubric.harnesses.registry import create_harness
    from adarubric.loading import load_spec
    from adarubric.reporting.status import FanReporter, StatusReporter
    from adarubric.reporting.terminal import TerminalReporter
    from adarubric.runner import EvalRunner
    from adarubric.sandboxes.registry import create_sandbox

    file_env = _load_env_file(env_file) if env_file else {}
    # Each entry is "name" or "name:model"; a per-harness model overrides the global --model.
    harness_specs = _parse_harness_specs(harness, model)

    spec = load_spec(path, instruction, task=task)

    # --dataset validation / override (auto = trust the detected shape).
    if dataset not in ("auto", "skillbench", "generic"):
        typer.secho(f"Unknown --dataset '{dataset}' (use auto | skillbench | generic).", fg="red")
        raise typer.Exit(code=1)
    if dataset == "skillbench" and spec.mode != "skillbench":
        typer.secho(
            "--dataset skillbench, but this folder isn't a SkillsBench task "
            "(expected task.md + environment/).", fg="red",
        )
        raise typer.Exit(code=1)
    if dataset == "generic" and spec.mode == "skillbench":
        typer.secho(
            "--dataset generic, but this folder is a SkillsBench task — run it with "
            "--dataset skillbench (or auto).", fg="red",
        )
        raise typer.Exit(code=1)

    if timeout is not None:  # explicit flag wins over config-file value
        spec.timeout_sec = timeout
    graders = "skillbench-verifier" if spec.mode == "skillbench" else f"{len(spec.graders)} grader(s)"
    typer.echo(f"mode={spec.mode}  sandbox={sandbox}  task={spec.name}  grade={grade} ({graders})")

    # Live status: written to <output>/status.json as the run happens (stages + activity feed), so a
    # watching dashboard can render it live. Console + status file share one fan-out reporter.
    import os as _os
    status_path = _os.path.join(output, "status.json")
    status = StatusReporter(status_path)
    reporter = FanReporter(TerminalReporter(), status)
    typer.echo(
        f"live status → {status_path}  (live dashboard: python dashboard/serve.py --output {output})"
    )

    for hname, hmodel in harness_specs:
        h = create_harness(hname, model=hmodel)
        # Configure the generic ACP wrapper from its dedicated flags.
        if hname == "acp":
            if not acp_cmd:
                typer.secho("--harness acp requires --acp-cmd (e.g. --acp-cmd 'gemini --acp').", fg="red")
                raise typer.Exit(code=1)
            if sandbox != "local":
                typer.secho("--harness acp currently supports --sandbox local only.", fg="red")
                raise typer.Exit(code=1)
            h.command = acp_cmd
            if acp_env_key:
                h.env_keys = tuple(k.strip() for k in acp_env_key.split(",") if k.strip())
            if acp_skill_dir:
                h.skill_dirs = tuple(d.strip() for d in acp_skill_dir.split(",") if d.strip())
        typer.echo(f"harness={hname}  model={hmodel or 'default (CLI decides)'}")
        # Fail fast if the harness's declared key isn't available (env or --env-file).
        missing = [k for k in h.env_keys if not (os.environ.get(k) or file_env.get(k))]
        if missing:
            typer.secho(
                f"Missing env var(s) for '{hname}': {', '.join(missing)} "
                f"(export them or pass --env-file).",
                fg="red",
            )
            raise typer.Exit(code=1)

        sb = create_sandbox(sandbox)
        sb.activity = status.note  # sandbox reports build/copy/exec steps into the live feed
        runner = EvalRunner(sb, output_root=output, reporter=reporter, grade=grade)
        # Inject ONLY this harness's declared key(s) from the file into the sandbox.
        harness_env = {k: file_env[k] for k in h.env_keys if k in file_env}
        if hname == "acp":
            # ACP is spawned directly (not via run_command), so hand it the injected keys explicitly.
            h.launch_env = harness_env

        report = runner.run(h, spec, trials=trials, env=harness_env)
        for tr in report.trials:
            m = tr.meta
            u = m.usage
            cost = u.cost_usd if u.cost_usd is not None else u.estimated_cost_usd
            reward = f"reward={tr.reward:.2f}" if tr.graded else "ungraded"
            turns = u.num_turns if u.num_turns is not None else "?"
            typer.echo(
                f"    -> {tr.output_dir}\n"
                f"       success={m.success} {reward} turns={turns} tokens={u.total_tokens} "
                f"cost={_fmt_cost(cost)} ({u.cost_source}) "
                f"time={m.timing.total_ms / 1000:.1f}s "
                f"changes=+{m.files_created}/~{m.files_modified}/-{m.files_deleted}"
            )
    status.close()
    typer.echo("done.")


def _parse_harness_specs(harness: str, default_model: str | None) -> list[tuple[str, str | None]]:
    """Parse ``--harness`` into ``[(name, model), ...]``.

    Each comma-separated entry is ``name`` or ``name:model``. A per-harness ``model`` wins; otherwise
    the entry inherits the global ``--model`` (``default_model``), which may itself be ``None`` (let
    the CLI decide). Harness names never contain ``:``, so splitting on the first colon is safe.
    """
    specs: list[tuple[str, str | None]] = []
    for entry in harness.split(","):
        entry = entry.strip()
        if not entry:
            continue
        name, sep, hmodel = entry.partition(":")
        hmodel = hmodel.strip()
        specs.append((name.strip(), hmodel if (sep and hmodel) else default_model))
    return specs


def _fmt_cost(cost: float | None) -> str:
    return f"${cost:.4f}" if cost is not None else "n/a"


def _load_env_file(path: str) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file (ignoring blanks and # comments)."""
    from pathlib import Path

    env: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip("'\"")
    return env


def main() -> None:
    """Console-script entry point (see pyproject `[project.scripts]`)."""
    app()


if __name__ == "__main__":
    main()
