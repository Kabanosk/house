"""Process locks in the data dir: one `flock` file per kind of long job, so two never run at once on a machine.

Lock files live in `notify.data_dir()` (`$HOUSE_DATA_DIR`, default `~/.local/share/house`) as `<name>.lock`.
A lock is an exclusive, non-blocking `fcntl.flock` on that file; the kernel drops it when the holder exits or
crashes, so there are no stale locks. While held, the file contains `<pid> <start-iso>` so a refused second
process can name the holder.

Names and who takes them:
- `night`: `house night` (cli.cmd_night), for the whole run of one night, any contest: one night at a time.
- `eval`: `house eval run` (cli._eval_run), for the whole evaluation run. Order: it takes `eval` first and, holding
  it, checks `held("night")` and the contests in progress, so an eval never starts over a night. The exclusion is
  one-sided by design (spec): `cmd_night` does not check `eval`.
- `upkeep`: the coming upkeep job.

Used by: `cli.cmd_night` and `cli._eval_run` (hold), `_eval_run` and doctor-style checks (held).
"""
import fcntl
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from . import notify


class Busy(Exception):
    """The lock is held by another process.

    Attributes:
        name: The lock name.
        pid: The holder's pid, or None when its lock file could not be read.
        since: The holder's start time (ISO text), or None.
    """

    def __init__(self, name: str, pid: int | None = None, since: str | None = None):
        self.name, self.pid, self.since = name, pid, since
        who = f" (held by pid {pid}" if pid is not None else ""
        if who:
            who += f", since {since})" if since else ")"
        super().__init__(f"{name} is already running{who}")


def _path(name: str) -> Path:
    """Return the lock file path for `name` inside the data dir."""
    return notify.data_dir() / f"{name}.lock"


def _holder(fd: int) -> tuple[int | None, str | None]:
    """Read `<pid> <start-iso>` from an open lock file; (None, None) when empty or unreadable."""
    try:
        text = os.pread(fd, 200, 0).decode().strip()
        pid, _, since = text.partition(" ")
        return int(pid), since or None
    except (OSError, ValueError):
        return None, None


@contextmanager
def hold(name: str):
    """Hold the lock `name` for the duration of the `with` block.

    Args:
        name: Lock name (`night`, `eval`, `upkeep`).

    Yields:
        None, while the lock is held. The lock is released on normal exit and on exceptions.

    Raises:
        Busy: Another process (or another open handle in this process) holds the lock.
    """
    fd = os.open(_path(name), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Busy(name, *_holder(fd)) from None
        os.ftruncate(fd, 0)
        os.pwrite(fd, f"{os.getpid()} {datetime.now().astimezone().isoformat(timespec='seconds')}\n".encode(), 0)
        try:
            yield
        finally:
            os.ftruncate(fd, 0)
    finally:
        os.close(fd)  # closing the descriptor drops the flock


def held(name: str) -> bool:
    """Tell whether some process holds the lock `name`, without disturbing the holder.

    Probes with a non-blocking shared lock that is released at once (a hold attempted in that instant could
    get a spurious `Busy`; the window is microseconds).

    Args:
        name: Lock name.

    Returns:
        True when the lock is taken; False when it is free or its file does not exist.
    """
    try:
        fd = os.open(_path(name), os.O_RDONLY)
    except FileNotFoundError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False
    finally:
        os.close(fd)
