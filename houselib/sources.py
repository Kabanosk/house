"""`.house/sources/`: contest documents (rules, task statement, API docs) kept byte for byte, with an index (K3).

Agents read these documents by line range instead of the model retyping them, so a file is copied, never
rewritten. `add` copies a file (or stdin bytes) into `<contest>/.house/sources/<name>` and records it in
`<contest>/.house/sources/index.json`:

    {"files": [{"name", "sha256", "bytes", "chars", "lines", "added", "origin"[, "binary"]}]}

Entries are sorted by name and keys are sorted. `origin` is `"stdin"` or the source file's base name (never a
path). `chars` and `lines` come from decoding the stored bytes as UTF-8 with `errors="replace"`; `lines` counts
`\\n` separators the way `grep -n` numbers them (a last line without a newline counts). A NUL byte in the first
8 KB marks the file `"binary": true` (counts are still given). Both the document and the index are written to a
temp file in the same directory and moved into place with `os.replace`, so a crash never leaves a half file
under a final name. The first `add` also writes `.house/sources/.gitignore` with `.add-*` (an existing one is kept),
so a temp file left by a crash is never committed. Nothing here commits; the coordinator commits `.house/` as usual.

`listing` is the table of contents agents read first: per file the size in characters, ~tokens (chars/4), lines,
the Markdown headings with line numbers (outside code fences, at most 50), and warnings (lines over 2 000
characters, a checksum that differs from `index.json`). Every file is streamed once in chunks, so a 500K-character
regulation costs one pass and constant memory. Only files whose names follow `NAME_RE` are read; any other file in
the directory is listed by its `repr` as `invalid name (not read)`. `index.json` is read without following a symlink
and only when it is a regular file.
"""
import codecs
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from datetime import datetime
from pathlib import Path

NAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,80}")
INDEX_NAME = "index.json"
_CHUNK = 1 << 20
_BINARY_PROBE = 8192
STDIN_MAX = 1_000_000  # bytes accepted from stdin by the CLI (pasted text is small; larger input goes via a file)
LONG_LINE = 2000
MAX_HEADINGS = 50
HEADING_CUT = 100
_PREFIX = 512  # characters of each line kept for heading and fence detection
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")


