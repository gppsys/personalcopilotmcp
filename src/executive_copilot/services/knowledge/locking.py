"""One cross-process write lock for knowledge and existing vault writers."""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path


class KnowledgeBusy(ValueError):
    """Another writer holds the vault lock; the caller may retry later."""


_HELD: ContextVar[frozenset[str]] = ContextVar("knowledge_write_locks", default=frozenset())


@contextmanager
def vault_lock(directory: Path | None) -> Iterator[None]:
    if directory is None:
        yield
        return
    key = str(directory.resolve())
    if key in _HELD.get():
        yield
        return
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(directory / "knowledge.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    token = None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise KnowledgeBusy("Knowledge operation already running; retry later") from None
        token = _HELD.set(_HELD.get() | {key})
        yield
    finally:
        if token is not None:
            _HELD.reset(token)
        os.close(fd)


def coordinated_write[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        with vault_lock(getattr(args[0], "coordination_dir", None)):
            return function(*args, **kwargs)

    return wrapped
