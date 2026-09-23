"""Private file storage for bulk uploads and artifacts.

Layout under BULK_STORAGE_DIR (default <UPLOAD_DIR>/private/bulk):

    uploads/<yyyy>/<mm>/<upload id>.<xlsx|csv>
    artifacts/<job id>/<kind>-<n>.csv
    tmp/                          in-flight uploads before inspection

Only relative paths are stored in the database; `resolve()` refuses anything that escapes
the root. Nothing here is ever served as a static file: downloads go through the API.
"""
from __future__ import annotations

import os
import uuid

from core.bulk import settings as _settings


def root() -> str:
    return _settings.get().storage_dir


def _ensure(path: str) -> str:
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def resolve(relative: str) -> str:
    """Absolute path of a stored relative path; ValueError when it escapes the root."""
    if not relative or os.path.isabs(relative) or "\x00" in relative:
        raise ValueError(f"invalid stored path {relative!r}")
    base = os.path.realpath(root())
    path = os.path.realpath(os.path.join(base, relative))
    if not path.startswith(base + os.sep):
        raise ValueError(f"stored path escapes the bulk storage root: {relative!r}")
    return path


def tmp_path() -> str:
    return os.path.join(_ensure(os.path.join(root(), "tmp")), f"{uuid.uuid4().hex}.part")


def upload_relative(upload_id: str, ext: str, when) -> str:
    return os.path.join("uploads", f"{when:%Y}", f"{when:%m}", f"{upload_id}.{ext}")


def artifact_relative(job_id: str, kind: str) -> str:
    return os.path.join("artifacts", str(job_id), f"{kind}-{uuid.uuid4().hex[:8]}.csv")


def place(src_abs: str, relative: str) -> str:
    """Move a finished temp file to its final private location."""
    dest = resolve(relative)
    _ensure(os.path.dirname(dest))
    os.replace(src_abs, dest)
    os.chmod(dest, 0o600)
    return dest


def new_file(relative: str) -> str:
    dest = resolve(relative)
    _ensure(os.path.dirname(dest))
    return dest


def delete(relative: str | None) -> bool:
    """Delete a stored file. True when it was removed or already absent."""
    if not relative:
        return True
    try:
        os.remove(resolve(relative))
    except FileNotFoundError:
        return True
    return True


def exists(relative: str | None) -> bool:
    if not relative:
        return False
    try:
        return os.path.isfile(resolve(relative))
    except ValueError:
        return False
