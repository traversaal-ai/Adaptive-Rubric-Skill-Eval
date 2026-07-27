"""Progress reporters — sinks for runner lifecycle events (live tracking).

The ``ProgressReporter`` contract lives in ``core/contracts.py``. Concrete reporters, all thread-safe:
  - TerminalReporter  — live console table via `rich`            (piece 1.5)
  - StatusReporter    — writes status.json / events.jsonl to disk (piece 1.5 / Phase 6)
  - WebReporter       — serves the localhost dashboard, pushes via SSE (Phase 6)

Placeholder for now; implemented as noted above.
"""
