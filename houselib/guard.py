"""Rules enforced by code rather than prompts (spec §6).

Used by the Claude Code hook (`bin/house-hook`, before a tool runs), by `house guard diff` and by `house merge`
(after the fact, on a task branch — this also covers Codex, which has no hooks).

What an agent should know:
  * Frozen acceptance criteria (`.house/checks/*.json`) are written only by `house check`; the acceptance tests
    and the local scorer pinned in them (`protected_paths`) must not be edited by workers.
  * The attempt register `.house/attempts.jsonl` is written only by house itself (`house night`, `house merge`);
    workers must not edit it.
  * Task branches must not carry `.house/` files (write through `$HOUSE_DIR` instead) or anything that looks like
    a secret; `git add` / `git commit -a` of a secret and pushes to a remote outside `guard.push_allow` (default
    `origin`) or forced pushes to the contest's main branch are refused.
  * Pre-tool checks (`check_edit`, `check_bash`) return a human-readable reason to refuse, or None to allow. They
    never raise on odd input and are best effort: shell parsing is heuristic, so the after-the-fact check
    (`diff_violations`, `frozen_tampering`) is the backstop that `house merge` relies on.
  * Paths in this module are strings relative to a checkout root (the contest checkout or one of its task
    worktrees) unless a parameter says otherwise.
"""
import ast
import hashlib
import json
import os
import re
import shlex
import subprocess
from pathlib import Path

from . import spec as specmod
from .export import SECRET_RES
from .fm import parse_frontmatter

