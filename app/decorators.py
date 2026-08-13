from functools import wraps

from flask import abort, g, redirect, session, url_for


def _tenant_session_valid() -> bool:
    return bool(g.get("tenant")) and session.get("tenant_id") == g.tenant.id


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in") or not _tenant_session_valid():
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in") or not _tenant_session_valid():
            return redirect(url_for("auth.login"))
        if session.get("role") != "admin":
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def courier_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in") or not _tenant_session_valid():
            return redirect(url_for("auth.login"))
        if session.get("role") != "entregador":
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def platform_admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("platform_admin_id"):
            return redirect(url_for("platform.login"))
        return view(*args, **kwargs)

    return wrapped
