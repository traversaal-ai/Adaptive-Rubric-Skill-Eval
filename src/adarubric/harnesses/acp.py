"""Generic ACP (Agent Client Protocol) harness — run ANY ACP-speaking agent.

ACP (https://agentclientprotocol.com) is a JSON-RPC 2.0 protocol over stdio for driving coding
agents (Zed's agents, ``gemini --acp``, and others). This adapter is *generic*: point it at any
ACP launch command with ``--acp-cmd`` and it drives the standard flow —
``initialize`` → ``session/new`` → ``session/prompt`` — collecting the agent's text, tool calls, and
(from the tool-call stream) skill usage.

Scope of this first cut: **``--sandbox local`` only**. ACP needs an interactive stdio session, which
the fire-and-return ``run_command`` seam can't provide, so the agent is spawned directly in the
workspace dir. A Docker bridge (``docker exec -i``) is a follow-up.

Dependency-free: a ~120-line JSON-RPC/stdio client (no ACP SDK needed). Protocol shapes are taken
from the ACP v1 spec; a real agent may differ in edge details — verify against your target agent.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import threading

from adarubric.core.contracts import Harness, RunCommand
from adarubric.core.models import RunOutput, SkillTrigger, TriggerSource

_PROTOCOL_VERSION = 1
_SKILL_PATH_RE = re.compile(r"[\\/]\.(?:claude|agents|gemini|codex)[\\/]skills[\\/]([^\\/\s'\"]+)")


class AcpError(Exception):
    """A protocol- or transport-level ACP failure."""


class AcpHarness(Harness):
    name = "acp"
    cli = "acp"
    env_keys: tuple[str, ...] = ()  # set from --acp-env-key (unknown for a generic agent)
    # The wrapped agent's real skill-discovery dir(s). Default to the cross-agent `.agents/skills`
    # alias; override with --acp-skill-dir to match the specific agent (see coding_agent_harness.md §2).
    skill_dirs: tuple[str, ...] = (".agents/skills",)
    docker_install = ""  # local-only for now — nothing to install into an image

    #: The launch command for the ACP agent, e.g. "gemini --acp". Set by the CLI from --acp-cmd.
    command: str = ""
    #: Environment for the spawned agent (os.environ + injected declared keys). Set by the CLI.
    launch_env: dict[str, str] | None = None

    def run(self, instruction: str, workspace: str, run_command: RunCommand) -> RunOutput:
        if not self.command:
            return RunOutput(output="", error="ACP harness requires --acp-cmd (e.g. --acp-cmd 'gemini --acp').")
        if not os.path.isdir(workspace):
            return RunOutput(
                output="",
                error="ACP harness supports --sandbox local only for now (got a non-filesystem workspace).",
            )
        conn = AcpConnection(self.command, workspace, env=self.launch_env)
        try:
            return conn.run_prompt(instruction)
        except AcpError as exc:
            return RunOutput(output="", raw_output=conn.stderr_tail(), error=f"ACP error: {exc}")
        finally:
            conn.close()


class AcpConnection:
    """A minimal JSON-RPC 2.0 client speaking ACP to a subprocess over newline-delimited stdio."""

    def __init__(self, command, cwd: str, env: dict[str, str] | None = None) -> None:
        self.cwd = cwd
        # Accept a pre-split list (exact, cross-platform) or a string (shlex; non-POSIX split on
        # Windows so backslash paths survive). Typical commands ("gemini --acp") split cleanly either way.
        args = list(command) if isinstance(command, (list, tuple)) else shlex.split(command, posix=(os.name != "nt"))
        self.proc = subprocess.Popen(  # noqa: S603 - launching the user-specified agent is the point
            args,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env={**os.environ, **(env or {})},
        )
        self._id = 0
        self._text: list[str] = []
        self._tools: dict[str, int] = {}
        self._skills: list[SkillTrigger] = []
        self._seen: set[tuple] = set()
        self._stderr: list[str] = []
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

    def _send(self, obj: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(obj) + "\n")
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
        path = params.get("path") or ""
        try:
            text = _resolve(self.cwd, path).read_text(encoding="utf-8", errors="replace")
            self._reply(rid, {"content": text})
        except OSError as exc:
            self._send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": str(exc)}})

    def _reply_fs_write(self, rid, params: dict) -> None:
        path = params.get("path") or ""
        try:
            p = _resolve(self.cwd, path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(params.get("content") or "", encoding="utf-8")
            self._reply(rid, {})
        except OSError as exc:
            self._send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": str(exc)}})

    def _handle_notification(self, msg: dict) -> None:
        if msg.get("method") != "session/update":
            return
        update = (msg.get("params") or {}).get("update") or {}
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            content = update.get("content") or {}
            if content.get("type") == "text" and content.get("text"):
                self._text.append(content["text"])
        elif kind in ("tool_call", "tool_call_update"):
            self._record_tool_call(update)

    def _record_tool_call(self, update: dict) -> None:
        title = str(update.get("title") or "")
        tool_id = str(update.get("toolCallId") or title or "?")
        self._tools[tool_id] = self._tools.get(tool_id, 0) + 1
        # Skill evidence: a tool titled like a skill, or one touching a `.../skills/<name>/...` path.
        haystack = " ".join(str(update.get(k, "")) for k in ("title", "rawInput", "locations", "kind"))
        if "skill" in haystack.lower():
            self._add_skill(title or tool_id, TriggerSource.TOOL_USE, haystack)
        m = _SKILL_PATH_RE.search(haystack)
        if m:
            self._add_skill(m.group(1), TriggerSource.FILE_READ, haystack)

    def _add_skill(self, name: str, source: TriggerSource, details: str) -> None:
        key = (source, name)
        if key not in self._seen:
            self._seen.add(key)
            self._skills.append(SkillTrigger(name=name, source=source, details=details[:200]))

    # ------------------------------------------------------------------ flow

    def run_prompt(self, instruction: str) -> RunOutput:
        self._request("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": True, "writeTextFile": True}},
        })
        session = self._request("session/new", {"cwd": self.cwd, "mcpServers": []})
        session_id = session.get("sessionId")
        if not session_id:
            raise AcpError("session/new returned no sessionId")
        result = self._request("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": instruction}],
        })

        stop = result.get("stopReason")
        error = None
        if stop and stop not in ("end_turn", "completed", "stop", "done"):
            error = f"stopReason={stop}"
        return RunOutput(
            output="\n".join(self._text).strip(),
            raw_output="\n".join(self._text),
            tools_used=sorted(self._tools),
            tool_counts=dict(self._tools),
            skills_triggered=self._skills,
            # Generic ACP agents vary in how skills surface, so: evidence → True, else unknown (None).
            skill_opened=True if self._skills else None,
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


def _resolve(cwd: str, path: str):
    """Resolve an ACP fs path (absolute or relative to the session cwd)."""
    from pathlib import Path

    p = Path(path)
    return p if p.is_absolute() else Path(cwd) / p