def _now() -> str:
    """Current local time as an ISO 8601 string with offset, to the second."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _sources_dir(contest: Path) -> Path:
    """Locate `<contest>/.house/sources`, refusing symlinked `.house` or `sources`.

    Args:
        contest: Contest repository path.

    Returns:
        The sources directory path (it may not exist yet).

    Raises:
        ValueError: If `.house` or `.house/sources` is a symlink (files would land outside the tracked tree).
    """
    house = Path(contest) / ".house"
    directory = house / "sources"
    for path in (house, directory):
        if path.is_symlink():
            raise ValueError(f"{path} is a symlink; refusing to write through it")
    return directory


def _validate_name(name: str) -> str:
    """Check a stored file name against the name rule.

    Args:
        name: Candidate name (used as given, never rewritten).

    Returns:
        The same name.

    Raises:
        ValueError: If it does not match `[a-z0-9][a-z0-9._-]{0,80}` or is the reserved `index.json`.
    """
    if not NAME_RE.fullmatch(name):
        raise ValueError(f"bad name {name!r}: use [a-z0-9][a-z0-9._-]{{0,80}} (lower case, no path separators)")
    if name == INDEX_NAME:
        raise ValueError(f"{INDEX_NAME} is a reserved name")
    return name


def _derive_name(base: str) -> str:
    """Derive a stored name from a source file's base name.

    Args:
        base: Base name of the source file.

    Returns:
        The base name lower-cased, characters outside `[a-z0-9._-]` mapped to `-`, leading characters that are
        not `[a-z0-9]` stripped.

    Raises:
        ValueError: If the result is empty, too long or reserved (pass an explicit name instead).
    """
    name = re.sub(r"[^a-z0-9._-]", "-", base.lower())
    name = re.sub(r"^[^a-z0-9]+", "", name)
    try:
        return _validate_name(name)
    except ValueError as exc:
        raise ValueError(f"cannot derive a name from {base!r} ({exc}); pass an explicit name") from None


def _open_nofollow(path: Path):
    """Open a file for binary reading without following a final symlink.

    Args:
        path: File to open.

    Returns:
        A buffered binary file object.

    Raises:
        OSError: With `errno.ELOOP` if `path` is a symlink, or any other open error.
    """
    return os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb")


def _scan(path: Path, on_line=None) -> dict:
    """Stream a file once and compute its hash and size figures.

    Args:
        path: File to scan (a final symlink is refused, not followed).
        on_line: Optional callback `(line_number, prefix, length)` per line, in order, where `prefix` is the
            first `_PREFIX` characters (no newline) and `length` the full line length in characters (a trailing
            CR of a CRLF line is not counted).

    Returns:
        Dict with `sha256`, `bytes`, `chars`, `lines` and, only for a binary file, `binary: True`.
    """
    digest = hashlib.sha256()
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    size = chars = newlines = 0
    last = ""
    binary = False
    line_no, cur_len, cur_prefix, cur_last = 1, 0, "", ""

    def feed(text: str) -> None:
        nonlocal chars, newlines, last, line_no, cur_len, cur_prefix, cur_last
        if not text:
            return
        chars += len(text)
        newlines += text.count("\n")
        last = text[-1]
        if on_line is None:
            return
        for i, part in enumerate(text.split("\n")):
            if i:
                on_line(line_no, cur_prefix, cur_len - (cur_last == "\r"))
                line_no, cur_len, cur_prefix, cur_last = line_no + 1, 0, "", ""
            cur_len += len(part)
            cur_last = part[-1] if part else cur_last
            if len(cur_prefix) < _PREFIX:
                cur_prefix += part[: _PREFIX - len(cur_prefix)]

    with _open_nofollow(path) as fh:
        while chunk := fh.read(_CHUNK):
            if size < _BINARY_PROBE and b"\x00" in chunk[: _BINARY_PROBE - size]:
                binary = True
            digest.update(chunk)
            size += len(chunk)
            feed(decoder.decode(chunk))
    feed(decoder.decode(b"", final=True))
    if on_line is not None and cur_len:
        on_line(line_no, cur_prefix, cur_len - (cur_last == "\r"))
    result = {"sha256": digest.hexdigest(), "bytes": size, "chars": chars,
              "lines": newlines + (1 if chars and last != "\n" else 0)}
    if binary:
        result["binary"] = True
    return result


def _read_index_text(path: Path) -> str | None:
    """Read `index.json` without following a symlink or blocking on a special file.

    Args:
        path: `index.json` path.

    Returns:
        The text, or None when the file does not exist.

    Raises:
        ValueError: If it is a symlink or not a regular file, or cannot be read.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError(f"{INDEX_NAME} is a symlink ({path}); refusing to read through it") from None
        raise ValueError(f"{INDEX_NAME} is unreadable ({path}); fix or remove it") from None
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            raise ValueError(f"{INDEX_NAME} is not a regular file ({path}); fix or remove it")
        return fh.read().decode("utf-8", errors="strict")


def _load_index(path: Path) -> list[dict]:
    """Read the index entries.

    Args:
        path: `index.json` path (may not exist).

    Returns:
        The list of entries; empty if the file is absent.

    Raises:
        ValueError: If the file is a symlink or not a regular file, is unreadable, not JSON, or not in the expected
            shape.
    """
    text = _read_index_text(path)
    if text is None:
        return []
    try:
        files = json.loads(text)["files"]
        if not isinstance(files, list) or not all(isinstance(e, dict) and isinstance(e.get("name"), str) for e in files):
            raise TypeError("files")
    except (ValueError, KeyError, TypeError):
        raise ValueError(f"{INDEX_NAME} is corrupt or unreadable ({path}); fix or remove it") from None
    return files


