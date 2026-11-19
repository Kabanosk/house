"""`house decisions`: the decisions of every contest in `contests.yaml`, one line per entry.

Used by: `house decisions` and the `/house-ideate` and `/house-start` skills, so that a human decision from an earlier
contest ("we don't train big models on this hardware") is found instead of remembered. Reads each contest's
`.house/DECISIONS.md` through `decisions.parse()` (entries only inside `## D-###` blocks, so the template header
is never an entry). Filtering is on plain fields only; full-text search is another project's job.

Line format: `<contest> D-### <date> <who>: <title> — <path>:<line>`. `<path>` is relative to the house root (e.g.
`contests/calc/.house/DECISIONS.md:12`), so no home directory leaks into pasted output. `<date>` is the date part of
`Data` (`?` when missing or unparsable: it must start with `YYYY-MM-DD`, anything may follow, e.g. `2026-11-17, 14:30`)
and `<who>` the `Kto` value as written (`unknown` when missing). A superseded entry (named in `Zastępuje` of another
entry of the same contest; ids are per contest) is hidden unless `all`, and then carries `[zastąpiona przez D-###]`.
A human entry is superseded only by a human entry (`decisions.supersession`): when an agent entry names it, it stays
listed and carries `[podważona przez D-### (agent)]`, the same rule `active_human` uses for the SessionStart hook.
`exclude` leaves out whole contests (the current one, whose decisions the skills already have).
"""
import re
from datetime import datetime
from pathlib import Path

from . import decisions, registry, scaffold
from .decisions import Decision

Entry = tuple[str, Path, Decision]

_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?!\d)")


def _note(name: str, why: str) -> str:
    return f"note: {name}: {why}"


def entries(root: Path, contest: str | None = None, notes: list[str] | None = None) -> list[Entry]:
    """Read the decision entries of the registered contests.

    Args:
        root: House repo root (holds `contests.yaml` and `contests/`).
        contest: Only this registered contest; None means every registered contest.
        notes: When given, one `note: <contest>: ...` line is appended for each contest that is skipped (invalid
            name, no directory, no `.house/DECISIONS.md`).

    Returns:
        `(contest name, path of its DECISIONS.md relative to `root`, Decision)` in registry order, then file order.

    Raises:
        ValueError: `contest` is not in the registry.
    """
    names = list(registry.load(root / "contests.yaml"))
    if contest is not None:
        if contest not in names:
            raise ValueError(f"unknown contest {contest!r}; registered: {', '.join(names) or 'none'}")
        names = [contest]
    out: list[Entry] = []
    for name in names:
        try:
            scaffold.validate_name(name)
        except ValueError:
            if notes is not None:
                notes.append(_note(name, "not a valid contest name (skipped)"))
            continue
        path = Path("contests") / name / ".house" / "DECISIONS.md"
        if not (root / path).is_file():
            if notes is not None:
                notes.append(_note(name, "no .house/DECISIONS.md (skipped)"))
            continue
        text = (root / path).read_text(encoding="utf-8", errors="replace")
        out.extend((name, path, d) for d in decisions.parse(text))
    return out


def _by_contest(items: list[Entry]) -> dict[str, list[Decision]]:
    out: dict[str, list[Decision]] = {}
    for contest, _, d in items:
        out.setdefault(contest, []).append(d)
    return out


def superseded_by(items: list[Entry]) -> dict[tuple[str, str], list[str]]:
    """Map each superseded entry to the ids that supersede it.

    Args:
        items: Entries as returned by `entries()` (all of them: a filter may drop the superseding entry).

    Returns:
        `(contest, id)` to the ids of the same contest whose `Zastępuje` names it and that may retire it, in file
        order: a human entry is retired only by a human entry (see `challenged_by` for the agent ones). An entry never
        supersedes itself.
    """
    return {(c, i): ids for c, ds in _by_contest(items).items() for i, ids in decisions.supersession(ds)[0].items()}


def challenged_by(items: list[Entry]) -> dict[tuple[str, str], list[str]]:
    """Map each human entry that an agent entry names in `Zastępuje` to those agent entries.

    Args:
        items: Entries as returned by `entries()` (all of them).

    Returns:
        `(contest, id)` to the ids of the same contest, in file order. Such a human entry is not superseded.
    """
    return {(c, i): ids for c, ds in _by_contest(items).items() for i, ids in decisions.supersession(ds)[1].items()}


def _date(d: Decision) -> str | None:
    """The `YYYY-MM-DD` part of the entry's `Data` field (`YYYY-MM-DD[ HH:MM]`), or None when unusable."""
    value = next((v for k, v in d.fields.items() if k.lower() == "data"), "")
    m = _DATE_RE.match(value.strip())
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def select(items: list[Entry], by: str | None = None, since: str | None = None, grep: str | None = None,
           all: bool = False, exclude: list[str] | None = None) -> list[Entry]:  # noqa: A002 — `all` is `--all`
    """Filter entries; every given filter must match.

    Args:
        items: Entries from `entries()`.
        by: "human" or "agent" (an entry with no `Kto` is "unknown" and matches neither); None for any.
        since: `YYYY-MM-DD`; keeps entries whose `Data` date is this day or later. An entry whose date cannot be
            parsed is dropped when `since` is given.
        grep: Literal, case-insensitive substring of the title or of a field value (not a regex).
        all: Also keep superseded entries (hidden by default).
        exclude: Contest names to leave out entirely (names that are not in `items` change nothing).

    Returns:
        The matching entries in their original order.
    """
    gone = superseded_by(items)
    skip = set(exclude or ())
    needle = grep.lower() if grep else None
    out: list[Entry] = []
    for contest, path, d in items:
        if contest in skip or (not all and (contest, d.id) in gone):
            continue
        if by and d.by != by:
            continue
        if since is not None:
            date = _date(d)
            if date is None or date < since:
                continue
        if needle and not any(needle in s.lower() for s in (d.title, *d.fields.values())):
            continue
        out.append((contest, path, d))
    return out


def _who(d: Decision) -> str:
    return next((v for k, v in d.fields.items() if k.lower() == "kto" and v), "unknown")


def render(items: list[Entry], full: bool = False, superseded: dict[tuple[str, str], list[str]] | None = None,
           challenged: dict[tuple[str, str], list[str]] | None = None) -> str:
    """Render entries, one line each.

    Args:
        items: Entries, usually from `select()`.
        full: Also print the entry text under its line (without its `## D-### title` header), indented by four spaces,
            with a blank line after.
        superseded: Result of `superseded_by()` over all entries; defaults to the one of `items`, which misses a
            superseding entry that a filter removed.
        challenged: Result of `challenged_by()` over all entries; defaults to the one of `items`.

    Returns:
        The text without a trailing newline; "" for no entries.
    """
    gone = superseded if superseded is not None else superseded_by(items)
    doubted = challenged if challenged is not None else challenged_by(items)
    blocks = []
    for contest, path, d in items:
        mark = f" [zastąpiona przez {', '.join(gone[(contest, d.id)])}]" if gone.get((contest, d.id)) else ""
        if doubted.get((contest, d.id)):
            mark += f" [podważona przez {', '.join(doubted[(contest, d.id)])} (agent)]"
        line = f"{contest} {d.id} {_date(d) or '?'} {_who(d)}: {d.title}{mark} — {path}:{d.line}"
        if full:
            body = d.text.splitlines()[1:]  # the `## D-### title` header is already in the line
            line += "\n" + "\n".join(f"    {t}" if t else "" for t in body) + "\n"
        blocks.append(line)
    return "\n".join(blocks).rstrip("\n")
