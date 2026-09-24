"""Flask extension singletons.

Kept in their own module so `models.py`, `app.py` and the blueprint modules can all
import `db` without importing `app` (which would be a circular import).

Naming note: the historical name for the SocketIO instance is `socketIO` (capital IO);
`socketio` is the new lowercase alias. Both point at the same object.
"""
from flask_compress import Compress
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_migrate import Migrate
from flask_socketio import SocketIO
from flask_sqlalchemy import SQLAlchemy

from core.config import Config

db = SQLAlchemy()
migrate = Migrate()
jwt = JWTManager()
cors = CORS()
compress = Compress()

# async_mode='threading' matches the gunicorn gthread worker (no eventlet/gevent monkey
# patching, which would break psycopg2). ping_timeout/interval reproduce the old values.
socketio = SocketIO(
    cors_allowed_origins=Config.CORS_ORIGINS,
    async_mode="threading",
    ping_timeout=60,
    ping_interval=25,
)

# Backwards-compatible alias used throughout app.py.
socketIO = socketio


def init_app(app):
    """Bind every extension to `app`. Called once from app.py."""
    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    # expose the headers v2 clients read (live-call ETag polling, export file names, replays)
    cors.init_app(app, origins=Config.CORS_ORIGINS,
                  expose_headers=["ETag", "Content-Disposition", "Idempotent-Replayed", "Content-Range", "Accept-Ranges"])
    compress.init_app(app)
    socketio.init_app(app, cors_allowed_origins=Config.CORS_ORIGINS)
    return app


__all__ = [
    "db",
    "migrate",
    "jwt",
    "cors",
    "compress",
    "socketio",
    "socketIO",
    "init_app",
]
