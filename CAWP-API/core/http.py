"""Uniform error bodies.

The wire contract is unusual and load-bearing: error responses carry HTTP **201** and the
frontend (`BaseService.ts`) treats any 2xx as success, reading `status: false` from the body.
Changing 201 -> 4xx would make axios reject promises that every screen currently swallows,
so `error_response` keeps the caller's status code (201 by default) and only fixes two things:

  * the exception is logged with a full traceback instead of `print(e)`;
  * `str(e)` (raw psycopg/SQLAlchemy text: SQL fragments, table and constraint names)
    is only echoed to the client when DEBUG is on. Otherwise the client gets
    "internal error" and the details stay in the log.
"""
import logging

from flask import jsonify

from core.config import Config

log = logging.getLogger("cawp.http")


def error_response(message, e, status=201):
    """jsonify({"message": message, "status": False, "error": ...}), status"""
    log.exception("%s: %s", message, e)
    return jsonify({
        "message": message,
        "status": False,
        "error": str(e) if Config.DEBUG else "internal error",
    }), status
