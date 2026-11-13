"""Rules enforced by code rather than prompts (spec §6).

Used by the Claude Code hook (`bin/house-hook`, before a tool runs), by `house guard diff` and by `house merge`
(after the fact, on a task branch — this also covers Codex, which has no hooks).
"""
import hashlib
import json
import re
import shlex
import subprocess
from pathlib import Path

from . import spec as specmod
from .export import SECRET_RES
from .fm import parse_frontmatter

CONTROL = {"&&", "||", ";", "|", "&", "\n"}
REDIRECTS = {">", ">>", ">|", "&>", "&>>", "1>", "2>"}
FINISHED = ("merged", "done")


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def contest_root(cwd: Path) -> Path | None:
    """The main checkout (with `.house/`) that cwd belongs to, also from a task worktree anywhere."""
    cwd = Path(cwd).resolve()
    for d in (cwd, *cwd.parents):
        if (d / ".house").is_dir() and (d / ".git").is_dir():  # a worktree has a .git *file* and its own
            return d                                             # tracked copy of .house/: keep looking
        if (d / ".git").is_file():
            break
    r = _git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir") if cwd.is_dir() else None
    if r and r.returncode == 0:
        main = Path(r.stdout.strip()).parent
        if (main / ".house").is_dir():
            return main.resolve()
    return None


def repo_relative(path: str, cwd: Path, contest: Path) -> str | None:
    """`path` relative to the checkout it lives in (the contest checkout or one of its worktrees)."""
    p = Path(path)
    p = (p if p.is_absolute() else Path(cwd) / p).resolve()
    contest = contest.resolve()
    worktrees = contest / ".worktrees"
    if p.is_relative_to(worktrees):
        parts = p.relative_to(worktrees).parts
        return str(Path(*parts[1:])) if len(parts) > 1 else None
    if p.is_relative_to(contest):
        return str(p.relative_to(contest))
    top = _git(p.parent if p.parent.is_dir() else Path(cwd), "rev-parse", "--show-toplevel")
    if top.returncode == 0 and contest_root(Path(top.stdout.strip())) == contest:
        return str(p.relative_to(Path(top.stdout.strip()).resolve()))
    return None


def _checks(contest: Path) -> dict[str, dict]:
    out = {}
    for path in sorted((contest / ".house/checks").glob("*.json")):
        try:
            out[path.stem] = json.loads(path.read_text())
        except ValueError:
            continue
    return out


def _scorer_files(contest: Path) -> list[str]:
    scorer = specmod.get(specmod.load_spec(contest / ".house"), "scoring.local_scorer")
    if not isinstance(scorer, str):
        return []
    try:
        tokens = shlex.split(scorer)
    except ValueError:
        tokens = scorer.split()
    return [t for t in tokens if "/" in t or "." in t if (contest / t).is_file()]


def protected_paths(contest: Path) -> dict[str, str]:
    """rel path → owner (task id or "local_scorer"). Only files whose content is pinned (hash known)."""
    out = {}
    for tid, data in _checks(contest).items():
        for rel, sha in (data.get("frozen", {}).get("protected") or {}).items():
            if sha:
                out[rel] = tid
    for rel in _scorer_files(contest):
        out.setdefault(rel, "local_scorer")
    return out


def check_edit(rel: str, contest: Path) -> str | None:
    if rel == ".house/checks" or rel.startswith(".house/checks/"):
        return f"{rel} is written only by `house check` (frozen acceptance criteria)"
    owner = protected_paths(contest).get(rel)
    if owner:
        return f"{rel} is protected ({owner}); acceptance tests and the scorer must not be changed by workers"
    return None


def _tokens(command: str) -> list[str]:
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        return list(lex)
    except ValueError:
        return command.split()


def _segments(command: str) -> list[list[str]]:
    segs, cur = [], []
    for tok in _tokens(command):
        if tok in CONTROL:
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        segs.append(cur)
    return segs


def _written_paths(seg: list[str]) -> list[str]:
    """Paths a shell segment writes or deletes (best effort)."""
    out = [seg[i + 1] for i, t in enumerate(seg[:-1]) if t in REDIRECTS]
    args = [t for t in seg if t not in REDIRECTS]
    if not args:
        return out
    cmd = Path(args[0]).name
    files = [a for a in args[1:] if not a.startswith("-")]
    if cmd in ("tee", "rm", "mv", "truncate", "shred", "unlink"):
        out += files
    elif cmd in ("sed", "perl") and any(a.startswith("-i") or a == "--in-place" for a in args[1:]):
        out += files[1:] if cmd == "sed" and not any(a in ("-e", "-f") for a in args) else files
    elif cmd in ("cp", "install", "ln") and files:
        out.append(files[-1])
    elif cmd == "git" and len(args) > 1 and args[1] in ("checkout", "restore", "rm", "mv"):
        out += [a for a in args[2:] if not a.startswith("-") and a != "--"]
    return out


def _secret_file(path: Path) -> bool:
    name = path.name
    if name.startswith(".env") and name != ".env.example":
        return True
    if path.is_symlink() or not path.is_file():
        return False
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return False
    return any(r.search(text) for r in SECRET_RES)


def _status(cwd: Path) -> list[tuple[str, str]]:
    out = _git(cwd, "-c", "core.quotePath=false", "status", "--porcelain", "-z", "--untracked-files=all").stdout
    return [(e[:2], e[3:]) for e in out.split("\0") if len(e) > 3]


