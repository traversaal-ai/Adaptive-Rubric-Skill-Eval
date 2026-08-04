"""Counting **model replies** — one definition, every harness.

"Turns" is only comparable if it counts the same event everywhere. It didn't, for a long time, and
each agent's own number turned out to mean something different:

===============  ==============================  ==========================
harness          its own number said             actual model replies
===============  ==============================  ==========================
claude-code      ``num_turns: 20``               **15** (distinct message ids)
codex            ``turn.completed: 1``           prompt cycles, not replies
acp              *nothing*                       **9** (measured below)
===============  ==============================  ==========================

A **model reply** is one invocation of the model. In a reply it can say something, request several
tools *at once*, or both. Then the tools run, the results go back, and the model is invoked again —
that next invocation is the next reply.

So a reply begins when the agent produces anything while **no tool call is still outstanding**. Two
details make or break it, both learned from a real transcript:

* **Track outstanding calls by id, not by message count.** claude-code-acp announces every tool call
  *twice*; counting notifications meant the outstanding tally never fell back to zero, and a whole
  conversation collapsed to "1 reply".
* **A batch of tool calls with no preceding text is still a reply.** Counting only blocks of speech
  reported 8 where the truth was 9.

Parallel tool calls are counted once, because they came from a single invocation.
"""

from __future__ import annotations


class ReplyCounter:
    """Counts model replies from a stream of agent events.

    Feed it three kinds of signal, in the order they arrive::

        c = ReplyCounter()
        c.output()             # the agent said something (text / reasoning)
        c.started("call-1")    # it requested a tool
        c.finished("call-1")   # that tool finished
        c.replies              # -> the count

    Repeated ``started``/``finished`` for the same id are harmless, so duplicate notifications and
    missing completions can't corrupt the count.
    """

    def __init__(self) -> None:
        self.replies = 0
        #: Tool calls requested and not yet finished, by id. A set, so duplicate announcements of the
        #: same call don't inflate it — the bug that once made a 9-reply run read as 1.
        self._outstanding: set[str] = set()
        #: True while we're inside a reply we've already counted.
        self._open = False

    def _begin(self) -> None:
        if not self._open and not self._outstanding:
            self.replies += 1
            self._open = True

    def output(self) -> None:
        """The agent produced text or reasoning."""
        self._begin()

    def started(self, call_id: str | None) -> None:
        """The agent requested a tool. A batch with no words before it still begins a reply."""
        self._begin()
        self._outstanding.add(call_id or f"anon-{len(self._outstanding)}")

    def finished(self, call_id: str | None) -> None:
        """A tool finished (completed OR failed — either way the model gets called again)."""
        self._outstanding.discard(call_id or "")
        if not self._outstanding:
            # Everything it asked for is done, so whatever comes next is a fresh invocation.
            self._open = False

    @property
    def value(self) -> int | None:
        """The count, or ``None`` when nothing was observed (honest unknown, not a zero)."""
        return self.replies or None