CONTROL = {"&&", "||", ";", "|", "&", ";;", "|&"}
REDIRECTS = {">", ">>", ">|", "&>", "&>>", "1>", "2>", ">&", "<>"}  # operators whose target is written (an fd
# target such as `2>&1` or `>&-` is skipped)
_NAMED_FD = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")  # bash `{fd}>file`: the shell picks the fd, names it fd
REDIRECT_OPS = REDIRECTS | {"<", "<<", "<<<", "<&", ">&", "<>"}  # every redirection operator (each takes one word)
SHELLS = {"sh", "bash", "dash", "zsh", "ksh"}  # `<shell> -c SCRIPT` is checked as SCRIPT too
FINISHED = ("merged", "done")


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Run `git *args` in `cwd`, capturing text output; never raises on a non-zero exit."""
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def contest_root(cwd: Path) -> Path | None:
    """Find the main checkout (the one holding `.house/`) that `cwd` belongs to, also from a task worktree anywhere.

    Args:
        cwd: Any directory (or file path) inside the contest checkout or inside one of its git worktrees,
            including worktrees that live outside the contest directory. Relative paths are resolved.

    Returns:
        Absolute path of the contest checkout, or None when `cwd` is not part of a house contest.
    """
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
    """Express `path` relative to the checkout it lives in (the contest checkout or one of its worktrees).

    Args:
        path: File path as a tool received it: absolute, or relative to `cwd`. It need not exist.
        cwd: Directory that a relative `path` is resolved against.
        contest: Contest checkout root, as returned by `contest_root`.

    Returns:
        Path relative to the contest checkout, or to the task worktree under `<contest>/.worktrees/<name>/` (or an
        external worktree of the same repo) that contains it. None when the path is outside the contest or is a
        worktree directory itself.
    """
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
    """Load every readable `.house/checks/*.json` as {task id: data}; unparsable files are skipped."""
    out = {}
    for path in sorted((contest / ".house/checks").glob("*.json")):
        try:
            out[path.stem] = json.loads(path.read_text())
        except ValueError:
            continue
    return out


def _tracked(contest: Path, rels: list[str]) -> set[str]:
    """Return which of the given contest-relative paths are tracked by git (one `git ls-files` call)."""
    if not rels:
        return set()
    r = _git(contest, "ls-files", "-z", "--", *rels)
    return set(r.stdout.split("\0")) - {""} if r.returncode == 0 else set()


def _local_imports(contest: Path, rel: str) -> list[str]:
    """Contest-relative module files a Python file imports from its own directory (`import m`, `from m import x`,
    `from pkg.mod import x` → `m.py`, `pkg/mod.py` or `pkg/__init__.py`), whether or not they exist."""
    try:
        tree = ast.parse((contest / rel).read_text(errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return []
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.append(node.module)
        elif isinstance(node, ast.ImportFrom) and node.level == 1:
            names += [f"{node.module}.{a.name}" if node.module else a.name for a in node.names]
    base = Path(rel).parent
    out = []
    for name in names:
        parts = name.split(".")
        for i in range(1, len(parts) + 1):
            stem = base.joinpath(*parts[:i])
            out += [f"{stem.as_posix()}.py", (stem / "__init__.py").as_posix()]
    return out


def scorer_files(contest: Path) -> list[str]:
    """List the files the local scorer consists of (protected by the hook, fingerprinted by `house exp`).

    Only files tracked by git count, so data and output paths named in the command (untracked predictions, data
    shared from outside git) never do. The set is: tracked files named in `scoring.local_scorer`; for Python
    files, the local modules they import, recursively; and every tracked file matching `scoring.scorer_files`
    (glob patterns relative to the contest; use it for shell scorers or scorers run from a subdirectory).

    Args:
        contest: Contest checkout root.

    Returns:
        Sorted contest-relative paths; [] when the spec names no scorer or nothing matches.
    """
    spec = specmod.load_spec(contest / ".house")
    scorer = specmod.get(spec, "scoring.local_scorer")
    candidates: list[str] = []
    if isinstance(scorer, str):
        try:
            tokens = shlex.split(scorer)
        except ValueError:
            tokens = scorer.split()
        candidates = [os.path.normpath(t) for t in tokens if ("/" in t or "." in t) and not t.startswith("-")]
    patterns = specmod.get(spec, "scoring.scorer_files")
    for pattern in patterns if isinstance(patterns, list) else []:
        pattern = str(pattern).strip()
        if pattern and not pattern.startswith("/") and ".." not in Path(pattern).parts:
            candidates += [p.relative_to(contest).as_posix() for p in contest.glob(pattern) if p.is_file()]
    candidates = [c for c in candidates if not c.startswith("..") and not os.path.isabs(c)]
    found = _tracked(contest, candidates)
    todo = [f for f in found if f.endswith(".py")]
    while todo:
        new = [m for m in _tracked(contest, _local_imports(contest, todo.pop())) if m not in found]
        found.update(new)
        todo += new
    return sorted(found)


def protected_paths(contest: Path) -> dict[str, str]:
    """Map each protected file to the owner that pinned it.

    Protected files are the acceptance tests frozen by `house check` (their hash is recorded in
    `.house/checks/<task>.json` under `frozen.protected`) and the files of the spec's `scoring.local_scorer`.

    Args:
        contest: Contest checkout root.

    Returns:
        Dict {path relative to the checkout: owner}, where owner is a task id (e.g. "T001") or "local_scorer".
        Only files whose content is pinned (hash known) are included.
    """
    out = {}
    for tid, data in _checks(contest).items():
        for rel, sha in (data.get("frozen", {}).get("protected") or {}).items():
            if sha:
                out[rel] = tid
    for rel in scorer_files(contest):
        out.setdefault(rel, "local_scorer")
    return out


def _in_checks(rel: str) -> bool:
    """Tell whether `rel` is `.house/checks` or lies below it."""
    return rel == ".house/checks" or rel.startswith(".house/checks/")


REGISTER = ".house/attempts.jsonl"
REGISTER_REASON = f"{REGISTER} is written only by house (the attempt register of `house night` / `house merge`)"


def _git_internal(rel: str) -> bool:
    """Tell whether `rel` is a file that can switch the git hooks off: `.git/hooks/**`, `.git/config` or a
    `config.worktree` (where `core.hooksPath` can be set per worktree)."""
    return rel == ".git/config" or rel.startswith(".git/hooks/") or rel.endswith("/config.worktree")


HOOKS_REASON = ("git hooks (pre-commit, pre-push) enforce the guard rules inside task worktrees; "
                "`--no-verify`, `core.hooksPath` and edits of `.git/hooks` or `.git/config` are for the human")
HOOKSPATH_RE = re.compile(r"core\.hookspath", re.IGNORECASE)
CONFIG_READ_RE = re.compile(r"--get\b|--get-all\b|--get-regexp\b|--list\b")


def pinned_sha(contest: Path, owner: str, rel: str) -> str | None:
    """The SHA-256 that `house check --freeze` pinned for protected file `rel` of task `owner` (None: not pinned)."""
    return (_checks(contest).get(owner, {}).get("frozen", {}).get("protected") or {}).get(rel)


def protected_violation(contest: Path, rel: str, content: bytes | None, protected: dict[str, str] | None = None) -> str | None:
    """Say why changing the protected file `rel` to `content` (None = deleted) is refused, else None.

    Same rule as `diff_violations`: any change to the local scorer counts; a frozen acceptance test may be
    changed only back to its pinned content.

    Args:
        contest: Contest checkout root.
        rel: Path relative to the checkout.
        content: New content of the file, or None when it is deleted.
        protected: Result of `protected_paths(contest)` when the caller already has it.
    """
    owner = (protected if protected is not None else protected_paths(contest)).get(rel)
    if not owner:
        return None
    if owner != "local_scorer" and content is not None \
            and hashlib.sha256(content).hexdigest() == pinned_sha(contest, owner, rel):
        return None
    return f"{rel} is protected ({owner}) and was changed"


def check_edit(rel: str, contest: Path) -> str | None:
    """Decide whether a worker may write the file `rel`.

    Refuses `.house/checks/**` (written only by `house check`), `.house/attempts.jsonl` (the attempt register,
    written only by house), every file in `protected_paths`, and the files that
    configure git hooks (`.git/hooks/**`, `.git/config`).

    Args:
        rel: File path relative to its checkout, as returned by `repo_relative`.
        contest: Contest checkout root.

    Returns:
        A reason string to show the agent (it is part of the hook's stderr message), or None when allowed.
    """
    if _in_checks(rel):
        return f"{rel} is written only by `house check` (frozen acceptance criteria)"
    if rel == REGISTER:
        return REGISTER_REASON
    if _git_internal(rel):
        return f"{rel}: {HOOKS_REASON}"
    owner = protected_paths(contest).get(rel)
    if owner:
        return f"{rel} is protected ({owner}); acceptance tests and the scorer must not be changed by workers"
    return None


_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1[^\n]*\n.*?\n\s*\2[ \t]*(?=\n|$)", re.DOTALL)


def _normalize(command: str) -> str:
    """Drop heredoc bodies (data, not commands) and turn unquoted newlines into `;`."""
    command = _HEREDOC_RE.sub(lambda m: m.group(0).split("\n", 1)[0], command)
    out, quote, escaped = [], None, False
    for ch in command:
        if escaped:
            escaped = False
        elif ch == "\\" and quote != "'":
            escaped = True
        elif quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "\n":
            out.append(" ; ")
            continue
        out.append(ch)
    return "".join(out)


# one shell operator; shlex returns a run of operator characters (`);`, `)&&`) as one token, which is split by this
_OPERATOR = re.compile(r"&>>|&>|>>|>&|>\||<<<|<<|<&|<>|&&|\|\||\|&|;;|[;()&|<>]")


def _tokens(command: str) -> list[str]:
    """Split a shell command into tokens: words and single operators (`&&`, `;`, `(`, `)`, `>>`, ... each a token of
    its own, also when written together as in `(true);rm x`); naive split on error."""
    lex = shlex.shlex(_normalize(command), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        raw = list(lex)
    except ValueError:
        return command.split()
    out: list[str] = []
    for tok in raw:
        if tok and all(ch in "();&|<>" for ch in tok):
            out += _OPERATOR.findall(tok)
        else:
            out.append(tok)
    return out


def _segments(command: str) -> list[list[str]]:
    """Split a shell command into simple commands at `&&`, `||`, `;`, `|`, `&`, dropping grouping tokens."""
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
    return [s for s in ([t for t in seg if t not in ("(", ")", "{", "}", "!")] for seg in segs) if s]


def _split_redirects(seg: list[str]) -> tuple[list[str], list[str]]:
    """Separate a simple command's words from its redirections.

    The tokenizer splits `2>/dev/null` into `2`, `>`, `/dev/null`, so a number (or a bash named fd such as `{fd}`)
    right before an operator is taken as its file descriptor and dropped (`echo 2 >x` loses the word `2`, which no
    check needs).

    Args:
        seg: Tokens of one simple command (see `_segments`).

    Returns:
        `(words, redirections)`: the words in order, and each redirection as its operator followed by its target.
    """
    words: list[str] = []
    redirs: list[str] = []
    i = 0
    while i < len(seg):
        if seg[i] in REDIRECT_OPS:
            redirs += seg[i:i + 2]
            i += 2
        elif (seg[i].isdigit() or _NAMED_FD.fullmatch(seg[i])) and i + 1 < len(seg) and seg[i + 1] in REDIRECT_OPS:
            i += 1
        else:
            words.append(seg[i])
            i += 1
    return words, redirs


def _git_parts(seg: list[str], cwd: Path) -> tuple[list[str], Path]:
    """`git [-C dir] [-c k=v] [--no-pager] sub args…` → ([sub, args…], effective cwd)."""
    args, i = seg[1:], 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] == "-C" and i + 1 < len(args):
            cwd = (cwd / args[i + 1]).resolve()
            i += 2
        elif args[i] == "-c" and i + 1 < len(args):
            i += 2
        else:
            i += 1
    return args[i:], cwd


def _written_paths(seg: list[str], cwd: Path) -> list[tuple[str, Path]]:
    """(path, cwd it is relative to) for paths a shell segment writes or deletes (best effort).

    Redirections count wherever they stand; the program is found after `VAR=x` words and wrappers
    (`timeout 5 rm x` deletes `x`).
    """
    words, redirs = _split_redirects(seg)
    out = [(redirs[i + 1], cwd) for i in range(0, len(redirs) - 1, 2)
           if redirs[i] in REDIRECTS and not (redirs[i + 1].isdigit() or redirs[i + 1] == "-")]
    args = words[_command_start(words):]
    if not args:
        return out
    cmd = Path(args[0]).name
    files = [a for a in args[1:] if not a.startswith("-")]
    if cmd in ("tee", "rm", "mv", "truncate", "shred", "unlink"):
        out += [(f, cwd) for f in files]
    elif cmd in ("sed", "perl") and any(a.startswith("-i") or a == "--in-place" for a in args[1:]):
        writes = files[1:] if cmd == "sed" and not any(a in ("-e", "-f") for a in args) else files
        out += [(f, cwd) for f in writes]
    elif cmd in ("cp", "install", "ln") and files:
        out.append((files[-1], cwd))
    elif cmd == "git":
        sub, gcwd = _git_parts(args, cwd)
        rest = sub[1:]
        if sub[:1] in (["rm"], ["mv"]):
            out += [(a, gcwd) for a in rest if not a.startswith("-") and a != "--"]
        elif sub[:1] == ["checkout"] and "--" in rest:
            source = [a for a in rest[:rest.index("--")] if not a.startswith("-")]
            if source and source[0] != "HEAD":  # restoring from HEAD is how a worker undoes its own change
                out += [(a, gcwd) for a in rest[rest.index("--") + 1:]]
        elif sub[:1] == ["restore"]:
            src = next((a.split("=", 1)[1] for a in rest if a.startswith("--source=")), None)
            if src is None and "-s" in rest and rest.index("-s") + 1 < len(rest):
                src = rest[rest.index("-s") + 1]
            if src and src != "HEAD":
                out += [(a, gcwd) for a in rest if not a.startswith("-") and a != src]
    return out


MAX_SCAN_BYTES = 1_000_000


def looks_secret(name: str, data: bytes) -> bool:
    """Tell whether a file with this base name and content looks like a secret (`.env*` except `.env.example`, or a
    key/token pattern in the first `MAX_SCAN_BYTES`; binary data never matches by content)."""
    if name.startswith(".env") and name != ".env.example":
        return True
    head = data[:MAX_SCAN_BYTES]
    if b"\0" in head[:8192]:
        return False  # binary (data, model weights)
    text = head.decode(errors="ignore")
    return any(r.search(text) for r in SECRET_RES)


def _secret_file(path: Path) -> bool:
    """Tell whether `path` is a regular text file that looks like a secret (`.env*` or a key/token pattern)."""
    if path.is_symlink() or not path.is_file():
        return False  # deleting a secret is fine; links and dirs carry no content of their own
    name = path.name
    if name.startswith(".env") and name != ".env.example":
        return True
    try:
        with path.open("rb") as f:
            head = f.read(MAX_SCAN_BYTES)
    except OSError:
        return False
    return looks_secret(name, head)


# Shapes `redact` masks on top of `export.SECRET_RES` (which stays as it is, so `house export` blocking does not
# change): a whole PEM private key block, the value of an environment assignment whose name says it is a secret, a
# bearer or basic authorization, an API-key header, the secret values of a URL query and the password of a URL.
_PEM_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.DOTALL)
# The name is at most 64 characters and starts at an identifier boundary, so a search costs O(64) per position and
# never rescans a long run of name characters; the secret test is done on its `_` segments in `_redact_env`.
_ENV_NAME_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Z0-9_]{1,64})=")
# A shell word is a run of adjacent fragments with no whitespace between them: "..." (with \" escapes), '...', $'...'
# (with \' escapes), a command substitution $( ... ) (parentheses balanced up to three levels deep, quotes and
# backticks inside it respected) or `...`, and unquoted chunks of non-separator characters or backslash escapes. A
# word that opens with a quote that never closes runs to the end of the text; an unterminated quote later in a word is
# left out (it is usually the closing quote of an enclosing string, as in `echo "KEY=1"`). A `$(` or backtick that
# never closes (or nests deeper) runs to the end of the text. Possessive quantifiers keep it linear.
_SUBST_ATOM = r"""[^()'"`\\]|\\[\s\S]|'[^']*+'|"(?:[^"\\]|\\[\s\S])*+"|`[^`]*+`"""
_PARENS = rf"""\((?:{_SUBST_ATOM})*+\)"""
for _ in range(2):
    _PARENS = rf"""\((?:{_SUBST_ATOM}|{_PARENS})*+\)"""
_ENV_VALUE_RE = re.compile(
    rf"""(?:"(?:[^"\\]|\\[\s\S])*+"|'[^']*+'|\$'(?:[^'\\]|\\[\s\S])*+'|\${_PARENS}|`[^`]*+`"""
    r"""|\$\([\s\S]*+\Z|`[\s\S]*+\Z|(?:[^\s;'"&|)\\$`]|\$(?![('])|\\[\s\S])++)++"""
    r"""|"(?:[^"\\]|\\[\s\S])*+\Z|'[^']*+\Z"""
)
_SECRET_SEGMENTS = frozenset({"KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "PASS", "PAT", "APIKEY"})
_BEARER_RE = re.compile(r"(?i)bearer\s+\S{16,}")
_BASIC_RE = re.compile(r"(?i)(authorization\s*:\s*)basic\s+[A-Za-z0-9+/=_-]{8,}")
_API_KEY_HEADER_RE = re.compile(r"(?i)(\bx-api-key\s*:\s*)[^\s'\"]+")
_QUERY_SECRET_RE = re.compile(r"(?i)([?&](?:token|access_token|refresh_token|api_key|apikey|key|password|secret"
                              r"|client_secret|sig|signature)=)[^&#\s'\"]+")
_URL_CRED_RE = re.compile(r"(://[^/\s:@]+:)[^/\s?#]*@")


def _redact_env(text: str) -> str:
    """Mask the value of every `NAME=value` whose NAME has a secret `_` segment, keeping the name.

    Args:
        text: Any text.

    Returns:
        The text with `NAME=` followed by `[REDACTED]` in place of each such assignment. Linear in the text: the
        value is looked at only after the name matched, and the scan then resumes after the value.
    """
    out, pos, scan = [], 0, 0
    while m := _ENV_NAME_RE.search(text, scan):
        scan = m.end()
        if _SECRET_SEGMENTS.isdisjoint(m.group(1).split("_")):
            continue  # not a secret name: look on from after the `=` (a secret may follow, as in `A=B_KEY=x`)
        value = _ENV_VALUE_RE.match(text, m.end())
        if value is None:
            continue
        out += [text[pos:m.end()], "[REDACTED]"]
        pos = scan = value.end()
    return "".join(out) + text[pos:]


def redact(text: str) -> str:
    """Replace every secret-looking token in `text` with `[REDACTED]`.

    Used before a session record is printed or written, so a Bash command that carries a key never leaves the
    reader in clear text. Text without any match comes back unchanged.

    Args:
        text: Any text, for example a shell command line.

    Returns:
        The text with each match of each `export.SECRET_RES` pattern replaced by `[REDACTED]`, plus (redact only) a
        whole PEM private key block (to its END line, or to the end of the text when truncated); the value of a
        `NAME=value` assignment (the whole shell word: adjacent quoted and unquoted fragments up to whitespace, `;`,
        `&`, `|` or `)`, with a balanced `$( ... )` or `` `...` `` inside it; one that never closes runs to the end of
        the text) whose
        uppercase NAME (up to 64 characters) has an underscore-separated segment KEY, TOKEN, SECRET, PASSWORD,
        PASSWD, PASS, PAT or APIKEY (`GITHUB_TOKEN=[REDACTED]`, the name stays; `MONKEY` and `TOKENIZERS_PARALLELISM`
        are left alone); `Bearer <16+ characters>` (becomes `Bearer [REDACTED]`); `Authorization: Basic <base64>`
        (becomes `Basic [REDACTED]`); the value of an `X-Api-Key:` header; the value of a URL query parameter
        `token`, `access_token`, `refresh_token`, `api_key`, `apikey`, `key`, `password`, `secret`,
        `client_secret`, `sig` or `signature`; and the password in
        `://user:password@` (becomes `://user:[REDACTED]@`; the password ends at `/`, `?`, `#` or whitespace).
    """
    text = _PEM_RE.sub("[REDACTED]", text)
    for rx in SECRET_RES:
        text = rx.sub("[REDACTED]", text)
    text = _redact_env(text)
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _BASIC_RE.sub(r"\1Basic [REDACTED]", text)
    text = _API_KEY_HEADER_RE.sub(r"\1[REDACTED]", text)
    text = _QUERY_SECRET_RE.sub(r"\1[REDACTED]", text)
    return _URL_CRED_RE.sub(r"\1[REDACTED]@", text)


def _status(cwd: Path) -> list[tuple[str, str]]:
    """Parse `git status --porcelain -z` in `cwd` into [(two-letter XY code, path)]; renames give the new path."""
    out = _git(cwd, "-c", "core.quotePath=false", "status", "--porcelain", "-z", "--untracked-files=all").stdout
    entries, result, skip = out.split("\0"), [], False
    for e in entries:
        if skip:  # the source path of a rename/copy entry
            skip = False
            continue
        if len(e) > 3:
            result.append((e[:2], e[3:]))
            skip = e[0] in "RC"
    return result


def _staging_candidates(sub: list[str], cwd: Path) -> list[str]:
    """List changed paths that `git add ...` / `git commit -a` (given as `sub` = [name, args…]) would stage."""
    name = sub[0] if sub else ""
    opts = [a for a in sub[1:] if a.startswith("-")]
    paths = [a for a in sub[1:] if not a.startswith("-") and a != "--"]
    status = _status(cwd)
    if name == "commit":
        if not any(o in ("-a", "--all") or (o.startswith("-") and not o.startswith("--") and "a" in o)
                   for o in opts):
            return []
        return [p for code, p in status if code != "??"]
    if name != "add":
        return []
    if any(o in ("-A", "--all", "-u", "--update") for o in opts) or "." in paths or not paths:
        return [p for code, p in status if not (code == "??" and ("-u" in opts or "--update" in opts))]
    top = Path(_git(cwd, "rev-parse", "--show-toplevel").stdout.strip() or cwd).resolve()
    rels = [str((cwd / p).resolve().relative_to(top)) if (cwd / p).resolve().is_relative_to(top) else p
            for p in paths]
    return [p for _, p in status if any(p == r or p.startswith(r.rstrip("/") + "/") for r in rels)]


def _push_remote(sub: list[str], cwd: Path) -> str:
    """Name the remote a `git push` (`sub` = ["push", args…]) targets; falls back to git config, then `origin`."""
    for a in sub[1:]:
        if a.startswith("--repo="):
            return a.split("=", 1)[1]
    positional = [a for a in sub[1:] if not a.startswith("-")]
    if positional:
        return positional[0]
    r = _git(cwd, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{push}")
    if r.returncode == 0 and "/" in r.stdout:
        return r.stdout.strip().split("/", 1)[0]
    branch = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    for key in (f"branch.{branch}.pushRemote", "remote.pushDefault", f"branch.{branch}.remote"):
        v = _git(cwd, "config", "--get", key).stdout.strip()
        if v:
            return v
    return "origin"


def _push_problem(sub: list[str], cwd: Path, contest: Path) -> str | None:
    """Return why a `git push` is refused (remote not in `guard.push_allow`, force push to main), else None."""
    args = sub[1:]
    force = any(a in ("--force",) or a.startswith("--force-with-lease") or a.startswith("--force-if")
                or (a.startswith("-") and not a.startswith("--") and "f" in a) for a in args)
    allow = specmod.get(specmod.load_spec(contest / ".house"), "guard.push_allow") or ["origin"]
    remote = _push_remote(sub, cwd)
    if remote not in allow:
        return f"push to `{remote}` is not allowed (spec guard.push_allow: {', '.join(map(str, allow))})"
    main = _git(contest, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    positional = [a for a in args if not a.startswith("-")]
    refs = positional[1:] or [_git(cwd, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()]
    for ref in refs:
        dst = ref.lstrip("+").split(":")[-1].removeprefix("refs/heads/")
        if dst == main and (force or ref.startswith("+")):
            return f"force push to the contest's main branch `{main}` is not allowed"
    return None


def check_bash(command: str, cwd: Path, contest: Path) -> str | None:
    """Decide whether a Bash tool command may run; the first violation found wins.

    The command is split into simple commands (heredoc bodies are ignored, newlines count as `;`). Refuses:
      * writes or deletes of `.house/checks/**`, `.house/attempts.jsonl` or protected files (see `check_edit`) through redirections
        (`>`, `>>`, `&>` …), `tee`, `rm`, `mv`, `truncate`, `shred`, `unlink`, `sed -i` / `perl -i`, the destination
        of `cp` / `install` / `ln`, `git rm` / `git mv`, and `git checkout <rev> --` / `git restore --source`
        from anything but HEAD (restoring from HEAD is allowed);
      * `git add` / `git commit -a` that would stage a file that looks like a secret (`.env*` except
        `.env.example`, or a key/token pattern);
      * `git push` to a remote not listed in spec `guard.push_allow` (default `["origin"]`) or a force push
        (`--force`, `--force-with-lease`, `-f`, `+ref`) to the contest's main branch;
      * switching the git hooks off: `--no-verify` (also `git commit -n`), any mention of `core.hooksPath` other than
        a read (`--get`, `--list`), writes to `.git/hooks/**` or `.git/config`. The hooks themselves (`githook.py`)
        are the real enforcement of the secret, push and protected-file rules inside task worktrees; this check
        only keeps the agent from removing them.
    `house out [--] CMD` and `house exp … -- CMD` are checked as CMD itself (`house out show|prune` and `house exp`
    without `--` are not wrappers; a single CMD word is checked as a shell script), also with redirections
    between its words; `sh|bash -c SCRIPT` is checked as SCRIPT too. The program of a simple command is found after
    `VAR=x` words and wrappers (`timeout 60 git push --no-verify` is refused like `git push --no-verify`).
    Best effort: it understands `git -C dir` and `-c k=v`, but not variables, subshell expansion or script files.

    Args:
        command: The shell command line exactly as the Bash tool received it.
        cwd: Directory the command runs in; relative paths and `git -C` are resolved against it.
        contest: Contest checkout root, as returned by `contest_root`.

    Returns:
        A reason string shown to the agent, or None when the command is allowed.
    """
    protected = protected_paths(contest)
    if HOOKSPATH_RE.search(_normalize(command)) and not CONFIG_READ_RE.search(command):
        return HOOKS_REASON
    for seg in _commands(command):
        for target, base in _written_paths(seg, cwd):
            rel = repo_relative(target, base, contest)
            if rel and (rel in protected or _in_checks(rel) or rel == REGISTER or _git_internal(rel)):
                reason = check_edit(rel, contest)
                if reason:
                    return reason
        words, _ = _split_redirects(seg)
        words = words[_command_start(words):]
        if words and Path(words[0]).name == "git":
            sub, gcwd = _git_parts(words, cwd)
            if "--no-verify" in sub or (sub[:1] == ["commit"] and any(
                    a.startswith("-") and not a.startswith("--") and "n" in a for a in sub[1:])):
                return HOOKS_REASON
            if sub[:1] in (["add"], ["commit"]):
                bad = [p for p in _staging_candidates(sub, gcwd) if _secret_file(gcwd / p)]
                if bad:
                    return "would commit secrets: " + ", ".join(bad) + " (keep keys in an ignored .env)"
            if sub[:1] == ["push"]:
                problem = _push_problem(sub, gcwd, contest)
                if problem:
                    return problem
    return None


SIGNAL_CLI_DIR = ".local/share/signal-cli"
HOUSE_CONFIG_DIR = ".config/house"
SIGNAL_CLI_REASON = ("running `signal-cli` directly bypasses the Signal store (received messages would be lost, sent "
                     "ones missing from the history, private chats exposed); use `house signal send|pull|read|groups`")
HUMAN_REASON = ("adding or removing a Signal group is the human's decision: ask Wojtek to run "
                "`house signal allow|deny …` in his own terminal")
ENV_REASON = ("HOUSE_* path overrides (HOUSE_SIGNAL_CONFIG, HOUSE_NOTIFY_CONFIG, HOUSE_DATA_DIR, "
              "HOUSE_SIGNAL_ATTACHMENTS) are for tests only")
DATA_REASON = "house data (~/.local/share/house: the Signal store, caches, cursors) is written only by house commands"
HOUSE_DATA_DIR = ".local/share/house"
HOUSE_ENV_RE = re.compile(r"\bHOUSE_(SIGNAL_CONFIG|NOTIFY_CONFIG|DATA_DIR|SIGNAL_ATTACHMENTS)=")
ALLOW_RE = re.compile(r"house\S*\s+signal\s+(allow|deny|agent\s+(on|members|token))\b")
HUMAN_WORDS = {"allow", "deny"}
HUMAN_AGENT_WORDS = {"on", "members", "token"}
INNER_SIGNAL_CLI_RE = re.compile(r"(^|[;&|(`]|\$\()\s*(\S*/)?signal-cli(\s|$)")
WRAPPERS = {"timeout", "env", "nice", "nohup", "sudo", "stdbuf", "exec", "command", "unbuffer", "script", "xargs",
            "time", "setsid"}
SIGNAL_FILES_REASON = ("signal-cli's files mix private chats with groups; agents read only the copies from allowed "
                       "chats in ~/.local/share/house/signal/files/ (`house signal read` shows their paths)")
CONFIG_REASON = ("house configuration (~/.config/house) is changed only by the human; groups: `house signal "
                 "allow/deny` in a terminal")
READ_TOOLS = {"Read", "Grep", "Glob"}


def _expand(path: str, base: Path, home: Path) -> Path:
    """Resolve a tool or shell path: `~`, `$HOME` and `${HOME}` become `home`; relative paths join `base`."""
    for prefix in ("~", "$HOME", "${HOME}"):
        if path == prefix or path.startswith(prefix + "/"):
            path = str(home) + path[len(prefix):]
            break
    p = Path(path)
    return (p if p.is_absolute() else base / p).resolve()


def _under(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def check_signal(tool: str, args: dict, cwd: Path, home: Path | None = None) -> str | None:
    """Signal rules for agents, applied wherever the hook runs (contests and the house repo).

    Refuses: running `signal-cli` at all (use `house signal …`, which logs, stores and filters private chats);
    `house signal allow|deny` (only the human widens the allowed set, in their own terminal); `HOUSE_*` path
    overrides (test-only); any tool touching signal-cli's data dir (`~/.local/share/signal-cli`, where files from
    private chats land); writes to `~/.config/house/` and `~/.local/share/house/` (Edit-type tools, or shell
    writes as `check_bash` understands them). Best effort, like `check_bash`.

    Args:
        tool: Claude Code tool name (Bash, Read, Grep, Glob, Edit, Write, MultiEdit, NotebookEdit).
        args: The tool input (`command`, `file_path`, `notebook_path` or `path`).
        cwd: Directory relative paths are resolved against.
        home: Home directory. Defaults to `Path.home()`.

    Returns:
        A reason to refuse, or None.
    """
    home = (home or Path.home()).resolve()
    cli_dir, config_dir, data_dir = home / SIGNAL_CLI_DIR, home / HOUSE_CONFIG_DIR, home / HOUSE_DATA_DIR
    if tool == "Bash":
        command = str(args.get("command") or "")
        if SIGNAL_CLI_DIR in command:
            return SIGNAL_FILES_REASON
        if ALLOW_RE.search(command) or _human_only_command(command):
            return HUMAN_REASON
        if HOUSE_ENV_RE.search(command):
            return ENV_REASON
        for seg in _commands(command):
            if _runs_signal_cli(seg):
                return SIGNAL_CLI_REASON
            for target, base in _written_paths(seg, cwd):
                full = _expand(target, base, home)
                if _under(full, config_dir):
                    return CONFIG_REASON
                if _under(full, data_dir):
                    return DATA_REASON
        return None
    path = args.get("file_path") or args.get("notebook_path") or args.get("path")
    if not path:
        return None
    full = _expand(str(path), cwd, home)
    if _under(full, cli_dir):
        return SIGNAL_FILES_REASON
    if tool not in READ_TOOLS and _under(full, config_dir):
        return CONFIG_REASON
    if tool not in READ_TOOLS and _under(full, data_dir):
        return DATA_REASON
    return None


def _human_only_command(command: str) -> bool:
    """`… signal allow|deny …` or `… signal agent … on|members …` in any form the tokenizer sees (quoted words,
    flags before the action, the program name in a variable), also inside quoted command strings."""
    texts = [command]
    for seg in _segments(command):
        texts += [t for t in seg if " " in t]
    for text in texts:
        for seg in _segments(text):
            words = [t.strip("'\"").lower() for t in seg]
            if "signal" not in words:
                continue
            rest = [w for w in words[words.index("signal") + 1:] if not w.startswith("-")]
            if rest[:1] and rest[0] in HUMAN_WORDS:
                return True
            if rest[:1] == ["agent"] and HUMAN_AGENT_WORDS & set(rest[1:]):
                return True
    return False


# wrapper options whose value is the next word (attached forms such as `-uX`, `-sKILL`, `--signal=KILL` are one word)
WRAPPER_ARG_OPTIONS = {
    "env": {"-u", "--unset", "-C", "--chdir"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "sudo": {"-u", "--user", "-g", "--group", "-C", "--close-from"},
    "nice": {"-n", "--adjustment"},
    "stdbuf": {"-i", "-o", "-e", "--input", "--output", "--error"},
}


def _command_start(seg: list[str], wrappers: set[str] = WRAPPERS) -> int:
    """Index of the program a simple command runs: after `VAR=x` words and wrappers such as `timeout 5` or `env`
    (with their options, including those that take a value: `env -u X`, `timeout -s KILL`, `sudo -u u`, ...)."""
    i = 0
    while i < len(seg):
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", seg[i]):
            i += 1
        elif Path(seg[i]).name in wrappers:
            takes_value = WRAPPER_ARG_OPTIONS.get(Path(seg[i]).name, set())
            i += 1
            while i < len(seg) and (seg[i].startswith("-") or re.fullmatch(r"[\d.]+[smhd]?", seg[i])):
                i += 2 if seg[i] in takes_value else 1
        else:
            break
    return i


def _through_house_out(seg: list[str]) -> list[str]:
    """`house out [--] CMD …` and `house exp … -- CMD …` (any path ending in `/house`, wrappers and nesting allowed)
    are CMD itself. For `exp`, `cli.main` cuts at the first `--` whatever comes before it (`exp --budget 5m run …`).

    Redirections anywhere in the segment (`house >/dev/null out -- rm x`) are skipped while looking for the wrapper
    and kept, after CMD, so their targets are still checked. `house out show …`, `house out prune …`, `house out`
    with an option and every `house exp` without `--` are not wrappers and stay as they are.
    """
    words, redirs = _split_redirects(seg)
    unwrapped = False
    while True:
        i = _command_start(words)
        if not (i + 1 < len(words) and Path(words[i]).name == "house" and words[i + 1] in ("out", "exp")):
            break
        rest = words[i + 2:]
        if words[i + 1] == "exp":
            if "--" not in rest:
                break
            rest = rest[rest.index("--") + 1:]  # `cli.main`: everything after the first `--` is the command
        elif rest[:1] == ["--"]:
            rest = rest[1:]
        elif not rest or rest[0] in ("show", "prune") or rest[0].startswith("-"):
            break
        if not rest:
            break
        words, unwrapped = rest, True
    return [*words, *redirs] if unwrapped else seg


def _shell_script(seg: list[str]) -> str | None:
    """The SCRIPT of `sh|bash|… [options] -c SCRIPT` (also `-lc`, `-ec`, after wrappers), or of a command that is one
    word with spaces (`house exp run … -- 'a && b'` runs that word through the shell), else None."""
    words, _ = _split_redirects(seg)
    if len(words) == 1 and any(ch.isspace() for ch in words[0]):
        return words[0]
    i = _command_start(words)
    if i >= len(words) or Path(words[i]).name not in SHELLS:
        return None
    j = i + 1
    while j < len(words) and words[j].startswith(("-", "+")):
        word = words[j]
        if word in ("--rcfile", "--init-file"):
            j += 2
            continue
        if not word.startswith("--") and "c" in word[1:]:
            return words[j + 1] if j + 1 < len(words) else None
        j += 2 if not word.startswith("--") and word[-1] in "oO" else 1  # `-o X`, `-euo X`, `+O X`
    return None


def _commands(command: str, depth: int = 0) -> list[list[str]]:
    """The simple commands of a shell line with `house out [--]` unwrapped and `sh -c SCRIPT` expanded (up to three
    levels deep): what really runs."""
    out = []
    for seg in _segments(command):
        seg = _through_house_out(seg)
        out.append(seg)
        script = _shell_script(seg)
        if script and depth < 3:
            out += _commands(script, depth + 1)
    return out


def _runs_signal_cli(seg: list[str]) -> bool:
    """Tell whether a simple command runs signal-cli: as the command (after `VAR=x` and wrappers such as
    `timeout 5`, `env`, `script -qc`), or inside a quoted command string (`bash -c "signal-cli …"`)."""
    i = _command_start(seg)
    if i < len(seg) and Path(seg[i]).name == "signal-cli":
        return True
    return any(" " in t and INNER_SIGNAL_CLI_RE.search(t) for t in seg)


JOB_BLOCKED_COMMANDS = {"gh", "curl", "wget", "ssh", "scp", "sftp", "nc", "ncat", "netcat", "socat", "rsync",
                        "house", "signal-cli", "sudo", "su", "doas", "systemctl", "systemd-run", "crontab", "at"}
JOB_BLOCKED_GIT = {"push", "remote", "config", "fetch", "pull", "clone", "submodule", "commit", "worktree", "gc",
                   "filter-branch", "update-ref", "symbolic-ref", "hooks"}
JOB_SYSTEM_READ = ("/usr", "/etc/ssl", "/etc/ca-certificates", "/etc/alternatives", "/lib", "/lib64", "/opt")
JOB_REASON = ("Signal job: {what}. The job works only inside its worktree, without network, git history changes "
              "or house commands; house commits and replies when the job ends")


def _command_name(seg: list[str]) -> tuple[str, list[str]]:
    """The program a simple command runs (after `VAR=x` and wrappers such as `timeout 5`) and its arguments."""
    i = _command_start(seg, WRAPPERS - {"sudo"})
    return (Path(seg[i]).name, seg[i + 1:]) if i < len(seg) else ("", [])


def check_job(tool: str, args: dict, cwd: Path, worktree: Path, home: Path | None = None) -> str | None:
    """Extra rules for an agent started from Signal (`HOUSE_AGENT_JOB` set), on top of `check_signal`.

    Refuses network tools, `git` subcommands that change history, remotes or config, house and systemd commands,
    writes outside the job's worktree, and reads of the home directory outside it. The sandbox enforces the same
    boundaries; this is the second layer, and the hook fails closed in job mode.

    Returns:
        A reason to refuse, or None.
    """
    home = (home or Path.home()).resolve()
    wt = Path(worktree).resolve()
    reason = check_signal(tool, args, cwd, home=home)
    if reason:
        return reason
    if tool == "Bash":
        for seg in _segments(str(args.get("command") or "")):
            name, _ = _command_name(seg)
            if name in JOB_BLOCKED_COMMANDS:
                return JOB_REASON.format(what=f"`{name}` is not available")
            if name == "git":
                sub, _ = _git_parts(seg, cwd)
                if sub[:1] and sub[0] in JOB_BLOCKED_GIT:
                    return JOB_REASON.format(what=f"`git {sub[0]}` is not available")
            if name == "ln" or any("symlink" in t for t in seg):
                return JOB_REASON.format(what="links are not available (house sends only real files)")
            for target, base in _written_paths(seg, cwd):
                full = _expand(target, base, home)
                if not _under(full, wt):
                    return JOB_REASON.format(what=f"writing {target} outside the worktree")
                if _protected_in_worktree(full, wt):
                    return JOB_REASON.format(what=f"{target} is managed by house")
            for tok in seg:
                if tok.startswith(("~", "$HOME", "${HOME}", "/")) and _under(_expand(tok, cwd, home), home) \
                        and not _under(_expand(tok, cwd, home), wt):
                    return JOB_REASON.format(what=f"{tok} is outside the worktree")
        return None
    path = args.get("file_path") or args.get("notebook_path") or args.get("path")
    pattern = str(args.get("pattern") or "")
    if tool == "Glob" and pattern.startswith(("/", "~")):
        path = pattern.split("*", 1)[0] or "/"
    if not path:
        return None
    full = _expand(str(path), cwd, home)
    if tool in READ_TOOLS:
        if not _under(full, wt) and not any(_under(full, Path(p)) for p in JOB_SYSTEM_READ):
            return JOB_REASON.format(what=f"reading {path} outside the worktree")
        return None
    if not _under(full, wt):
        return JOB_REASON.format(what=f"writing {path} outside the worktree")
    if _protected_in_worktree(full, wt):
        return JOB_REASON.format(what=f"{path} is managed by house (git metadata, Claude settings)")
    return None


def _protected_in_worktree(full: Path, wt: Path) -> bool:
    """`.git` (file or dir, at any depth), `.gitattributes`, `.gitmodules` and `.claude/` inside the worktree."""
    parts = full.relative_to(wt).parts if _under(full, wt) else ()
    return any(p in (".git", ".claude") for p in parts) or (parts[-1:] and parts[-1] in (".gitattributes",
                                                                                         ".gitmodules"))


def _sha(path: Path) -> str | None:
    """Return the SHA-256 hex digest of the file at `path`, or None when it is not a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _at_head(repo: Path, rel: str) -> str | None:
    """Return the content of `rel` at HEAD in `repo`, or None when HEAD does not have it."""
    r = _git(repo, "show", f"HEAD:{rel}")
    return r.stdout if r.returncode == 0 else None


def register_changed(contest: Path) -> bool:
    """Tell whether the attempt register differs from HEAD, byte for byte.

    A missing file equals a register that HEAD does not have either; any other difference (an edit, a deletion, a
    new file not committed yet) counts as changed. Bytes are compared because the register may hold any content.
    """
    shown = subprocess.run(["git", "show", f"HEAD:{REGISTER}"], cwd=contest, capture_output=True, check=False)
    head_bytes = shown.stdout if shown.returncode == 0 else None
    path = contest / REGISTER
    return (path.read_bytes() if path.is_file() else None) != head_bytes


def restore_register(contest: Path) -> None:
    """Put the attempt register back to HEAD's bytes (working tree and index); delete it when HEAD has none."""
    if _git(contest, "cat-file", "-e", f"HEAD:{REGISTER}").returncode == 0:
        _git(contest, "checkout", "HEAD", "--", REGISTER)
    else:
        (contest / REGISTER).unlink(missing_ok=True)
        _git(contest, "rm", "--cached", "-q", "--ignore-unmatch", "--", REGISTER)


def frozen_tampering(contest: Path) -> list[str]:
    """Find uncommitted changes in the contest checkout that only house itself may make.

    Compares the working tree with HEAD (house commits its own `.house/` changes at once, so any difference is
    a hand edit). Reports: an unreadable `.house/checks/*.json`; a changed `frozen` section there (legitimate only
    through `house check --freeze/--refreeze`); a task file `.house/tasks/T*-*.md` whose `status` was set to
    `merged` / `done` by hand (only `house merge` sets it); `.house/attempts.jsonl` that differs from HEAD
    (house commits each register row right after writing it, so any difference is a hand edit, a forged or deleted
    row). Used by `house merge` as a gate and by
    `diff_violations`.

    Args:
        contest: Contest checkout root.

    Returns:
        Human-readable messages, one per problem; an empty list means no tampering.
    """
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
    if register_changed(contest):
        found.append(f"{REGISTER} changed outside house (only house night/merge write the attempt register); "
                     f"restore with: git checkout HEAD -- {REGISTER}")
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


def _secrets_in_history(repo: Path, base: str) -> list[str]:
    """Paths that looked like secrets in ANY commit of base..HEAD (a later deletion does not remove them)."""
    found: set[str] = set()
    for commit in _git(repo, "rev-list", f"{base}..HEAD").stdout.split():
        names = _git(repo, "show", "--name-only", "--diff-filter=AM", "--format=", commit).stdout.splitlines()
        for rel in filter(None, names):
            name = Path(rel).name
            if name.startswith(".env") and name != ".env.example":
                found.add(rel)
                continue
            blob = subprocess.run(["git", "show", f"{commit}:{rel}"], cwd=repo, capture_output=True, check=False)
            if blob.returncode == 0 and looks_secret(name, blob.stdout):
                found.add(rel)
    return sorted(found)


def diff_violations(contest: Path, task_id: str, worktree: Path) -> list[str]:
    """Report what a task branch changed that it must not, plus tampering in the contest checkout.

    Looks at everything the branch changed since its merge-base with the contest HEAD (commits and uncommitted
    or untracked files in the worktree). Reports: a protected file (see `protected_paths`) that changed (for the
    local scorer any change counts; for frozen tests a content different from the pinned hash counts); any file
    under `.house/` (workers write via `$HOUSE_DIR`); any changed file that looks like a secret; and everything
    `frozen_tampering` finds. Backs `house guard diff` (prints `VIOLATION <message>` per item) and the
    `house merge` gate (`refused by guard: ...`), so message texts are user-visible.

    Args:
        contest: Contest checkout root.
        task_id: Task id (e.g. "T001"); currently unused, kept for the caller's signature.
        worktree: The task's git worktree directory.

    Returns:
        Violation messages; an empty list means clean (exit 0 for the CLI).
    """
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
    for rel in _secrets_in_history(worktree, base):
        found.append(f"{rel} held a secret in a commit on the branch (rewrite the branch history to drop it)")
    for rel in sorted(changed):
        if rel == ".house" or rel.startswith(".house/"):
            found.append(f"{rel}: task branches must not carry .house/ files (write via $HOUSE_DIR)")
        elif _secret_file(worktree / rel):
            found.append(f"{rel} looks like a secret")
    return found + frozen_tampering(contest)


# `<<<<<<<` / `>>>>>>>` lines only: a lone `=======` is a legitimate RST or setext underline, never a marker
_CONFLICT_MARKER = re.compile(rb"^(<{7}|>{7})( |$)")


def conflict_markers(repo: Path, main_ref: str | None, diff_args: tuple[str, ...] = (),
                     files: list[str] | tuple[str, ...] = (), env: dict | None = None) -> set[str]:
    """Paths holding conflict marker lines that the main line does not carry itself.

    The one scan for conflict markers (`night` before and after committing a merge of main, the `pre-commit` hook
    during a merge). A marker line starts with `<<<<<<<` or `>>>>>>>` followed by a space or the line end; a lone
    `=======` never counts.

    Args:
        repo: The worktree to look in.
        main_ref: The main-line commit that was merged (`MERGE_HEAD` or its sha). A marker line counts only when the
            `main_ref` version of the same file lacks that exact line (main may carry such lines on purpose, e.g. a
            conflict sample in the docs). None: every marker line counts.
        diff_args: Arguments of a `git diff` whose added lines are scanned (e.g. `("HEAD",)`, `("--cached",
            "HEAD")`, `("<base>..HEAD",)`); empty for no diff. `git diff` never sees untracked files: the night
            scans those after staging them (`--cached`, just before its commit), the hook sees them staged.
        files: Paths (relative to `repo`) whose whole working-tree content is scanned too.
        env: Environment for git (the hook passes its index file).

    Returns:
        The paths with at least one such marker line.
    """
    found: dict[str, set[bytes]] = {}
    if diff_args:
        r = subprocess.run(["git", "-c", "core.quotePath=false", "diff", "--no-renames", *diff_args, "-U0",
                            "--no-color", "--no-ext-diff"], cwd=repo, capture_output=True, check=False, env=env)
        current, in_hunk = "", False
        for raw in r.stdout.split(b"\n"):
            # file headers come from `diff --git a/<p> b/<p>` (both sides name the same path, renames are off), not
            # from `+++ `: an added line that itself starts with `++ ` shows as `+++ ...` inside a hunk
            if raw.startswith(b"diff --git "):
                rest = raw[len(b"diff --git "):].decode(errors="replace")
                size = (len(rest) - 5) // 2
                current = rest[2:2 + size] if size > 0 and rest.startswith("a/") else ""
                in_hunk = False
            elif raw.startswith(b"diff --cc ") or raw.startswith(b"diff --combined "):
                current, in_hunk = raw.split(b" ", 2)[2].decode(errors="replace"), False
            elif raw.startswith(b"@@"):
                in_hunk = True
            elif in_hunk and current and raw.startswith(b"+") and _CONFLICT_MARKER.match(raw[1:]):
                found.setdefault(current, set()).add(raw[1:])
    for rel in files:
        path = repo / rel
        try:
            data = path.read_bytes() if path.is_file() and not path.is_symlink() else b""
        except OSError:
            data = b""
        lines = {ln for ln in data.split(b"\n") if _CONFLICT_MARKER.match(ln)}
        if lines:
            found.setdefault(rel, set()).update(lines)
    out = set()
    for rel, lines in found.items():
        if main_ref:
            shown = subprocess.run(["git", "show", f"{main_ref}:{rel}"], cwd=repo, capture_output=True, check=False,
                                   env=env)
            lines = lines - set(shown.stdout.split(b"\n")) if shown.returncode == 0 else lines
        if lines:
            out.add(rel)
    return out
