"""Generic ACP (Agent Client Protocol) harness — run ANY ACP-speaking agent.

ACP (https://agentclientprotocol.com) is a JSON-RPC 2.0 protocol over stdio for driving coding
agents (Zed's agents, ``gemini --acp``, and others). This adapter is *generic*: point it at any
ACP launch command with ``--acp-cmd`` and it drives the standard flow —
``initialize`` → ``session/new`` → ``session/prompt`` — collecting the agent's text, tool calls, and
(from the tool-call stream) skill usage.

Runs under **both sandboxes**. ACP needs a long-lived stdio conversation, which the fire-and-return
``run_command`` seam can't provide, so the agent is started through ``Sandbox.popen``: locally that's
a process with ``cwd`` set, in Docker it's ``docker exec -i`` into the running container. The Docker
path is what makes SkillsBench tasks (docker-only) reachable over ACP.

Metrics it can and cannot report:

* **model** — from ``session/new``'s model info, when the agent sends it.
* **turns** — counted as blocks of agent output separated by tool calls. ACP has no turn counter, so
  this is derived: each time the agent starts talking again after using a tool, that is another
  model reply. Same definition the other harnesses use.
* **skill_opened** — definitive both ways. Skill evidence in the tool-call stream → True. Tool calls
  present but none touching a skill → False (the agent reports its actions, and none was a skill).
  No tool calls at all → None, because then we cannot tell silence from inaction.
* **tokens / cost** — not part of ACP. Read from the prompt result if an agent volunteers them,
  otherwise absent.

Dependency-free: a small JSON-RPC/stdio client, no ACP SDK. Protocol shapes follow the ACP v1 spec;
a real agent may differ in edge details — verify against your target agent.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import subprocess
import threading
from pathlib import Path
from typing import Callable, Protocol

from adarubric.core.contracts import Harness, RunCommand
from adarubric.core.models import RunOutput, SkillTrigger, TriggerSource
from adarubric.core.turns import ReplyCounter

_PROTOCOL_VERSION = 1
#: A path inside an agent's skill-discovery dir, capturing the skill's own folder name. The escaped
#: form `\/` also matches, since the haystack is JSON-encoded.
_SKILL_PATH_RE = re.compile(
    r"(?:\\{1,2}|/)\.(?:claude|agents|gemini|codex)(?:\\{1,2}|/)skills(?:\\{1,2}|/)([^\\/\s'\"]+)"
)
#: Any SKILL.md read, wherever it lives — the fallback SkillsBench uses for unknown ACP agents.
_SKILL_MD_RE = re.compile(r"SKILL\.md", re.IGNORECASE)
#: Claude's dedicated skill tool announces itself in the tool-call content.
_LAUNCHING_RE = re.compile(r"Launching skill:\s*([^\"\\\n]+)")


def _vendor_tool_name(update: dict) -> str | None:
    """The agent's own tool name from ``_meta``, when it puts one there.

    ACP's ``title`` is a human label ("Read File"); vendors often carry the real name alongside it —
    claude-code-acp uses ``_meta.claudeCode.toolName`` (``Bash``, ``Skill``, ``mcp__acp__Read``).
    Observed in a real transcript, so worth preferring; unknown shapes fall back to the title.
    """
    meta = update.get("_meta")
    if not isinstance(meta, dict):
        return None
    for value in meta.values():
        if isinstance(value, dict):
            name = value.get("toolName") or value.get("tool_name")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return None


def _pick_auth_method(methods: list, env_names: set[str]) -> str | None:
    """The id of the auth method to use: prefer one whose declared env vars we injected, else the
    first offered. ``None`` when the agent advertised nothing (then auth can't be satisfied)."""
    ids = [m.get("id") for m in methods if isinstance(m, dict) and m.get("id")]
    if not ids:
        return None
    for m in methods:
        if not isinstance(m, dict):
            continue
        declared = {v.get("name") for v in (m.get("vars") or []) if isinstance(v, dict)}
        if declared and declared <= env_names:
            return m.get("id")
    return ids[0]


def _session_model(session: dict) -> str | None:
    """Best-effort model name from an ACP ``session/new`` response.

    ACP's model-selection extension reports the session's model as ``models.currentModelId`` plus an
    ``availableModels`` list of ``{modelId, name}``. It is optional, and wrappers differ, so this
    stays defensive — an unknown shape yields ``None`` (honestly "unreported") rather than a guess.
    """
    models = session.get("models")
    if not isinstance(models, dict):
        return None
    current = models.get("currentModelId") or models.get("modelId")
    available = models.get("availableModels")
    if isinstance(available, list):
        for entry in available:
            if isinstance(entry, dict) and entry.get("modelId") == current:
                name = entry.get("name") or entry.get("modelId")
                return str(name) if name else None
    return str(current) if current else None


class AcpError(Exception):
    """A protocol- or transport-level ACP failure."""


class _Files(Protocol):
    """Serves the agent's ``fs/read_text_file`` / ``fs/write_text_file`` requests."""

    def read(self, path: str) -> str: ...
    def write(self, path: str, content: str) -> None: ...


class _HostFiles:
    """Local sandbox: the workspace is a real directory, so read and write it directly."""

    def __init__(self, cwd: str) -> None:
        self.cwd = cwd

    def _p(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else Path(self.cwd) / p

    def read(self, path: str) -> str:
        return self._p(path).read_text(encoding="utf-8", errors="replace")

    def write(self, path: str, content: str) -> None:
        p = self._p(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


class _SandboxFiles:
    """Docker sandbox: the paths live inside the container, so go through its shell.

    Content is moved base64-encoded rather than interpolated into the command, so newlines, quotes and
    non-ASCII in a file the agent writes cannot break out of the shell command or be mangled.
    """

    def __init__(self, run_command: RunCommand) -> None:
        self._run = run_command

    def read(self, path: str) -> str:
        res = self._run(f"base64 < {shlex.quote(path)}")
        if res.exit_code != 0:
            raise OSError((res.stderr or f"cannot read {path}").strip()[:300])
        return base64.b64decode("".join(res.stdout.split())).decode("utf-8", errors="replace")

    def write(self, path: str, content: str) -> None:
        blob = base64.b64encode(content.encode("utf-8")).decode("ascii")
        quoted = shlex.quote(path)
        res = self._run(
            f"mkdir -p \"$(dirname {quoted})\" && printf %s {shlex.quote(blob)} | base64 -d > {quoted}"
        )
        if res.exit_code != 0:
            raise OSError((res.stderr or f"cannot write {path}").strip()[:300])


class AcpHarness(Harness):
    name = "acp"
    cli = "acp"
    env_keys: tuple[str, ...] = ()  # set from --acp-env-key (unknown for a generic agent)
    # The wrapped agent's real skill-discovery dir(s). Default to the cross-agent `.agents/skills`
    # alias; override with --acp-skill-dir to match the specific agent (see coding_agent_harness.md §2).
    skill_dirs: tuple[str, ...] = (".agents/skills",)
    #: Shell snippet installing the wrapped agent's CLI into a docker image. Empty by default because
    #: a generic ACP agent could be anything; set it with --acp-install when using --sandbox docker,
    #: otherwise the command must already exist in the task image.
    docker_install = ""

    #: The launch command for the ACP agent, e.g. "gemini --acp". Set by the CLI from --acp-cmd.
    command: str = ""
    #: Environment for the spawned agent (injected declared keys). Set by the CLI.
    launch_env: dict[str, str] | None = None
    #: Starts the agent as a long-lived process in the workspace — ``Sandbox.popen``, supplied by the
    #: CLI. This is what makes the harness sandbox-agnostic: local process or ``docker exec -i``, the
    #: conversation is identical either way.
    spawn: "Callable[[str, str, dict[str, str] | None], subprocess.Popen[str]] | None" = None

    def run(self, instruction: str, workspace: str, run_command: RunCommand) -> RunOutput:
        if not self.command:
            return RunOutput(output="", error="ACP harness requires --acp-cmd (e.g. --acp-cmd 'gemini --acp').")
        command = self.command
        if self.model:
            # A pinned model is passed through as ACP has no model parameter on session/new; the
            # wrapped CLI's own flag is the only place it can go.
            command = ([*command, "--model", self.model] if isinstance(command, (list, tuple))
                       else f"{command} --model {self.model}")
        # The session cwd must be a real path AS THE AGENT SEES IT. Locally the workspace handle IS
        # that path, but under Docker it's a container ID — sending it made gemini resolve
        # `/root/<container-id>` and reject session/new with "Directory does not exist". Asking the
        # sandbox shell for `pwd` gets the true working directory in either case.
        local = os.path.isdir(workspace)
        session_cwd = workspace if local else _sandbox_cwd(run_command)
        proc = self._start(command, workspace)
        # Local workspaces are real host directories, so fs/ requests are served directly. A container
        # workspace is not on this filesystem, so those requests are served through the sandbox shell.
        files: _Files = _HostFiles(session_cwd) if local else _SandboxFiles(run_command)
        conn = AcpConnection(proc, session_cwd, files=files,
                             auth_env_names=set(self.launch_env or ()) | set(self.env_keys or ()))
        try:
            return conn.run_prompt(instruction)
        except Exception as exc:  # noqa: BLE001 - see below
            # ANY failure, not just a protocol one. A crash here previously escaped with an empty
            # raw_output, destroying the transcript at the exact moment it was needed to diagnose the
            # crash. Whatever went wrong, the conversation so far is kept, plus the agent's stderr.
            kind = "ACP error" if isinstance(exc, AcpError) else f"ACP client {type(exc).__name__}"
            transcript = conn.wire_log()
            tail = conn.stderr_tail()
            return RunOutput(
                output="\n".join(conn.text_so_far()).strip(),
                raw_output=(transcript + ("\n\nagent stderr:\n" + tail if tail else "")) or tail,
                error=f"{kind}: {exc}",
            )
        finally:
            conn.close()

    def _start(self, command: str, workspace: str) -> "subprocess.Popen[str]":
        if self.spawn is not None:
            return self.spawn(workspace, command, self.launch_env)
        # Fallback for direct/library use without a sandbox: run it here, in the workspace.
        # Accept a pre-split list (exact, cross-platform) or a string (shlex; non-POSIX on Windows so
        # backslash paths survive).
        args = (list(command) if isinstance(command, (list, tuple))
                else shlex.split(command, posix=(os.name != "nt")))
        return subprocess.Popen(  # noqa: S603 - launching the user-specified agent is the point
            args, cwd=workspace if os.path.isdir(workspace) else None,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            env={**os.environ, **(self.launch_env or {})},
        )


class AcpConnection:
    """A minimal JSON-RPC 2.0 client speaking ACP to a subprocess over newline-delimited stdio."""

    def __init__(
        self,
        proc: "subprocess.Popen[str]",
        cwd: str,
        files: _Files | None = None,
        auth_env_names: set[str] | None = None,
    ) -> None:
        #: The session's working directory as the AGENT sees it (a container path under docker).
        self.cwd = cwd
        self.proc = proc
        #: Names (never values) of env vars we injected into the agent — used to pick which of the
        #: agent's advertised authentication methods can actually work.
        self._auth_env_names = auth_env_names or set()
        self._files = files if files is not None else _HostFiles(cwd)
        self._id = 0
        self._text: list[str] = []
        self._tools: dict[str, int] = {}
        #: toolCallId -> its title, so `tool_call_update`s that omit the title still resolve to a name.
        self._call_titles: dict[str, str] = {}
        #: toolCallIds already counted, so progress updates don't multiply the call count.
        self._counted_calls: set[str] = set()
        self._skills: list[SkillTrigger] = []
        self._seen: set[tuple] = set()
        self._stderr: list[str] = []
        #: Every protocol message, both directions, saved as the run's raw log. Without it an ACP run
        #: left no record of what the agent actually said — so when tokens, cost or a skill came back
        #: missing there was nothing to inspect to find out where they live (or whether they exist).
        self._wire: list[str] = []
        #: Model replies, via the shared rule (core/turns.py): new output while nothing is
        #: outstanding. Verified by hand against a real 269 KB claude transcript → 9.
        self._replies = ReplyCounter()
        #: True while the last entry of ``_text`` is still being appended to by incoming chunks.
        self._block_open = False
        #: Any tool call at all. Distinguishes "the agent reported its actions and none was a skill"
        #: (a real False) from "the agent told us nothing" (unknowable, None).
        self._saw_tool_call = False
        #: Cumulative session cost in USD, from ACP `usage_update` notifications (the only place ACP
        #: reports cost). None = the agent never sent one.
        self._cost_usd: float | None = None
        self._cost_note: str | None = None
        #: Metric-collection problems, reported at the end instead of aborting the run.
        self._parse_errors: list[str] = []
        self._context_used: int | None = None
        self._context_size: int | None = None
        # Drain stderr in the background so a chatty agent can't deadlock on a full pipe.
        self._draining = threading.Thread(target=self._drain_stderr, daemon=True)
        self._draining.start()

    # ------------------------------------------------------------------ transport

    def _drain_stderr(self) -> None:
        try:
            for line in self.proc.stderr:  # type: ignore[union-attr]
                self._stderr.append(line)
                del self._stderr[:-200]
        except Exception:  # noqa: BLE001
            pass

    def stderr_tail(self) -> str:
        return "".join(self._stderr)[-2000:]

    _MAX_WIRE = 4000  # bounded: a long session can emit tens of thousands of chunk notifications

    def _log_wire(self, direction: str, raw: str) -> None:
        self._wire.append(f"{direction} {raw}")
        del self._wire[:-self._MAX_WIRE]

    def wire_log(self) -> str:
        """The full protocol transcript: ``->`` sent by us, ``<-`` sent by the agent."""
        return "\n".join(self._wire)

    def text_so_far(self) -> list[str]:
        """Whatever the agent said before things went wrong — worth keeping on a failure."""
        return list(self._text)

    def _send(self, obj: dict) -> None:
        assert self.proc.stdin is not None
        raw = json.dumps(obj)
        self._log_wire("->", raw)
        self.proc.stdin.write(raw + "\n")
        self.proc.stdin.flush()

    def _request(self, method: str, params: dict) -> dict:
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        return self._pump_until(rid)

    def _pump_until(self, wait_id: int) -> dict:
        """Read messages until the response to ``wait_id`` arrives, handling notifications and
        agent→client requests (permission / fs) inline."""
        assert self.proc.stdout is not None
        while True:
            line = self.proc.stdout.readline()
            if line == "":
                raise AcpError(f"agent closed the stream. stderr: {self.stderr_tail()[-400:]}")
            line = line.strip()
            if not line:
                continue
            self._log_wire("<-", line)
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # some agents interleave non-JSON logs on stdout
            if "method" in msg and msg.get("id") is not None:
                self._handle_agent_request(msg)
            elif "method" in msg:
                self._handle_notification(msg)
            elif msg.get("id") == wait_id:
                if "error" in msg:
                    raise AcpError(json.dumps(msg["error"])[:300])
                return msg.get("result") or {}
            # a response to some other id → ignore

    # ------------------------------------------------------------------ handlers

    def _handle_agent_request(self, msg: dict) -> None:
        """Answer a request FROM the agent, always replying even if our handler misbehaves.

        The agent is blocked waiting on us. If we raise instead of answering, it waits forever and the
        run dies on a timeout with no explanation — so an unexpected shape must become a JSON-RPC
        error reply, never an exception.
        """
        rid = msg.get("id")
        try:
            self._dispatch_agent_request(msg)
        except Exception as exc:  # noqa: BLE001 - see docstring
            note = f"{type(exc).__name__}: {exc}"
            if note not in self._parse_errors:
                self._parse_errors.append(note)
                del self._parse_errors[10:]
            try:
                self._send({"jsonrpc": "2.0", "id": rid,
                            "error": {"code": -32000, "message": note[:300]}})
            except Exception:  # noqa: BLE001 - the pipe is gone; the run is over anyway
                pass

    def _dispatch_agent_request(self, msg: dict) -> None:
        method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if method == "session/request_permission":
            # Auto-approve (evaluation context): prefer an allow option, else the first.
            options = params.get("options") or []
            opt = next((o for o in options if "allow" in str(o.get("kind", "")).lower()), None) \
                or (options[0] if options else None)
            outcome = ({"outcome": "selected", "optionId": opt.get("optionId")}
                       if opt else {"outcome": "cancelled"})
            self._reply(rid, {"outcome": outcome})
        elif method == "fs/read_text_file":
            self._reply_fs_read(rid, params)
        elif method == "fs/write_text_file":
            self._reply_fs_write(rid, params)
        else:
            self._send({"jsonrpc": "2.0", "id": rid,
                        "error": {"code": -32601, "message": f"method not handled: {method}"}})

    def _reply(self, rid, result: dict) -> None:
        self._send({"jsonrpc": "2.0", "id": rid, "result": result})

    def _reply_fs_read(self, rid, params: dict) -> None:
        # Broad on purpose: a bad encoding or malformed base64 payload raises things that are not
        # OSError, and any of them would otherwise leave the agent waiting forever.
        try:
            self._reply(rid, {"content": self._files.read(params.get("path") or "")})
        except Exception as exc:  # noqa: BLE001
            self._send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": str(exc)}})

    def _reply_fs_write(self, rid, params: dict) -> None:
        try:
            self._files.write(params.get("path") or "", params.get("content") or "")
            self._reply(rid, {})
        except Exception as exc:  # noqa: BLE001 - always answer; see _reply_fs_read
            self._send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": str(exc)}})

    def _handle_notification(self, msg: dict) -> None:
        """Dispatch a session notification, never letting a metric bug kill the run.

        Everything in here is BOOKKEEPING — text, turns, tool counts, usage. A defect in it (an
        unexpected message shape, or plain human error: a stale variable name once raised NameError
        here) used to abort the whole trial, throwing away a run the agent had already completed and
        real API spend with it. Losing a metric is acceptable; losing the run is not. Problems are
        collected and surfaced afterwards rather than swallowed.
        """
        try:
            self._dispatch_notification(msg)
        except Exception as exc:  # noqa: BLE001 - see docstring
            note = f"{type(exc).__name__}: {exc}"
            if note not in self._parse_errors:
                self._parse_errors.append(note)
                del self._parse_errors[10:]

    def _dispatch_notification(self, msg: dict) -> None:
        if msg.get("method") != "session/update":
            return
        update = (msg.get("params") or {}).get("update") or {}
        kind = update.get("sessionUpdate")
        if kind in ("agent_message_chunk", "agent_thought_chunk"):
            self._replies.output()
            content = update.get("content") or {}
            if kind == "agent_message_chunk" and content.get("type") == "text" and content.get("text"):
                # Chunks are fragments of one message and are CONCATENATED — a stream can split
                # mid-word ("Working " + "on it."), so joining them with newlines mangles the text.
                # Separate messages are what get newlines between them.
                if self._block_open and self._text:
                    self._text[-1] += content["text"]
                else:
                    self._text.append(content["text"])
                self._block_open = True
        elif kind == "tool_call":
            # A tool the model asked for. Announced TWICE per call by claude-code-acp, which is why
            # the counter tracks ids in a set rather than counting notifications.
            self._replies.started(str(update.get("toolCallId") or ""))
            self._block_open = False
            self._record_tool_call(update)
        elif kind == "tool_call_update":
            # "completed" or "failed" — either way the model is invoked again afterwards.
            if str(update.get("status") or "") in ("completed", "failed"):
                self._replies.finished(str(update.get("toolCallId") or ""))
            self._block_open = False
            self._record_tool_call(update)
        elif kind == "usage_update":
            self._record_usage_update(update)

    def _record_usage_update(self, update: dict) -> None:
        """ACP's ``usage_update``: the session's running cost and how full the context window is.

        This is the ONLY place ACP reports cost, and ignoring it is why an ACP run showed none. The
        figures are cumulative for the session, so the last one seen is the total. claude-acp and
        codex-acp send these; gemini-cli currently does not.
        """
        cost = update.get("cost")
        if isinstance(cost, dict):
            amount = cost.get("amount")
            currency = str(cost.get("currency") or "USD").upper()
            try:
                value = float(amount)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                value = None
            # Only USD is recorded as a cost: silently treating EUR as dollars would corrupt totals.
            if value is not None and currency == "USD":
                self._cost_usd = value
            elif value is not None:
                self._cost_note = f"agent reported {value} {currency}; not recorded (USD only)"
        used, size = _as_int(update.get("used")), _as_int(update.get("size"))
        if used:
            self._context_used = used
        if size:
            self._context_size = size

    def _record_tool_call(self, update: dict) -> None:
        title = str(update.get("title") or "")
        call_id = str(update.get("toolCallId") or "")
        self._saw_tool_call = True

        # Count by tool NAME, once per call. Keying on toolCallId made every call its own "tool"
        # (`toolu_01D5em…: 4`) and inflated the total 4x, because ACP sends one `tool_call` plus
        # several `tool_call_update`s as a single call progresses. The first notification carries the
        # title; later updates may omit it, so remember it per id.
        # Prefer the agent's REAL tool name when it exposes one. claude-code-acp puts it in
        # `_meta.claudeCode.toolName` (Bash / Skill / mcp__acp__Read) while `title` is a generic
        # human label like "Read File" — the real names are far more useful for comparison.
        real = _vendor_tool_name(update) or title
        if call_id and real:
            self._call_titles.setdefault(call_id, real)
        name = real or self._call_titles.get(call_id) or update.get("kind") or "tool"
        if call_id:
            if call_id not in self._counted_calls:
                self._counted_calls.add(call_id)
                self._tools[name] = self._tools.get(name, 0) + 1
        else:
            self._tools[name] = self._tools.get(name, 0) + 1
        # Skill evidence, in the order SkillsBench's own audit looks for it: a dedicated skill tool
        # (claude's "Launching skill: <name>"), a read under `.../skills/<name>/...`, or any SKILL.md.
        haystack = json.dumps(update) if update else ""
        if "skill" in haystack.lower():
            m = _LAUNCHING_RE.search(haystack)
            # `name` is already resolved above (title, else the remembered title, else the kind) — the
            # earlier code referenced a variable that no longer existed, which only blew up when a
            # tool call arrived WITHOUT a title, so `or` never short-circuited past it.
            self._add_skill(m.group(1).strip() if m else name, TriggerSource.TOOL_USE, haystack)
        m = _SKILL_PATH_RE.search(haystack)
        if m:
            self._add_skill(m.group(1), TriggerSource.FILE_READ, haystack)
        elif _SKILL_MD_RE.search(haystack):
            self._add_skill(name or "SKILL.md", TriggerSource.FILE_READ, haystack)

    def _add_skill(self, name: str, source: TriggerSource, details: str) -> None:
        key = (source, name)
        if key not in self._seen:
            self._seen.add(key)
            self._skills.append(SkillTrigger(name=name, source=source, details=details[:200]))

    # ------------------------------------------------------------------ flow

    def run_prompt(self, instruction: str) -> RunOutput:
        init = self._request("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": True, "writeTextFile": True}},
        })
        try:
            session = self._request("session/new", {"cwd": self.cwd, "mcpServers": []})
        except AcpError as exc:
            # Some agents (codex-acp) refuse session/new until the client sends `authenticate`,
            # even when the API key env var is already set — gemini and claude-code-acp never ask,
            # which is why this path went unexercised. Pick the advertised method whose env vars we
            # actually injected, authenticate, and retry ONCE.
            method = _pick_auth_method(init.get("authMethods") or [], self._auth_env_names)
            if method is None or "auth" not in str(exc).lower():
                raise
            self._log_wire("!!", f"agent requires authentication - using method '{method}'")
            self._request("authenticate", {"methodId": method})
            session = self._request("session/new", {"cwd": self.cwd, "mcpServers": []})
        session_id = session.get("sessionId")
        if not session_id:
            raise AcpError("session/new returned no sessionId")
        model = _session_model(session)
        result = self._request("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": instruction}],
        })

        stop = result.get("stopReason")
        error = None
        if stop and stop not in ("end_turn", "completed", "stop", "done"):
            error = f"stopReason={stop}"

        # Definitive both ways, matching how gemini is measured: evidence → True; the agent reported
        # tool calls and none was a skill → False; it reported nothing → None (silence isn't inaction).
        if self._skills:
            skill_opened: bool | None = True
        elif self._saw_tool_call:
            skill_opened = False
        else:
            skill_opened = None

        in_tok, out_tok, total_tok, cached = _usage_tokens(result)
        if self._parse_errors:
            # Surfaced, not silent — but as a note appended to the outcome, because these are metric
            # problems on OUR side. The agent's work still stands and is still graded.
            self._log_wire("!!", "metric collection problems: " + "; ".join(self._parse_errors))
        return RunOutput(
            output="\n".join(self._text).strip(),
            # The raw log is the PROTOCOL TRANSCRIPT, not just the agent's prose: when a metric comes
            # back missing this is the only way to see whether the agent ever sent it.
            raw_output=self.wire_log(),
            model=model,
            num_turns=self._replies.value,
            num_turns_reported=_reported_turns(result),
            input_tokens=in_tok,
            output_tokens=out_tok,
            total_tokens=total_tok,
            cached_input_tokens=cached,
            cost_usd=self._cost_usd,
            tools_used=sorted(self._tools),
            tool_counts=dict(self._tools),
            skills_triggered=self._skills,
            skill_opened=skill_opened,
            skill_files_read=[s.details for s in self._skills if s.source == TriggerSource.FILE_READ and s.details],
            error=error,
        )

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def _sandbox_cwd(run_command: RunCommand, fallback: str = "/workspace") -> str:
    """The working directory inside the sandbox, as the agent will see it.

    ``run_command`` already executes with the sandbox's workdir set, so ``pwd`` reports exactly the
    path the agent should be told about. Login shells can print banners, so the last non-empty
    absolute line wins.
    """
    try:
        res = run_command("pwd")
    except Exception:  # noqa: BLE001 - never let a probe fail the run
        return fallback
    for line in reversed((res.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("/"):
            return line
    return fallback


def _as_int(value: object) -> int | None:
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _reported_turns(result: dict) -> int | None:
    """A turn count only if the AGENT states one. Never inferred — see the note at the call site."""
    for block in (result.get("usage"), result.get("tokenUsage"), result):
        if isinstance(block, dict):
            for key in ("turns", "num_turns", "numTurns", "turn_count", "turnCount"):
                n = _as_int(block.get(key))
                if n:
                    return n
    return None


def _usage_tokens(result: dict) -> tuple[int | None, int | None, int | None, int | None]:
    """Token counts from a ``session/prompt`` reply: ``(input, output, total, cached_read)``.

    ACP *does* define this: ``PromptResponse.usage`` with ``input_tokens`` / ``output_tokens`` /
    ``total_tokens``, plus optional ``thought_tokens`` / ``cached_read_tokens`` / ``cached_write_tokens``.

    gemini-cli, however, reports tokens off-spec at ``_meta.quota.token_count`` and no cost at all
    (google-gemini/gemini-cli#24280), which is why an ACP gemini run showed nothing. That fallback is
    checked second so a spec-compliant agent always wins.
    """
    for key in ("usage", "tokenUsage", "tokens"):
        block = result.get(key)
        if isinstance(block, dict):
            in_tok = _as_int(block.get("input_tokens") or block.get("inputTokens") or block.get("prompt"))
            out_tok = _as_int(block.get("output_tokens") or block.get("outputTokens") or block.get("completion"))
            total = _as_int(block.get("total_tokens") or block.get("totalTokens"))
            cached = _as_int(block.get("cached_read_tokens") or block.get("cachedReadTokens"))
            # Thinking tokens are billed as output, so fold them in rather than losing them.
            thought = _as_int(block.get("thought_tokens") or block.get("thoughtTokens"))
            if thought and out_tok:
                out_tok += thought
            elif thought and not out_tok:
                out_tok = thought
            if in_tok or out_tok or total:
                return in_tok, out_tok, total, cached

    # gemini's non-standard spot: a bare total, with no input/output split.
    meta = result.get("_meta")
    if isinstance(meta, dict):
        quota = meta.get("quota")
        if isinstance(quota, dict):
            total = _as_int(quota.get("token_count") or quota.get("tokenCount"))
            if total:
                return None, None, total, None
    return None, None, None, None


def replay_wire_log(transcript: str) -> RunOutput:
    """Re-derive a run's metrics from a saved protocol transcript (``raw.log``).

    The transcript is the whole conversation, so every metric that came from it can be recomputed
    later — which is the point of recording it. Used by ``adarubric recompute`` to bring older runs up
    to date when the parsing improves, without re-running (and re-paying for) the agent.

    Only lines the AGENT sent (``<-``) are replayed; our own (``->``) carry no metrics.
    """
    conn = AcpConnection.__new__(AcpConnection)  # no subprocess: we're reading, not talking
    conn.cwd = ""
    conn._files = _HostFiles("")
    conn._id = 0
    conn._text = []
    conn._tools = {}
    conn._call_titles = {}
    conn._counted_calls = set()
    conn._skills = []
    conn._seen = set()
    conn._stderr = []
    conn._wire = []
    conn._replies = ReplyCounter()
    conn._block_open = False
    conn._saw_tool_call = False
    conn._cost_usd = None
    conn._cost_note = None
    conn._parse_errors = []
    conn._context_used = None
    conn._context_size = None

    model, result = None, {}
    for line in (transcript or "").splitlines():
        if not line.startswith("<-"):
            continue
        try:
            msg = json.loads(line[3:])
        except json.JSONDecodeError:
            continue
        if "method" in msg:
            conn._handle_notification(msg)
            continue
        payload = msg.get("result")
        if not isinstance(payload, dict):
            continue
        if model is None:
            model = _session_model(payload)          # the session/new reply
        if "stopReason" in payload:
            result = payload                          # the session/prompt reply

    in_tok, out_tok, total_tok, cached = _usage_tokens(result)
    return RunOutput(
        output="\n".join(conn._text).strip(),
        raw_output=transcript,
        model=model,
        num_turns=conn._replies.value,
        num_turns_reported=_reported_turns(result),
        input_tokens=in_tok,
        output_tokens=out_tok,
        total_tokens=total_tok,
        cached_input_tokens=cached,
        cost_usd=conn._cost_usd,
        tools_used=sorted(conn._tools),
        tool_counts=dict(conn._tools),
        skills_triggered=conn._skills,
        skill_opened=(True if conn._skills else (False if conn._saw_tool_call else None)),
        skill_files_read=[s.details for s in conn._skills
                          if s.source == TriggerSource.FILE_READ and s.details],
    )
