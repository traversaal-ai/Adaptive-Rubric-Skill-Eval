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
        None,
        "--harness",
        help="Harness(es) to test, comma-separated: claude-code | gemini-cli | codex. "
        "Multiple runs the same task on each. Pin a model per "
        "harness with 'name:model', e.g. 'claude-code:claude-opus-4-8,codex:gpt-5-codex' "
        "(falls back to --model, else the CLI's own default). Optional when the task's "
        "adarubric.yaml sets defaults.agent — this flag overrides it.",
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
        None, "--trials",
        help="Repetitions inside this attempt (agents are non-deterministic). "
        "Default: the task's defaults.trials, else 1. This flag overrides both.",
    ),
    timeout: int = typer.Option(
        None, "--timeout",
        help="Per-harness timeout in seconds (default: config file value, else 300).",
    ),
    grade: bool = typer.Option(
        True, "--grade/--no-grade",
        help="Run graders after the agent (skillbench verifier / config graders / llm rubric).",
    ),
    llm_rubric: str = typer.Option(
        None, "--llm-rubric",
        help="Also have an LLM judge score the run against a rubric: yes (default) | no. Uses the "
        "task's own llm_rubric grader when it defines one, else a built-in general rubric at weight "
        "0.3. Needs a judge API key (GEMINI_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY, gemini "
        "picked first) — with no key the default judge is skipped quietly. Costs a small LLM call "
        "per trial. Accepts yes/no, true/false, 1/0, on/off.",
    ),
    fixed_rubric: str = typer.Option(
        None, "--fixed-rubric",
        help="Also score with the FIXED rubric - the SAME rubric text for every task "
        "(rubrics/fixed.md, ours written there if you have none): yes | no. The baseline next to "
        "the generated static and adaptive rubrics; weight 0 in the reward. Default: the yaml's "
        "grading.fixed_rubric, else yes.",
    ),
    adaptive_rubric: str = typer.Option(
        None, "--adaptive-rubric",
        help="Also score with the ADAPTIVE rubric: yes (default) | no. Four task-specific tests "
        "generated from the instruction + SKILL.md (cached in rubrics/<task>/), judged blind, "
        "one small LLM call per test. Recorded and shown next to the other scores but weight 0 in "
        "the reward. Same key rule as --llm-rubric. Accepts yes/no, true/false, 1/0, on/off.",
    ),
    adaptive_provider: str = typer.Option(
        None, "--adaptive-provider",
        help="Judge/generator for the adaptive rubric: gemini | anthropic | openai. "
        "Default: first key found (gemini first).",
    ),
    adaptive_model: str = typer.Option(
        None, "--adaptive-model",
        help="Model for the adaptive rubric's generator and judge (default: the provider's default).",
    ),
    inject_skills: str = typer.Option(
        None, "--inject-skills",
        help="Give the agent the task's skills: yes | no. 'no' runs the SAME task with the "
        "guidance withheld — the control half of 'did the skill actually help?'. Default: the "
        "yaml's inject_skills (else yes); this flag overrides for one run. Either way the run "
        "records which skills existed. Accepts yes/no, true/false, 1/0, on/off.",
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
    acp_install: str = typer.Option(
        None, "--acp-install",
        help="For --harness acp with --sandbox docker: how to install the wrapped agent's CLI into the "
        "image. Either the NAME of a built-in harness to reuse its installer (e.g. 'gemini-cli' — the "
        "easy path, it already handles installing Node) or a raw shell snippet. Omit if the command "
        "already exists in the task image.",
    ),
    acp_name: str = typer.Option(
        None, "--acp-name",
        help="For --harness acp: the label this run is filed under (output/<label>/…). Defaults to the "
        "wrapped agent's name, e.g. 'acp-gemini', so ACP runs of different agents don't pile into one "
        "folder and become impossible to compare.",
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

    from adarubric.core.errors import SandboxUnavailable
    from adarubric.harnesses.registry import create_harness
    from adarubric.loading import load_spec
    from adarubric.reporting.status import FanReporter, StatusReporter
    from adarubric.reporting.terminal import TerminalReporter
    from adarubric.runner import EvalRunner
    from adarubric.sandboxes.registry import create_sandbox

    file_env = _load_env_file(env_file) if env_file else {}

    spec = load_spec(path, instruction, task=task)

    # Precedence, everywhere: command line > the task's defaults > built-in defaults.
    harness = harness or spec.default_harness
    if not harness:
        typer.secho(
            "No harness: pass --harness (claude-code | gemini-cli | codex | acp) or set "
            "defaults.agent in the task's adarubric.yaml.", fg="red",
        )
        raise typer.Exit(code=1)
    trials = trials if trials is not None else (spec.default_trials or 1)

    # Each entry is "name" or "name:model"; a per-harness model overrides the global --model.
    harness_specs = _parse_harness_specs(harness, model)

    if inject_skills is not None:  # flag > yaml's inject_skills > default yes
        spec.inject_skills = _parse_bool_flag(inject_skills, "--inject-skills")
    # Judge switches: CLI flag (when passed) > the yaml's grading: block > default on.
    if llm_rubric is not None:
        spec.run_llm_rubric = _parse_bool_flag(llm_rubric, "--llm-rubric")
    if adaptive_rubric is not None:
        spec.run_adaptive_rubric = _parse_bool_flag(adaptive_rubric, "--adaptive-rubric")
    if fixed_rubric is not None:
        spec.run_fixed_rubric = _parse_bool_flag(fixed_rubric, "--fixed-rubric")
    spec.adaptive_provider = adaptive_provider
    spec.adaptive_model = adaptive_model
    if not spec.inject_skills:
        withheld = ", ".join(os.path.basename(p) for p in spec.skill_paths) or "none"
        typer.secho(
            f"--inject-skills no: withholding {withheld}. This is the CONTROL run — compare its "
            f"reward against a normal run to see what the skill is worth.", fg="yellow",
        )

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

    # Preflight the execution environment BEFORE creating any output. A stopped Docker daemon is a
    # problem on our side, not an agent failure — it must not produce a trial folder, a status.json
    # or a `success: false` run.json that then shows up in the dashboard as a lost run.
    try:
        create_sandbox(sandbox).preflight()
    except SandboxUnavailable as exc:
        typer.secho(str(exc), fg="red")
        typer.echo("Nothing was written - no run recorded.")
        raise typer.Exit(code=1) from None

    if timeout is not None:  # explicit flag wins over config-file value
        spec.timeout_sec = timeout
    graders = "skillbench-verifier" if spec.mode == "skillbench" else f"{len(spec.graders)} grader(s)"
    typer.echo(f"mode={spec.mode}  sandbox={sandbox}  task={spec.name}  grade={grade} ({graders})")

    # Judge keys (for the LLM rubric) come from --env-file / the environment. They are handed to the
    # judge only — never injected into the sandbox, so the agent and check scripts can't read them.
    from adarubric.grading.static_rubric import pick_provider
    judge_keys = ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "TOGETHER_API_KEY",
                  # The judge OWN settings - select the judging LLM independently of the
                  # agent: which provider, its key, and optionally its exact model.
                  "JUDGE_LLM_PROVIDER", "JUDGE_API_KEY", "JUDGE_MODEL")
    judge_env = {k: v for k in judge_keys if (v := file_env.get(k) or os.environ.get(k))}
    if spec.run_llm_rubric:
        judge = pick_provider(None, judge_env)
        if any(g.type == "llm_rubric" for g in spec.graders):
            rubric_src = "the task's own grader"
        elif spec.static_rubric_text:
            rubric_src = "the file named in grading.static_rubric"
        else:
            rubric_src = "generated per task (rubrics/<task>/static.md, fallback: built-in)"
        if judge:
            typer.echo(f"llm rubric: on  judge={judge}  rubric={rubric_src}")
        else:
            typer.secho(
                "llm rubric: skipped - no judge API key found (GEMINI_API_KEY / ANTHROPIC_API_KEY / "
                "OPENAI_API_KEY). Deterministic grading still runs.", fg="yellow",
            )
    else:
        typer.echo("llm rubric: off (--llm-rubric no)")
    if spec.run_fixed_rubric and pick_provider(None, judge_env):
        typer.echo("fixed rubric: on  (same text for every task - rubrics/fixed.md, weight 0)")
    if spec.run_adaptive_rubric:
        a_judge = pick_provider(adaptive_provider, judge_env)
        if a_judge:
            typer.echo(
                f"adaptive rubric: on  judge={a_judge}  4 task-specific tests, blind, weight 0 "
                f"(shown, not blended)")
        else:
            typer.secho("adaptive rubric: skipped - no judge API key.", fg="yellow")
    else:
        typer.echo("adaptive rubric: off (--adaptive-rubric no)")

    # Live status: written to <output>/status.json as the run happens (stages + activity feed), so a
    # watching dashboard can render it live. Console + status file share one fan-out reporter.
    import os as _os
    status_path = _os.path.join(output, "status.json")
    status = StatusReporter(status_path)
    reporter = FanReporter(TerminalReporter(), status)
    typer.echo(
        f"live status -> {status_path}  (live dashboard: python dashboard/serve.py --output {output})"
    )

    all_rewards: list[float] = []
    for hname, hmodel in harness_specs:
        h = create_harness(hname, model=hmodel)
        # Configure the generic ACP wrapper from its dedicated flags.
        if hname == "acp":
            if not acp_cmd:
                typer.secho("--harness acp requires --acp-cmd (e.g. --acp-cmd 'gemini --acp').", fg="red")
                raise typer.Exit(code=1)
            h.command = acp_cmd
            if acp_env_key:
                h.env_keys = tuple(k.strip() for k in acp_env_key.split(",") if k.strip())
            if acp_skill_dir:
                h.skill_dirs = tuple(d.strip() for d in acp_skill_dir.split(",") if d.strip())
            # Every ACP run would otherwise be filed under plain "acp/", so gemini-over-ACP and
            # codex-over-ACP would land in the same folder and overwrite each other's attempts. Label
            # it with the wrapped agent instead.
            h.name = acp_name or _acp_label(acp_cmd)
            if acp_install:
                # A harness name reuses that harness's installer — which already knows it must install
                # Node before npm exists. Anything else is taken as a shell snippet.
                try:
                    h.docker_install = create_harness(acp_install).docker_install
                    typer.echo(f"  --acp-install {acp_install}: reusing that harness's installer")
                except ValueError:
                    h.docker_install = acp_install
            elif sandbox == "docker":
                typer.secho(
                    "--harness acp --sandbox docker: no --acp-install given, so the agent command must "
                    "already exist in the task image. Pass --acp-install <harness-name> (e.g. "
                    "gemini-cli) or a shell snippet.", fg="yellow",
                )
        typer.echo(
            f"harness={h.name}  model={hmodel or 'not pinned - the CLI picks; the name it reports is recorded'}"
        )
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
        if hname == "acp":
            # Hand the harness the sandbox's interactive launcher: a local process, or `docker exec -i`
            # into the container. Same ACP conversation either way — this is what unlocks docker.
            h.spawn = sb.popen
        runner = EvalRunner(sb, output_root=output, reporter=reporter, grade=grade,
                            judge_env=judge_env)
        # Inject ONLY this harness's declared key(s) from the file into the sandbox.
        harness_env = {k: file_env[k] for k in h.env_keys if k in file_env}
        if hname == "acp":
            # ACP is spawned directly (not via run_command), so hand it the injected keys explicitly.
            h.launch_env = harness_env

        try:
            report = runner.run(h, spec, trials=trials, env=harness_env)
        except SandboxUnavailable as exc:  # daemon died after preflight — abort, don't score it
            status.close()
            typer.secho(str(exc), fg="red")
            raise typer.Exit(code=1) from None
        for tr in report.trials:
            m = tr.meta
            u = m.usage
            cost = u.cost_usd if u.cost_usd is not None else u.estimated_cost_usd
            # "grading failed" is a third outcome, distinct from a low score: the check script never
            # reached a verdict, so there IS no reward to report for this trial.
            if tr.graded:
                reward = f"reward={tr.reward:.2f}"
                all_rewards.append(tr.reward)
            elif m.grading_error:
                reward = "GRADING FAILED (not the agent's fault)"
            else:
                reward = "ungraded"
            turns = u.num_turns if u.num_turns is not None else "?"
            typer.echo(
                f"    -> {tr.output_dir}\n"
                f"       success={m.success} {reward} turns={turns} tokens={u.total_tokens} "
                f"cost={_fmt_cost(cost)} ({u.cost_source}) "
                f"time={m.timing.total_ms / 1000:.1f}s "
                f"changes=+{m.files_created}/~{m.files_modified}/-{m.files_deleted}"
            )
            if m.grading_error:
                typer.secho(f"       grading error: {m.grading_error}", fg="yellow")
    status.close()
    # A clean sweep of zeros is the signature of a broken task, not a weak model — that is exactly
    # how the mangled-line-endings bug hid. Point at the free check instead of letting the user guess.
    if all_rewards and all(r < 0.001 for r in all_rewards) and spec.oracle_path:
        typer.secho(
            f"\nEvery trial scored 0. That can mean the TASK is broken rather than the agent.\n"
            f"Verify it for free (no model, no key):  uv run adarubric check {path}",
            fg="yellow",
        )
    typer.echo("done.")


@app.command()
def check(
    path: str = typer.Argument(..., help="Path to a SkillsBench task package."),
    sandbox: str = typer.Option("docker", "--sandbox", help="Where to run: docker (default) | local."),
    output: str = typer.Option(
        "output", "--output", help="Output root; the check lands in <output>/oracle/<task>/."
    ),
    timeout: int = typer.Option(None, "--timeout", help="Seconds to allow the solution to run."),
) -> None:
    """Prove a task is passable BEFORE spending money on agents.

    Runs the task's own ``oracle/solve.sh`` — the reference solution its author wrote — and grades it
    with the task's real grader. A healthy task scores 1.00. Anything less means the *task* is broken
    (bad grader, missing dependency, mangled line endings) and every agent score you collect from it
    would be meaningless.

    Costs nothing: no model, no API key, no tokens. Run this first on any task that is new to you, or
    whenever every agent mysteriously scores zero.
    """
    import os

    from adarubric.core.errors import SandboxUnavailable
    from adarubric.harnesses.registry import create_harness
    from adarubric.loading import load_spec
    from adarubric.reporting.terminal import TerminalReporter
    from adarubric.runner import EvalRunner
    from adarubric.sandboxes.registry import create_sandbox

    spec = load_spec(path, None)
    if not spec.oracle_path:
        typer.secho(
            f"No oracle/solve.sh in {path} — there is no reference solution to check.\n"
            "Only SkillsBench-style tasks ship one.", fg="red",
        )
        raise typer.Exit(code=1)
    if timeout is not None:
        spec.timeout_sec = timeout

    try:
        create_sandbox(sandbox).preflight()
    except SandboxUnavailable as exc:
        typer.secho(str(exc), fg="red")
        raise typer.Exit(code=1) from None

    typer.echo(f"checking task={spec.name}  sandbox={sandbox}  (reference solution, no model, free)")
    harness = create_harness("oracle")
    runner = EvalRunner(create_sandbox(sandbox), output_root=output,
                        reporter=TerminalReporter(), grade=True)
    report = runner.run(harness, spec, trials=1, env={})
    trial = report.trials[0]
    meta = trial.meta

    typer.echo(f"    -> {trial.output_dir}")
    if meta.error:
        typer.secho(f"\nBROKEN: the reference solution itself failed to run.\n{meta.error}", fg="red")
        raise typer.Exit(code=1)
    if meta.grading_error:
        typer.secho(f"\nBROKEN: grading never produced a result.\n{meta.grading_error}", fg="red")
        raise typer.Exit(code=1)
    if not meta.graded:
        typer.secho("\nBROKEN: nothing scored this run.", fg="red")
        raise typer.Exit(code=1)
    if trial.reward >= 0.999:
        typer.secho(f"\nOK: the reference solution scores {trial.reward:.2f}. "
                    "The task and its grader work — agent scores from it are trustworthy.", fg="green")
        return
    typer.secho(
        f"\nBROKEN: the reference solution scores only {trial.reward:.2f}, expected 1.00.\n"
        "The task cannot be passed even with the correct answer, so any agent score is meaningless.\n"
        f"Look at {os.path.join(trial.output_dir, 'grading.json')} for what the grader said.",
        fg="red",
    )
    raise typer.Exit(code=1)


@app.command()
def init(
    path: str = typer.Argument(".", help="Your skill folder, OR a SkillsBench task folder."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing generated files."),
    static_rubric: str = typer.Option(
        "yes", "--static-rubric",
        help="Generate the static judge rubric: yes (default) | no. 'no' writes the switch off in "
        "the yaml and spends nothing on it. Flip to yes later — the next run generates it."),
    adaptive_rubric: str = typer.Option(
        "yes", "--adaptive-rubric",
        help="Generate the 4 adaptive tests: yes (default) | no. Same rules as --static-rubric."),
) -> None:
    """Write the task config (adarubric.yaml) — for your own skill OR a SkillsBench task.

    Your skill folder: an LLM reads SKILL.md and drafts the whole config (instruction, workspace,
    a script grader, the llm rubric). Without a key you get a commented template.

    A SkillsBench task: the dataset folder is NEVER written to. Instead you get a thin wrapper —
    tasks/<name>/adarubric.yaml whose `source:` points at the dataset (verifier, Dockerfile, data
    stay there) — plus the generated rubrics in rubrics/<name>/ for review. Edit, then
    `adarubric run tasks/<name>`.
    """
    import os
    from pathlib import Path

    from adarubric.loading import load_spec
    from adarubric.scaffold import (
        detect_skills_with_content,
        extract_instruction_hint,
        generate_with_llm,
        pick_init_llm,
        render_template,
    )

    want_static = _parse_bool_flag(static_rubric, "--static-rubric")
    want_adaptive = _parse_bool_flag(adaptive_rubric, "--adaptive-rubric")

    d = Path(path).expanduser().resolve()
    if not d.is_dir():
        typer.secho(f"Not a folder: {d}", fg="red")
        raise typer.Exit(code=1)

    # ---- SkillsBench branch: wrapper in tasks/ + rubrics in rubrics/, dataset untouched. ----
    if (d / "task.md").is_file() and (d / "environment").is_dir():
        _init_skillbench(d, force=force, want_static=want_static, want_adaptive=want_adaptive)
        return

    out_path = d / "adarubric.yaml"
    if out_path.exists() and not force:
        typer.secho("adarubric.yaml already exists. Use --force to overwrite.", fg="red")
        raise typer.Exit(code=1)

    typer.echo("\nadarubric init\n")
    skills = detect_skills_with_content(d)
    if not skills:
        typer.echo("  No SKILL.md found. Creating a generic template.")
        typer.echo("     Place a SKILL.md in this folder for better scaffolding.\n")
        out_path.write_text(
            render_template("my-skill", "Describe what the agent should do with this skill."),
            encoding="utf-8")
        typer.echo(f"  Created {out_path.name}. Edit it, then run: adarubric run {path}")
        return

    typer.echo(f"  Found {len(skills)} skill(s): {', '.join(n for n, _ in skills)}\n")

    # <dir>/.env fills in keys the shell doesn't already have — skillgrade's exact precedence.
    env = dict(os.environ)
    env_file = d / ".env"
    if env_file.is_file():
        for k, v in _load_env_file(str(env_file)).items():
            env.setdefault(k, v)

    provider = pick_init_llm(env)
    if provider:
        typer.echo(f"  generating the config skeleton with {provider}…")
        try:
            content = generate_with_llm(skills, provider, env)
            out_path.write_text(content, encoding="utf-8")
            typer.secho(f"  created {out_path.name}", fg="green")
            # Same treatment as skillbench init: rubrics generated per switches into rubrics/,
            # referenced by path in the yaml — one behaviour, both task kinds.
            try:
                spec = load_spec(str(out_path))
                static_val, adaptive_val, fixed_val = _generate_rubrics(
                    spec, env, d, want_static, want_adaptive)
                out_path.write_text(
                    content + _grading_block(static_val, adaptive_val, fixed_val),
                    encoding="utf-8")
                load_spec(str(out_path))
            except Exception as exc:  # noqa: BLE001
                typer.secho(
                    f"  warning: the generated file doesn't load cleanly ({exc}). "
                    "Fix it by hand before running.", fg="yellow")
            typer.echo(f"     Review and edit the file, then run: adarubric run {path}\n")
            return
        except Exception as exc:  # noqa: BLE001
            typer.secho(f"  AI generation failed: {exc}", fg="red")
            typer.echo("     Falling back to template.\n")
    else:
        typer.echo(
            "  Set GEMINI_API_KEY, ANTHROPIC_API_KEY, or OPENAI_API_KEY for AI-powered "
            "generation.\n")

    name, md = skills[0]
    out_path.write_text(
        render_template(f"test-{name}", extract_instruction_hint(md)), encoding="utf-8")
    typer.echo(f"  Created {out_path.name}. Edit it to define your eval tasks, then run: "
               f"adarubric run {path}\n")


def _generate_rubrics(spec, env: dict, ref_dir, want_static: bool, want_adaptive: bool):
    """Generate the switched-on rubrics into rubrics/<task>/ and return the two switch values
    to write in the yaml: a RELATIVE PATH when generated (so the yaml shows where it lives),
    'yes' when generation must wait for the first run (no key), 'no' when switched off.
    A 'no' spends nothing — the generator is never called for it."""
    import os
    from pathlib import Path

    from adarubric.grading.adaptive_rubric import generated_adaptive_rubric
    from adarubric.grading.static_rubric.generate import (
        _slug,
        ensure_fixed_rubric,
        generated_task_rubric,
    )

    rubrics_dir = Path("rubrics") / _slug(spec.name)

    def rel(p) -> str:
        return os.path.relpath(p, ref_dir).replace("\\", "/")

    # The fixed rubric always exists after init, so the yaml can hint straight at it.
    ensure_fixed_rubric("rubrics")

    static_val, adaptive_val = "no", "no"
    if want_static:
        text = generated_task_rubric(spec, env, "rubrics")
        static_val = rel(rubrics_dir / "static.md") if text else "yes"
        typer.echo(f"  static rubric  -> rubrics/{_slug(spec.name)}/static.md" if text
                   else "  static rubric: couldn't generate now (no key?) - first run will.")
    if want_adaptive:
        criteria = generated_adaptive_rubric(spec, env, "rubrics")
        adaptive_val = rel(rubrics_dir / "adaptive.json") if criteria is not None else "yes"
        typer.echo(f"  adaptive tests -> rubrics/{_slug(spec.name)}/adaptive.json"
                   if criteria is not None
                   else "  adaptive tests: couldn't generate now (no key?) - first run will.")
    fixed_val = rel(Path("rubrics") / "fixed.md")
    return static_val, adaptive_val, fixed_val


def _grading_block(static_val: str, adaptive_val: str, fixed_val: str = "yes") -> str:
    return (
        "\n# Run the control condition (skill withheld) with `inject_skills: no`;"
        "\n# --inject-skills overrides for one run."
        "\n# inject_skills: no"
        "\n"
        "\n# Which LLM judges run (yes | no | a rubric file path). This yaml is the source of"
        "\n# truth; flags (--fixed-rubric / --llm-rubric / --adaptive-rubric) override for one run."
        "\ngrading:"
        f"\n  fixed_rubric: {fixed_val}"
        f"\n  static_rubric: {static_val}"
        f"\n  adaptive_rubric: {adaptive_val}\n")


def _init_skillbench(task_dir, force: bool, want_static: bool, want_adaptive: bool) -> None:
    """Manual mode for a benchmark task: wrapper yaml + reviewable rubrics, dataset untouched."""
    import os
    from pathlib import Path

    from adarubric.loading import load_spec

    name = task_dir.name
    wrapper_dir = Path("tasks") / name
    wrapper = wrapper_dir / "adarubric.yaml"
    if wrapper.exists() and not force:
        typer.secho(f"{wrapper} already exists. Use --force to overwrite.", fg="red")
        raise typer.Exit(code=1)

    typer.echo(f"\nadarubric init (SkillsBench task: {name})\n")
    spec = load_spec(str(task_dir))

    # Judge keys: .env in the CURRENT folder, then the shell (same rule as everywhere).
    env = dict(os.environ)
    if Path(".env").is_file():
        for k, v in _load_env_file(".env").items():
            env.setdefault(k, v)

    # Generate ONLY what the switches ask for — a 'no' spends nothing.
    static_val, adaptive_val, fixed_val = _generate_rubrics(
        spec, env, wrapper_dir, want_static, want_adaptive)

    source_rel = os.path.relpath(task_dir, wrapper_dir).replace("\\", "/")
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    wrapper.write_text(
        f"# SkillsBench wrapper - generated by `adarubric init`. The benchmark task itself\n"
        f"# (instruction, data, Dockerfile, verifier, skills) lives at `source:` and is never\n"
        f"# copied or changed. This file holds only YOUR knobs: which judges run (yes | no |\n"
        f"# a rubric file path), agent, trials, timeout. Flags still win for a single run.\n"
        f"source: {source_rel}\n"
        f"\n"
        f"defaults:\n"
        f"  # agent: gemini-cli        # uncomment to set a default agent for this task\n"
        f"  trials: 1\n"
        f"timeout: {spec.timeout_sec}\n"
        + _grading_block(static_val, adaptive_val, fixed_val),
        encoding="utf-8")
    typer.secho(f"  created {wrapper}", fg="green")
    try:
        load_spec(str(wrapper_dir))
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"  warning: wrapper doesn't load cleanly ({exc}).", fg="yellow")
    typer.echo(
        f"\n  Review/edit the rubrics, then run:\n"
        f"    uv run adarubric run tasks/{name} --harness <agent> --sandbox docker --env-file .env\n")


#: batch.yaml keys that map 1:1 onto `adarubric run` flags (underscores become dashes).
_BATCH_FLAG_KEYS = (
    "harness", "sandbox", "model", "dataset", "instruction", "task", "output", "trials",
    "timeout", "inject_skills", "llm_rubric", "adaptive_rubric", "adaptive_provider",
    "adaptive_model", "env_file",
)


def batch_commands(cfg: dict) -> list[tuple[str, list[str]]]:
    """(task path, run-args) per task from a batch file. Pure — unit-tested without subprocesses.

    Each task entry: ``path`` (required) + any run flag as a key (task overrides ``defaults``) +
    an optional raw ``flags: [...]`` list appended verbatim for anything exotic (ACP flags etc.).
    """
    defaults = cfg.get("defaults") or {}
    jobs: list[tuple[str, list[str]]] = []
    for i, t in enumerate(cfg.get("tasks") or [], start=1):
        if not isinstance(t, dict) or not t.get("path"):
            raise ValueError(f"batch task #{i} needs a `path:`")
        merged = {**defaults, **t}
        args = ["run", str(merged["path"])]
        for key in _BATCH_FLAG_KEYS:
            value = merged.get(key)
            if value is not None:
                # yaml reads bare yes/no as booleans — hand the flag the words it expects.
                text = ("yes" if value else "no") if isinstance(value, bool) else str(value)
                args += [f"--{key.replace('_', '-')}", text]
        args += [str(x) for x in (merged.get("flags") or [])]
        jobs.append((str(merged["path"]), args))
    return jobs


@app.command()
def batch(
    file: str = typer.Argument(..., help="A batch yaml: defaults + tasks[] (see batch.example.yaml)."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the commands that WOULD run, run nothing, spend nothing."),
) -> None:
    """Run many tasks one by one from a single yaml file.

    Define your tasks once (each with its own harness/sandbox/trials/... if needed), then run them
    all with this one command. A failing task doesn't stop the rest; you get a summary at the end,
    and the exit code is non-zero if anything failed. All results land in the normal output/ tree,
    so the dashboard shows the whole batch.
    """
    import subprocess
    import sys
    from pathlib import Path

    p = Path(file).expanduser()
    if not p.is_file():
        typer.secho(f"Batch file not found: {p}", fg="red")
        raise typer.Exit(code=1)
    import yaml as _yaml
    try:
        jobs = batch_commands(_yaml.safe_load(p.read_text(encoding="utf-8")) or {})
    except ValueError as exc:
        typer.secho(str(exc), fg="red")
        raise typer.Exit(code=1) from None
    if not jobs:
        typer.secho("The batch file lists no tasks.", fg="red")
        raise typer.Exit(code=1)

    typer.echo(f"\nadarubric batch — {len(jobs)} task(s) from {p.name}\n")
    results: list[tuple[str, int]] = []
    for i, (path, args) in enumerate(jobs, start=1):
        cmd = [sys.executable, "-m", "adarubric", *args]
        typer.secho(f"[{i}/{len(jobs)}] {' '.join(args)}", bold=True)
        if dry_run:
            results.append((path, 0))
            continue
        # One subprocess per task — a crash, timeout, or missing key in one task can never take
        # down the rest of the batch. Output streams straight to your terminal, live.
        code = subprocess.run(cmd).returncode  # noqa: S603 - our own CLI with composed args
        results.append((path, code))
        if code != 0:
            typer.secho(f"    task failed (exit {code}) - continuing with the rest", fg="yellow")

    typer.echo("\nbatch summary:")
    failed = 0
    for path, code in results:
        ok = code == 0
        failed += 0 if ok else 1
        typer.secho(f"  {'OK    ' if ok else 'FAILED'} {path}", fg=("green" if ok else "red"))
    if dry_run:
        typer.echo("  (dry run - nothing was executed)")
    if failed:
        typer.secho(f"\n{failed} of {len(results)} task(s) failed.", fg="red")
        raise typer.Exit(code=1)
    typer.echo("\nall done.")


@app.command()
def recompute(
    output: str = typer.Option("output", "--output", help="Output root to update."),
    apply: bool = typer.Option(
        False, "--apply", help="Actually write the changes. Without this it's a preview."
    ),
) -> None:
    """Re-derive metrics for past runs from the logs they already saved.

    Every run keeps its agent's full output in ``raw.log``, so anything computed *from* that output
    can be recomputed later. When the parsing improves — a turn count that was measuring the wrong
    thing, tool names that were really call ids, a price table that gained an entry — old runs can be
    brought up to date without re-running (and re-paying for) the agent.

    Only re-derived facts are touched. The reward, the timings, whether it succeeded, the file
    changes: none of that comes from ``raw.log``, so none of it is rewritten. Updates happen in place
    — no backup copies, because ``raw.log`` is the evidence and this can always be run again.

    Previews by default; pass ``--apply`` to write.
    """
    import json
    from pathlib import Path

    import yaml

    from adarubric.core.pricing import estimate_cost, is_specific_model
    from adarubric.core.skill_depth import classify as classify_skill_depth
    from adarubric.harnesses.acp import replay_wire_log
    from adarubric.harnesses.claude import parse_stream_json
    from adarubric.harnesses.codex import parse_codex_jsonl
    from adarubric.harnesses.gemini import parse_gemini_output

    def reparse(harness: str, raw: str):
        """Run the current parser for whichever agent produced this log."""
        if harness.startswith("acp"):
            return replay_wire_log(raw)
        if harness == "claude-code":
            return parse_stream_json(raw, "", 0)
        if harness == "codex":
            return parse_codex_jsonl(raw)
        if harness == "gemini-cli":
            return parse_gemini_output(raw)
        return None

    def skill_paths_for(trial_dir: Path, meta: dict) -> list[str]:
        """Where this task's skills live on disk — needed to judge depth fairly.

        A skill that is only a SKILL.md has no depth to reach, so knowing what the skill *contains*
        is what stops a single-file skill being marked shallow.
        """
        ev = trial_dir.parent / "eval.yaml"
        if not ev.is_file():
            return []
        try:
            manifest = yaml.safe_load(ev.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            return []
        dockerfile = ((manifest.get("environment") or {}).get("dockerfile")) or ""
        if not dockerfile:
            return []
        skills_root = Path(dockerfile).parent / "skills"
        return [str(skills_root / n) for n in (manifest.get("skills") or [])]

    root = Path(output)
    rows: list[tuple[str, str, str, str]] = []
    changed = 0

    for run_json in sorted(root.rglob("run.json")):
        trial_dir = run_json.parent
        raw_log = trial_dir / "raw.log"
        if not raw_log.is_file():
            continue
        try:
            meta = json.loads(run_json.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        raw = raw_log.read_text(encoding="utf-8", errors="replace")
        if not raw.strip():
            continue
        out = reparse(meta.get("harness", ""), raw)
        if out is None:
            continue

        usage = meta.setdefault("usage", {})
        skill = meta.setdefault("skill_usage", {})
        before = (usage.get("num_turns"), usage.get("cost_usd") or usage.get("estimated_cost_usd"),
                  skill.get("skill_depth"), tuple(sorted((usage.get("tool_counts") or {}))))

        # "New wins unless the new value is None" — a parser that can't see something must never
        # erase what an earlier one did see.
        def put(target: dict, key: str, value) -> None:
            if value is not None:
                target[key] = value

        put(meta, "model", out.model)
        put(usage, "num_turns", out.num_turns)
        put(usage, "num_turns_reported", out.num_turns_reported)
        put(usage, "input_tokens", out.input_tokens)
        put(usage, "output_tokens", out.output_tokens)
        put(usage, "cached_input_tokens", out.cached_input_tokens)
        total = out.total_tokens
        if total is None and out.input_tokens is not None and out.output_tokens is not None:
            total = out.input_tokens + out.output_tokens
        put(usage, "total_tokens", total)
        if out.tool_counts:
            usage["tool_counts"] = dict(out.tool_counts)
            usage["tools_used"] = sorted(out.tool_counts)
            usage["num_tool_calls"] = sum(out.tool_counts.values())
        put(usage, "cost_usd", out.cost_usd)

        # Re-price with today's table, and against the pin when the agent named no usable model.
        reported = out.model if is_specific_model(out.model) else None
        priced = reported or meta.get("model_requested")
        est = estimate_cost(priced, usage.get("input_tokens"), usage.get("output_tokens"))
        usage["estimated_cost_usd"] = est
        usage["cost_source"] = ("reported" if usage.get("cost_usd") is not None
                                else ("estimated" if est is not None else None))

        if out.skill_opened is not None:
            skill["skill_opened"] = out.skill_opened
        if out.skills_triggered:
            skill["skills_triggered"] = [
                {"name": s.name, "source": getattr(s.source, "value", str(s.source)),
                 "timestamp": s.timestamp, "details": s.details}
                for s in out.skills_triggered
            ]
            skill["skill_files_read"] = list(out.skill_files_read)
            skill["num_skill_files_read"] = len(out.skill_files_read)
        depth = classify_skill_depth(skill.get("skill_opened"), out.skills_triggered,
                                     skill_paths_for(trial_dir, meta))
        put(skill, "skill_depth", depth)

        after = (usage.get("num_turns"), usage.get("cost_usd") or usage.get("estimated_cost_usd"),
                 skill.get("skill_depth"), tuple(sorted((usage.get("tool_counts") or {}))))
        if before == after:
            continue
        changed += 1
        key = str(trial_dir.relative_to(root)).replace("\\", "/")
        rows.append((key,
                     f"{before[0]} -> {after[0]}",
                     f"{_fmt_cost(before[1])} -> {_fmt_cost(after[1])}",
                     f"{before[2]} -> {after[2]}"))
        if apply:
            # Updated in place, with no backup: `run.json` is DERIVED data and `raw.log` is the
            # evidence it came from, so this can always be run again. A copy would only be clutter.
            run_json.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")

    if not rows:
        typer.echo("Nothing to update — every run already matches the current parsing.")
        return
    typer.echo(f"{'run':<52} {'turns':<16} {'cost':<22} skill")
    for key, turns, cost, depth in rows:
        typer.echo(f"{key:<52} {turns:<16} {cost:<22} {depth}")
    typer.echo("")
    if apply:
        typer.secho(f"Updated {changed} run(s) in place.", fg="green")
    else:
        typer.secho(f"{changed} run(s) would change. Re-run with --apply to write.", fg="yellow")


#: Command words that wrap the real agent rather than being it — skipped when labelling an ACP run.
_ACP_WRAPPERS = {"npx", "node", "npm", "bunx", "bun", "deno", "uv", "uvx", "run", "python", "python3",
                 "sh", "bash", "-y", "--yes", "exec", "pipx"}


def _acp_label(command: str) -> str:
    """Name an ACP run after the agent it wraps: 'acp-gemini', 'acp-claude-code-acp'.

    Filing every ACP run under a bare "acp" would put different agents in the same output folder,
    where their attempt numbers collide and no comparison is possible.
    """
    import re as _re

    tokens = [t for t in (command or "").replace("\t", " ").split(" ") if t]
    for token in tokens:
        if token.lower() in _ACP_WRAPPERS or token.startswith("-"):
            continue
        # "@zed-industries/claude-code-acp" -> "claude-code-acp"; "/usr/bin/gemini" -> "gemini"
        leaf = token.replace("\\", "/").rstrip("/").split("/")[-1]
        leaf = _re.sub(r"\.(js|mjs|cjs|py|exe)$", "", leaf, flags=_re.IGNORECASE)
        leaf = _re.sub(r"[^A-Za-z0-9._-]+", "-", leaf).strip("-.")
        if leaf:
            return f"acp-{leaf.lower()}"
    return "acp"


_TRUE = {"yes", "y", "true", "t", "1", "on"}
_FALSE = {"no", "n", "false", "f", "0", "off"}


def _parse_bool_flag(value: str, flag: str) -> bool:
    """Accept the several spellings people actually type for a yes/no flag."""
    v = (value or "").strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    typer.secho(
        f"{flag} expects yes/no (also true/false, 1/0, on/off) — got '{value}'.", fg="red"
    )
    raise typer.Exit(code=1)


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
    _make_console_unicode_safe()
    app()


def _make_console_unicode_safe() -> None:
    """Never let a console encoding kill a run.

    Legacy Windows consoles default to cp1252, which cannot encode the arrows and em dashes in our
    help text and progress lines — writing one raised ``UnicodeEncodeError`` and aborted the whole
    command mid-eval. Degrading unencodable characters to '?' is always better than losing the run.
    """
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):  # not a reconfigurable text stream (e.g. captured)
            pass


if __name__ == "__main__":
    main()