def _ensure_gitignore(directory: Path) -> None:
    """Create `.gitignore` with `.add-*` unless one is there (never written through a symlink, never changed)."""
    try:
        fd = os.open(directory / ".gitignore", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    except FileExistsError:
        return
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(".add-*\n")


def _atomic_write(directory: Path, final: str, fill) -> None:
    """Create `directory/final` atomically via a temp file in the same directory.

    Args:
        directory: Existing target directory.
        final: Final file name.
        fill: Callable taking the temp path that must write the complete content there.

    Raises:
        Exception: Whatever `fill` or the final move raises; the temp file is removed first.
    """
    fd, tmp = tempfile.mkstemp(prefix=".add-", dir=directory)
    os.close(fd)
    try:
        fill(Path(tmp))
        os.chmod(tmp, 0o644)
        os.replace(tmp, directory / final)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def add(contest: Path, src: Path | None = None, data: bytes | None = None, name: str | None = None,
        replace: bool = False) -> dict:
    """Copy a document byte for byte into the contest's `.house/sources/` and record it in the index.

    Args:
        contest: Contest repository path.
        src: File to copy (not a symlink). Give exactly one of `src` and `data`.
        data: Bytes to store (e.g. stdin); requires `name`.
        name: Stored file name; validated, never rewritten. Defaults to the lower-cased, sanitized base name
            of `src`.
        replace: Overwrite an existing document of that name (the index entry and `added` are refreshed).

    Returns:
        The index entry: `name`, `sha256`, `bytes`, `chars`, `lines`, `added`, `origin` (+ `binary` if binary).

    Raises:
        ValueError: On bad arguments or names, a missing, symlinked or non-regular source, an existing name
            without `replace`, or a corrupt index.
    """
    if (src is None) == (data is None):
        raise ValueError("give exactly one of a source file and data")
    if src is not None:
        src = Path(src)
        if src.is_symlink():
            raise ValueError(f"{src.name}: source is a symlink; refusing (add the real file instead)")
        if not src.exists():
            raise ValueError(f"{src}: source file not found")
        if src.is_dir():
            raise ValueError(f"{src}: source is a directory, not a file")
        if not src.is_file():
            raise ValueError(f"{src}: source is not a regular file")
        stored = _validate_name(name) if name is not None else _derive_name(src.name)
        origin = src.name
    else:
        if name is None:
            raise ValueError("data from stdin needs an explicit name")
        stored = _validate_name(name)
        origin = "stdin"

    directory = _sources_dir(contest)
    index_path = directory / INDEX_NAME
    files = _load_index(index_path)
    if (directory / stored).is_dir() and not (directory / stored).is_symlink():
        raise ValueError(f"{stored} is a directory in {directory}; remove it first")
    exists = (directory / stored).exists() or any(e["name"] == stored for e in files)
    if exists and not replace:
        raise ValueError(f"{stored} already exists; use replace to overwrite it")

    directory.mkdir(parents=True, exist_ok=True)
    _ensure_gitignore(directory)
    scanned: dict = {}

    def fill(tmp: Path) -> None:
        if src is not None:
            try:  # O_NOFOLLOW: a link swapped in after the check above is refused, never followed
                fd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise ValueError(f"{src.name}: source is a symlink; refusing (add the real file instead)") from None
                raise
            with os.fdopen(fd, "rb") as fin, open(tmp, "wb") as fout:
                if not stat.S_ISREG(os.fstat(fin.fileno()).st_mode):
                    raise ValueError(f"{src}: source is not a regular file")
                shutil.copyfileobj(fin, fout, _CHUNK)
        else:
            tmp.write_bytes(data)
        scanned.update(_scan(tmp))  # the stored bytes, so the entry always matches the file

    _atomic_write(directory, stored, fill)
    entry = {"name": stored, **scanned, "added": _now(), "origin": origin}
    files = sorted([e for e in files if e["name"] != stored] + [entry], key=lambda e: e["name"])
    try:
        _atomic_write(directory, INDEX_NAME, lambda tmp: tmp.write_text(
            json.dumps({"files": files}, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"))
    except BaseException:
        if not exists:
            (directory / stored).unlink(missing_ok=True)
        raise
    return dict(sorted(entry.items()))


class _Outline:
    """Collects Markdown headings (outside code fences) and over-long lines while a file is streamed.

    Attributes:
        headings: The first `MAX_HEADINGS` headings as `(line, hashes, text)`.
        heading_count: Total number of headings seen.
        long_count: Number of lines longer than `LONG_LINE` characters.
        long_first: Line number of the first such line, or None.
        long_max: Length of the longest line seen.
        fence_line: Line number where a still-open code fence was opened, or None.
    """

    def __init__(self) -> None:
        self.headings: list[tuple[int, str, str]] = []
        self.heading_count = 0
        self.long_count = 0
        self.long_first: int | None = None
        self.long_max = 0
        self.fence_line: int | None = None
        self._fence: tuple[str, int] | None = None

    def __call__(self, line_no: int, prefix: str, length: int) -> None:
        """Take one line: update fence state, headings and the long-line figures.

        Args:
            line_no: 1-based line number.
            prefix: Start of the line without its newline (a trailing CR is ignored).
            length: Full length of the line in characters (without a CR before the newline).
        """
        if length > LONG_LINE:
            self.long_count += 1
            self.long_first = self.long_first or line_no
        self.long_max = max(self.long_max, length)
        text = prefix.rstrip("\r")
        fence = FENCE_RE.match(text)
        if self._fence:
            char, size = self._fence
            if fence and fence.group(1)[0] == char and len(fence.group(1)) >= size and not fence.group(2).strip():
                self._fence = self.fence_line = None
            return
        if fence and not (fence.group(1)[0] == "`" and "`" in fence.group(2)):
            self._fence = (fence.group(1)[0], len(fence.group(1)))
            self.fence_line = line_no
            return
        heading = HEADING_RE.match(text)
        if heading:
            self.heading_count += 1
            if len(self.headings) < MAX_HEADINGS:
                title = heading.group(2)
                if len(title) > HEADING_CUT:
                    title = title[:HEADING_CUT] + "..."
                self.headings.append((line_no, heading.group(1), title))


def _describe(directory: Path, name: str, entry: dict | None, has_index: bool) -> str:
    """Render the listing block of one file.

    Args:
        directory: The sources directory.
        name: File name.
        entry: Its index entry, or None.
        has_index: Whether a readable index exists (without one nothing is "not in index").

    Returns:
        The block text (header line, headings, warnings), without a trailing newline.
    """
    path = directory / name
    if path.is_symlink():
        return f"{name}: symlink, not read"
    if not path.is_file():
        return f"{name}: missing (listed in index.json, file is gone)"
    outline = _Outline()
    stats = _scan(path, outline)
    if stats.get("binary"):
        lines = [f"{name}: binary, {stats['bytes']} bytes (no headings)"]
    else:
        lines = [f"{name}: {stats['chars']} chars, ~{stats['chars'] // 4} tokens, {stats['lines']} lines"]
        lines += [f"  L{n}  {hashes} {title}" for n, hashes, title in outline.headings]
        if outline.heading_count > len(outline.headings):
            lines.append(f"  ... and {outline.heading_count - len(outline.headings)} more headings")
        if outline.fence_line:
            lines.append(f"  warning: code fence opened at L{outline.fence_line} is never closed; "
                         "headings after it are not shown")
        if outline.long_count:
            word = "line" if outline.long_count == 1 else "lines"
            lines.append(f"  warning: {outline.long_count} {word} longer than {LONG_LINE} characters "
                         f"(first at L{outline.long_first}, longest {outline.long_max}); read them by character range")
    if entry is None:
        if has_index:
            lines.append("  note: not in index (no checksum to compare; re-add it with `house sources add`)")
    elif entry.get("sha256") != stats["sha256"]:
        lines.append("  warning: content differs from index.json (edited after it was added); "
                     "line ranges in spec.yaml may have moved")
    return "\n".join(lines)


def listing(contest: Path) -> str:
    """Table of contents of the contest's `.house/sources/` with checks.

    Streams every file once (sha256, sizes and headings in the same pass). Only ATX headings (`# Title`) are
    listed; Setext headings (a text line underlined with `===` or `---`) are not. Index entries whose name breaks
    the name rule are shown as invalid and never read; so are files on disk whose names break it. Files that are dotfiles, `.add-*` temp
    files, `index.json` or subdirectories are skipped; symlinks are noted but never read.

    Args:
        contest: Contest repository path.

    Returns:
        Text for the terminal: one block per file (blocks separated by a blank line), or `no sources yet` with
        the `house sources add` hint when there is nothing.

    Raises:
        ValueError: If `.house` or `.house/sources` is a symlink.
    """
    directory = _sources_dir(contest)
    notes: list[str] = []
    try:
        entries = {e["name"]: e for e in _load_index(directory / INDEX_NAME)}
        has_index = True
    except ValueError as exc:
        entries, has_index = {}, False
        notes.append(f"warning: {exc}")
    invalid = sorted(n for n in entries if not NAME_RE.fullmatch(n) or n == INDEX_NAME)
    entries = {n: e for n, e in entries.items() if n not in invalid}
    on_disk = set()
    if directory.is_dir():
        on_disk = {p.name for p in directory.iterdir()
                   if not p.name.startswith(".") and p.name != INDEX_NAME and (p.is_file() or p.is_symlink())}
    bad_files = sorted(n for n in on_disk if not NAME_RE.fullmatch(n))
    names = sorted((on_disk - set(bad_files)) | set(entries))
    if not names and not invalid and not bad_files:
        cname = Path(contest).resolve().name
        hint = (f"no sources yet\nadd the contest documents: house sources add {cname} <file> "
                f"[--name NAME]  (stdin: house sources add {cname} - --name NAME)")
        return "\n".join(notes + [hint])
    total = len(names) + len(invalid) + len(bad_files)
    head = [f"{total} source{'s' if total != 1 else ''} in .house/sources/ "
            f"(read by line range; ~tokens = chars/4)"]
    blocks = ["\n".join(notes + head)]
    blocks += [_describe(directory, n, entries.get(n), has_index) for n in names]
    blocks += [f"{n!r}: invalid name in index (not read)" for n in invalid]
    blocks += [f"{n!r}: invalid name (not read)" for n in bad_files]
    return "\n\n".join(blocks)
