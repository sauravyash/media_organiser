"""Shared issue model for the read-only library audit dashboards.

Both the movie dashboard (:mod:`media_organiser.library`) and the music
dashboard (:mod:`media_organiser.music`) report findings as :class:`Issue`
records so the web UI can render, filter and count them the same way.

Nothing here mutates the library — an issue is a *recommendation* for the user
to verify manually.
"""
from dataclasses import dataclass, asdict
from typing import Iterable, Optional

# Ordered most to least urgent.
SEVERITIES = ("high", "medium", "low")
_SEVERITY_RANK = {name: i for i, name in enumerate(SEVERITIES)}


@dataclass
class Issue:
    """A single recommended change for the user to verify manually.

    ``kind`` is a stable slug (used for filtering in the UI), ``message``
    explains what looks wrong, and ``suggestion`` — when present — is the
    concrete change to make (a target filename, or a ``beet`` command).
    """
    kind: str
    severity: str
    message: str
    suggestion: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def severity_rank(severity: str) -> int:
    """Sort key for a severity; unknown severities sort last."""
    return _SEVERITY_RANK.get(severity, len(SEVERITIES))


def sort_issues(issues: Iterable[Issue]) -> list[Issue]:
    """Most urgent first, then alphabetically by kind for a stable order."""
    return sorted(issues, key=lambda i: (severity_rank(i.severity), i.kind, i.message))


def worst_severity(issues: Iterable[Issue]) -> Optional[str]:
    """The most urgent severity present, or None when there are no issues."""
    best = None
    for issue in issues:
        if best is None or severity_rank(issue.severity) < severity_rank(best):
            best = issue.severity
    return best


def summarise(entries: Iterable[dict]) -> dict:
    """Roll up issue counts across already-serialised entries.

    Each entry is expected to carry an ``issues`` list of dicts (as produced by
    :meth:`Issue.to_dict`). Returns totals plus per-severity and per-kind counts.
    """
    entries = list(entries)
    by_severity = {s: 0 for s in SEVERITIES}
    by_kind: dict[str, int] = {}
    total_issues = 0
    flagged = 0

    for entry in entries:
        issues = entry.get("issues") or []
        if issues:
            flagged += 1
        for issue in issues:
            total_issues += 1
            severity = issue.get("severity", "low")
            by_severity[severity] = by_severity.get(severity, 0) + 1
            kind = issue.get("kind", "unknown")
            by_kind[kind] = by_kind.get(kind, 0) + 1

    return {
        "total": len(entries),
        "flagged": flagged,
        "clean": len(entries) - flagged,
        "issues": total_issues,
        "by_severity": by_severity,
        # Most common kinds first so the UI filter chips are usefully ordered.
        "by_kind": dict(sorted(by_kind.items(), key=lambda kv: (-kv[1], kv[0]))),
    }
