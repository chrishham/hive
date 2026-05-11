# src/hive/safety.py
from __future__ import annotations

import os
import re
from pathlib import Path

SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_REPLACE_RE = re.compile(r"[^A-Za-z0-9._-]")
_MAX_LEN = 64
_DEFAULT_NAME = "session"


class InvalidSessionName(ValueError):
    pass


class TmuxError(RuntimeError):
    pass


def validate_session_name(name: str) -> None:
    if not isinstance(name, str) or not SESSION_NAME_RE.fullmatch(name):
        raise InvalidSessionName(f"invalid session name: {name!r}")
    if name in {".", ".."}:
        raise InvalidSessionName(f"reserved session name: {name!r}")


def sanitize_session_name(name: str) -> str:
    if not isinstance(name, str):
        return _DEFAULT_NAME
    cleaned = _REPLACE_RE.sub("_", name)[:_MAX_LEN]
    if not cleaned or cleaned in {".", ".."} or set(cleaned) == {"_"}:
        return _DEFAULT_NAME
    return cleaned


def _open_dir_no_symlinks(parent: Path) -> int:
    """Open `parent` ensuring no component is a symlink. Returns a dirfd."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    parts = parent.resolve(strict=False).parts if not parent.is_absolute() else parent.parts
    if not parent.is_absolute():
        parts = parent.absolute().parts
    cur_fd = os.open(parts[0], os.O_RDONLY | directory)
    try:
        for component in parts[1:]:
            try:
                next_fd = os.open(
                    component,
                    os.O_RDONLY | directory | nofollow,
                    dir_fd=cur_fd,
                )
            except OSError as exc:
                raise OSError(f"refusing to traverse {parent}: {exc}") from exc
            os.close(cur_fd)
            cur_fd = next_fd
        return cur_fd
    except BaseException:
        os.close(cur_fd)
        raise


def _refuse_symlink_in_existing_chain(parent: Path) -> None:
    abs_parent = parent.absolute()
    cur = Path(abs_parent.parts[0])
    for component in abs_parent.parts[1:]:
        cur = cur / component
        if not cur.exists():
            return
        if cur.is_symlink():
            raise OSError(f"refusing to traverse symlink: {cur}")


def atomic_write_text(path: Path, content: str, *, follow_symlinks: bool = False) -> None:
    if follow_symlinks:
        # Resolve once up-front so the atomic write lands on the real target
        # (e.g. dotfiles-managed settings symlinked into ~/.claude). The
        # remaining symlink protections still apply to the resolved parent
        # chain and the resolved destination.
        path = Path(os.path.realpath(path))
    parent = path.parent
    _refuse_symlink_in_existing_chain(parent)
    parent.mkdir(parents=True, exist_ok=True)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        dir_fd = _open_dir_no_symlinks(parent)
    except OSError as exc:
        raise OSError(f"refusing to open parent directory: {parent}") from exc
    tmp_name: str | None = None
    try:
        # mkstemp doesn't accept dir_fd; create relative path under the
        # already-validated parent fd via openat semantics by passing the
        # fd-relative name to os.open.
        prefix = f".{path.name}."
        for _ in range(64):
            candidate = f"{prefix}{os.urandom(6).hex()}.tmp"
            try:
                fd = os.open(
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow,
                    0o600,
                    dir_fd=dir_fd,
                )
                tmp_name = candidate
                break
            except FileExistsError:
                continue
        else:
            raise OSError("could not allocate temp file")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            os.unlink(tmp_name, dir_fd=dir_fd)
            raise
        # Ensure final destination, if it exists, is not a symlink.
        try:
            st = os.stat(path.name, dir_fd=dir_fd, follow_symlinks=False)
            import stat as _stat
            if _stat.S_ISLNK(st.st_mode):
                raise OSError(f"refusing to replace symlink: {path}")
        except FileNotFoundError:
            pass
        os.replace(tmp_name, path.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        tmp_name = None
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name, dir_fd=dir_fd)
            except FileNotFoundError:
                pass
        os.close(dir_fd)


def escape_tmux_format(text: str) -> str:
    return text.replace("#", "##")