def _staging_candidates(seg: list[str], cwd: Path) -> list[str]:
    sub = seg[1] if len(seg) > 1 else ""
    opts = [a for a in seg[2:] if a.startswith("-")]
    paths = [a for a in seg[2:] if not a.startswith("-") and a != "--"]
    status = _status(cwd)
    if sub == "commit":
        if not any(o in ("-a", "--all") or (o.startswith("-") and not o.startswith("--") and "a" in o)
                   for o in opts):
            return []
        return [p for code, p in status if code != "??"]
    if sub != "add":
        return []
    if any(o in ("-A", "--all", "-u", "--update") for o in opts) or "." in paths or not paths:
        return [p for code, p in status if not (code == "??" and "-u" in opts)]
    top = Path(_git(cwd, "rev-parse", "--show-toplevel").stdout.strip() or cwd)
    rels = [str((cwd / p).resolve().relative_to(top.resolve())) if (cwd / p).resolve().is_relative_to(top.resolve())
            else p for p in paths]
    return [p for _, p in status if any(p == r or p.startswith(r.rstrip("/") + "/") for r in rels)]


def _push_problem(seg: list[str], cwd: Path, contest: Path) -> str | None:
    args = seg[2:]
    force = any(a in ("-f", "--force") or a.startswith("--force-with-lease") or a.startswith("--force-if")
                for a in args)
    positional = [a for a in args if not a.startswith("-")]
    allow = specmod.get(specmod.load_spec(contest / ".house"), "guard.push_allow") or ["origin"]
    remote = positional[0] if positional else "origin"
    if remote not in allow:
        return f"push to `{remote}` is not allowed (spec guard.push_allow: {', '.join(map(str, allow))})"
    main = _git(contest, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    refs = positional[1:] or [_git(cwd, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()]
    for ref in refs:
        dst = ref.lstrip("+").split(":")[-1].removeprefix("refs/heads/")
        if dst == main and (force or ref.startswith("+")):
            return f"force push to the contest's main branch `{main}` is not allowed"
    return None


def check_bash(command: str, cwd: Path, contest: Path) -> str | None:
    protected = protected_paths(contest)
    for seg in _segments(command):
        for target in _written_paths(seg):
            rel = repo_relative(target, cwd, contest)
            if rel and (rel in protected or rel.startswith(".house/checks")):
                return check_edit(rel, contest)
        if seg[:1] == ["git"] and len(seg) > 1:
            if seg[1] in ("add", "commit"):
                bad = [p for p in _staging_candidates(seg, cwd) if _secret_file(cwd / p)]
                if bad:
                    return "would commit secrets: " + ", ".join(bad) + " (keep keys in an ignored .env)"
            if seg[1] == "push":
                problem = _push_problem(seg, cwd, contest)
                if problem:
                    return problem
    return None


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _at_head(repo: Path, rel: str) -> str | None:
    r = _git(repo, "show", f"HEAD:{rel}")
    return r.stdout if r.returncode == 0 else None


def frozen_tampering(contest: Path) -> list[str]:
    """Changes only house may make, found uncommitted in the contest checkout (house commits them at once)."""
    house = contest / ".house"
    found = []
    for path in sorted((house / "checks").glob("*.json")):
        rel = str(path.relative_to(contest))
        head = _at_head(contest, rel)
        try:
            now = json.loads(path.read_text())["frozen"]
            before = json.loads(head)["frozen"] if head else None
        except (ValueError, KeyError, TypeError):
            found.append(f"{rel} is unreadable")
            continue
        if before != now:
            found.append(f"frozen criteria in {rel} changed outside `house check --freeze/--refreeze`")
    for path in sorted((house / "tasks").glob("T*-*.md")):
        rel = str(path.relative_to(contest))
        try:
            now = parse_frontmatter(path.read_text())[0].get("status")
            head = _at_head(contest, rel)
            before = parse_frontmatter(head)[0].get("status") if head else None
        except ValueError:
            continue
        if now in FINISHED and before != now:
            found.append(f"{path.name.split('-', 1)[0]} set to `{now}` by hand (only house merge sets it)")
    return found


def diff_violations(contest: Path, task_id: str, worktree: Path) -> list[str]:
    """What a task branch (committed + uncommitted) changed that it must not, plus tampering in the checkout."""
    main_head = _git(contest, "rev-parse", "HEAD").stdout.strip()
    base = _git(worktree, "merge-base", main_head, "HEAD").stdout.strip() or main_head
    changed = set(filter(None, _git(worktree, "diff", "--name-only", base, "HEAD").stdout.splitlines()))
    changed |= {p for _, p in _status(worktree)}
    found = []
    for rel, owner in protected_paths(contest).items():
        if rel in changed:
            pinned = (_checks(contest).get(owner, {}).get("frozen", {}).get("protected") or {}).get(rel)
            if owner == "local_scorer" or _sha(worktree / rel) != pinned:
                found.append(f"{rel} is protected ({owner}) and was changed on the branch")
    for rel in sorted(changed):
        if re.match(r"^\.house(/|$)", rel):
            found.append(f"{rel}: task branches must not carry .house/ files (write via $HOUSE_DIR)")
        elif _secret_file(worktree / rel):
            found.append(f"{rel} looks like a secret")
    return found + frozen_tampering(contest)
