"""
SmartPoli - "Delete my account" from inside the app.

The deletion order (children first, one transaction, nothing of anyone else's removed) is tools/delete_user.py, already
tested; this runs that same function on the request's own database transaction so it is all-or-nothing there too. It is
loaded by file path so a package called `tools` installed elsewhere can never be picked up by mistake.
"""

import importlib.util
import os

from sqlalchemy.orm import Session

_tool = None


def _load():
    global _tool
    if _tool is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "delete_user.py")
        spec = importlib.util.spec_from_file_location("smartpoli_delete_user_tool", path)
        _tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_tool)
    return _tool


def delete_account(db: Session, user_id: int) -> dict:
    """Remove the user and all their data in this session's transaction (commits on success, rolls back on any error)."""
    try:
        deleted = _load().delete_user(db.connection(), int(user_id))
        db.commit()
        return deleted
    except Exception:
        db.rollback()
        raise
