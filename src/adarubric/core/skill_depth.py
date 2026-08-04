"""Did the agent *notice* the skill, or actually *use* it?

A skill is a front page plus its detail::

    fuzzy-match/
      SKILL.md                  <- headings and a summary
      references/detail.md      <- the actual how-to

A yes/no "was a skill opened" cannot tell these apart:

* opened ``SKILL.md``, skimmed the headings, then did its own thing
* opened ``SKILL.md``, followed its links, and worked from the detail

Both score "opened", but only the second is skill use. SkillsBench's own audit found codex reading
three front pages, never opening a single linked file, then writing code that ignored the guidance —
scored 0.45. A boolean calls that a success.

So depth is reported alongside the boolean:

``USED``       the agent went past the front page (a ``references/``/``scripts/`` file, or any other
              file inside the skill folder) — evidence it worked *from* the skill
``NOTICED``    a skill was opened, but only its front page
``NOT_OPENED`` skills were available and definitively untouched
``None``       the harness cannot tell us (gemini reports per-tool tallies with no file paths, so its
              depth is genuinely unknowable — which is one argument for driving agents over ACP)

This measures which FILES were reached. It does not check whether the agent then followed the
advice — that needs judging the work itself, which is a separate step.
"""

from __future__ import annotations

import re

from adarubric.core.models import SkillTrigger

#: Depth values, deliberately stable strings (they land in run.json and the dashboard reads them).
USED = "used"
NOTICED = "noticed"
NOT_OPENED = "not_opened"

#: A path inside a skill folder that is NOT the front page: the conventional subdirectories, or any
#: other file sitting beside SKILL.md. Matches "/" and "\" and their JSON-escaped forms, because the
#: evidence string may be a raw command, a Windows path, or a JSON blob depending on the harness.
_SEP = r"(?:\\{1,2}|/)"
_DEEP_SUBDIR_RE = re.compile(
    rf"skills{_SEP}[^\\/\s'\"]+{_SEP}(?:references|reference|scripts|assets|examples|docs){_SEP}",
    re.IGNORECASE,
)
_SKILL_FILE_RE = re.compile(rf"skills{_SEP}[^\\/\s'\"]+{_SEP}([^\\/\s'\";|&)]+)", re.IGNORECASE)
_FRONT_PAGE = "skill.md"


def _is_deep(evidence: str) -> bool:
    """True if ``evidence`` shows a file inside a skill folder other than its front page."""
    if _DEEP_SUBDIR_RE.search(evidence):
        return True
    for match in _SKILL_FILE_RE.finditer(evidence):
        leaf = match.group(1).strip().strip("'\"").lower()
        # A bare subdirectory name (no extension) isn't proof a file inside it was read.
        if leaf and leaf != _FRONT_PAGE and "." in leaf:
            return True
    return False


def classify(skill_opened: bool | None, triggers: list[SkillTrigger]) -> str | None:
    """Grade how deeply the skills were used, from whatever trajectory evidence the harness gave.

    ``skill_opened`` carries the harness's own verdict, including the meaningful difference between a
    definitive ``False`` and an unknowable ``None``; ``triggers`` carry the file/tool evidence.
    """
    if triggers:
        haystack = " ".join(f"{t.name} {t.details or ''}" for t in triggers)
        return USED if _is_deep(haystack) else NOTICED
    if skill_opened is True:
        # The harness is sure a skill was opened but gave no paths (gemini's tool tallies): we know it
        # was noticed and cannot know more. Claiming USED here would be a guess.
        return NOTICED
    if skill_opened is False:
        return NOT_OPENED
    return None
