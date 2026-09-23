"""Environment-driven configuration.

Defaults reproduce the values that were hard-coded in app.py so an unchanged
production deployment keeps working; anything can be overridden via .env / env vars.
"""
import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


class Config:
    # --- Flask / auth ---
    SECRET_KEY = os.getenv("SECRET_KEY", "super-secret")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", SECRET_KEY)
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=_int("JWT_ACCESS_HOURS", 24))
    DEBUG = _bool("FLASK_DEBUG", False)
    PORT = _int("PORT", 9797)
    CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()] or "*"
    MAX_CONTENT_LENGTH = _int("MAX_UPLOAD_MB", 50) * 1024 * 1024
    UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads"))

    # --- Database ---
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", "postgresql+psycopg2://postgres:101016@10.10.10.240:5433/mydata"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_size": _int("DB_POOL_SIZE", 20),
        "max_overflow": _int("DB_MAX_OVERFLOW", 30),
        "pool_pre_ping": True,
        "pool_recycle": 1800,
        "pool_timeout": 30,
        "connect_args": {
            "connect_timeout": 10,
            "options": "-c statement_timeout=%d" % (_int("DB_STATEMENT_TIMEOUT_MS", 120000)),
            "application_name": "cawp-api",
        },
    }

    # --- Redis ---
    REDIS_HOST = os.getenv("REDIS_HOST", "10.10.10.240")
    REDIS_PORT = _int("REDIS_PORT", 6379)
    REDIS_DB = _int("REDIS_DB", 0)
    REDIS_PASSWORD = os.getenv("REDIS_PASSWORD") or None
    REDIS_URL = os.getenv("REDIS_URL") or (
        f"redis://{':' + REDIS_PASSWORD + '@' if REDIS_PASSWORD else ''}{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
    )
    CACHE_DEFAULT_TTL = _int("CACHE_DEFAULT_TTL", 120)

    # --- Asterisk CDR (MySQL) ---
    CDR_HOST = os.getenv("CDR_HOST", "10.10.0.220")
    CDR_DB = os.getenv("CDR_DB", "asteriskcdrdb")
    CDR_USER = os.getenv("CDR_USER", "crm")
    CDR_PASSWORD = os.getenv("CDR_PASSWORD", "Admin123@123")
    CDR_CONNECT_TIMEOUT = _int("CDR_CONNECT_TIMEOUT", 3)
    CDR_ENABLED = _bool("CDR_ENABLED", True)

    # --- Call recordings (SSH/SFTP on the Asterisk box) ---
    # Only paths under RECORDINGS_ROOT may be fetched (review S1: the old code
    # interpolated the query string into `ssh.exec_command(f"cat {path}")` as root).
    RECORDINGS_ROOT = os.getenv("RECORDINGS_ROOT", "/var/spool/asterisk/monitor/")
    RECORDINGS_SSH_HOST = os.getenv("RECORDINGS_SSH_HOST", "10.10.0.220")
    RECORDINGS_SSH_USER = os.getenv("RECORDINGS_SSH_USER", "root")
    RECORDINGS_SSH_PASSWORD = os.getenv("RECORDINGS_SSH_PASSWORD", "Alitqan@1313")
    RECORDINGS_SSH_KEY = os.getenv("RECORDINGS_SSH_KEY", "/root/.ssh/id_rsa")
    RECORDINGS_SSH_TIMEOUT = _int("RECORDINGS_SSH_TIMEOUT", 10)

    # --- Jobs ---
    JOB_WORKERS = _int("JOB_WORKERS", 4)
    JOB_RESULT_TTL = _int("JOB_RESULT_TTL", 3600)

    # --- Sentry ---
    SENTRY_DSN = os.getenv("SENTRY_DSN") or None
