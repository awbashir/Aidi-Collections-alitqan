"""Safe filesystem paths for uploaded files (review S11: path traversal).

Seven call sites used to do `os.path.join(app.root_path, "static/temp", file_name)` with
`file_name` taken straight from the query string or the JSON body, so `../../` escaped the
directory and pandas/openpyxl then parsed (and returned a column of) an arbitrary file.

Layout:
    <UPLOAD_DIR>/temp/        parsed-then-referenced Excel uploads   (new)
    <UPLOAD_DIR>/documents/   client documents                       (new)
    <UPLOAD_DIR>/exports/     generated export files for job results  (new)
    <app_root>/static/temp    the historical temp directory          (read-only fallback)

`temp_file_path()` returns the new location; `resolve_temp_file()` also falls back to the
old one so files uploaded before this refactor are still readable.
"""
import os

from werkzeug.utils import secure_filename

from core.config import Config

UPLOAD_DIR = Config.UPLOAD_DIR
TEMP_DIR = os.path.join(UPLOAD_DIR, "temp")
DOCUMENTS_DIR = os.path.join(UPLOAD_DIR, "documents")
EXPORTS_DIR = os.path.join(UPLOAD_DIR, "exports")

# Pre-refactor location, kept readable so already-uploaded files keep working.
LEGACY_TEMP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "temp")
LEGACY_DOCUMENTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "clint_documents"
)


def ensure_dirs():
    """Create the upload directories. Called once at startup."""
    for d in (UPLOAD_DIR, TEMP_DIR, DOCUMENTS_DIR, EXPORTS_DIR):
        os.makedirs(d, exist_ok=True)


def _safe_join(base, file_name):
    if not file_name:
        raise ValueError("empty file name")
    name = secure_filename(str(file_name))
    if not name:
        raise ValueError("invalid file name: %r" % (file_name,))
    path = os.path.realpath(os.path.join(base, name))
    root = os.path.realpath(base)
    if path != root and not path.startswith(root + os.sep):
        raise ValueError("path escapes %s: %r" % (base, file_name))
    return path


def temp_file_path(file_name):
    """Absolute path of `file_name` inside <UPLOAD_DIR>/temp, traversal-proof."""
    return _safe_join(TEMP_DIR, file_name)


def resolve_temp_file(file_name):
    """Path of an existing temp upload: new directory first, legacy `static/temp` second.

    Returns the new-directory path when neither exists, so callers keep producing the same
    "file not found" behaviour they had before.
    """
    new_path = temp_file_path(file_name)
    if os.path.exists(new_path):
        return new_path
    try:
        legacy = _safe_join(LEGACY_TEMP_DIR, file_name)
    except ValueError:
        return new_path
    if os.path.exists(legacy):
        return legacy
    return new_path


def document_path(file_name):
    """Absolute path of a client document inside <UPLOAD_DIR>/documents."""
    return _safe_join(DOCUMENTS_DIR, file_name)


def export_path(file_name):
    """Absolute path of a generated export inside <UPLOAD_DIR>/exports."""
    return _safe_join(EXPORTS_DIR, file_name)


def is_under_exports(path):
    """True when `path` really lives under <UPLOAD_DIR>/exports (used by /jobs/<id>/download)."""
    if not path:
        return False
    real = os.path.realpath(str(path))
    root = os.path.realpath(EXPORTS_DIR)
    return real == root or real.startswith(root + os.sep)
