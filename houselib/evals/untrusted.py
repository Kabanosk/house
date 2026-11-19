"""Reading files an eval agent could have written, on the host, without trusting them.

    read_regular(path, limit, truncate=False) -> bytes | None

Used by the grader (QUESTIONS.md, task, check and compared files) and by `results.event_counts` (the house eval log).
The agent's mount is read-write, so any such path may have been replaced by a FIFO (an open would block the host
forever), a device, a directory, a symlink or a huge file.

Rules an agent should know:
- The file is opened with `O_NONBLOCK | O_NOFOLLOW` and checked on the open descriptor (`fstat`): only a regular file
  is read, and a swap between check and read changes nothing.
- A missing file is None; anything else that is not a regular file of at most `limit` bytes raises `Unreadable`,
  unless `truncate`, which reads the first `limit` bytes of a larger regular file instead.
"""
import os
import stat
from pathlib import Path


class Unreadable(Exception):
    """The file is not a regular file, is larger than the limit, or cannot be read."""


def read_regular(path: Path, limit: int, truncate: bool = False) -> bytes | None:
    """Read an untrusted file without blocking, following a final symlink or reading more than `limit` bytes.

    Args:
        path: The file.
        limit: Largest number of bytes read.
        truncate: Return the first `limit` bytes of a larger file instead of raising.

    Returns:
        The bytes, or None when the file does not exist.

    Raises:
        Unreadable: Not a regular file (FIFO, device, directory, symlink), larger than `limit` without `truncate`,
            or unreadable.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise Unreadable from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or (st.st_size > limit and not truncate):
            raise Unreadable
        with os.fdopen(fd, "rb", closefd=False) as f:
            data = f.read(limit + 1)
    except OSError as exc:
        raise Unreadable from exc
    finally:
        os.close(fd)
    if len(data) > limit:
        if not truncate:
            raise Unreadable
        data = data[:limit]
    return data
