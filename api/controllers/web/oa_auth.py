"""OA webapp chat gate.

Three endpoints, all under ``/api/oa``:
- ``POST /api/oa/login``  — verify OA credentials, issue ``oa_session`` cookie
- ``POST /api/oa/logout`` — clear the cookie
- ``GET  /api/oa/me``     — return the current OA session user + expiry, or 401

The cookie carries an ``exp`` claim and a matching ``Max-Age``, both derived
from ``OA_SESSION_EXPIRE_HOURS`` (default 8). Expiry is enforced on both ends:
``decode_oa_session_cookie`` returns None once the JWT lapses — so the webapp
gates in ``controllers/web/wraps.py`` / ``passport.py`` / ``app.py`` treat the
visitor as anonymous again — and ``/oa/me`` reports ``expires_at`` so the chat
sidebar can show the deadline and bounce the visitor back to ``/oa-login``.

This is intentionally separate from Dify's webapp auth: a successful OA login
lets the visitor open ``/chat/[token]`` (gated by the Next.js middleware), but
does not by itself create a Dify account. The chat wrapper then sends the
OA workcode as ``sys.user_id`` so Dify's existing EndUser / PassportService
machinery logs the conversation under the workcode.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from flask import Response, has_request_context, request
from flask_restx import Resource
from werkzeug.exceptions import BadRequest, Unauthorized

from configs import dify_config
from controllers.web import web_ns
from libs.oa_auth import oa_login_with_password
from libs.passport import PassportService
from libs.token import _cookie_domain, is_secure

OA_SESSION_COOKIE_NAME = "oa_session"
OA_SESSION_TOKEN_SOURCE = "oa_session"


def _set_oa_session_cookie(response: Response, user_info: dict[str, str]) -> None:
    """Sign a short-lived JWT with the OA user info and write it as a cookie."""
    payload = {
        "iss": "oa_auth",
        "sub": user_info["workcode"],
        "workcode": user_info["workcode"],
        "name": user_info.get("name", ""),
        "department": user_info.get("department", ""),
        "token_source": OA_SESSION_TOKEN_SOURCE,
        "exp": int((datetime.now(UTC) + timedelta(hours=dify_config.OA_SESSION_EXPIRE_HOURS)).timestamp()),
    }
    token = PassportService().issue(payload)
    response.set_cookie(
        OA_SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        domain=_cookie_domain(),
        secure=is_secure(),
        samesite="Lax",
        max_age=int(dify_config.OA_SESSION_EXPIRE_HOURS * 3600),
        path="/",
    )


def _clear_oa_session_cookie(response: Response) -> None:
    response.set_cookie(
        OA_SESSION_COOKIE_NAME,
        "",
        expires=0,
        path="/",
        domain=_cookie_domain(),
        secure=is_secure(),
        httponly=True,
        samesite="Lax",
    )


def decode_oa_session_cookie() -> dict[str, Any] | None:
    """Return the decoded OA session payload, or None if missing/invalid."""
    raw = request.cookies.get(OA_SESSION_COOKIE_NAME)
    if not raw:
        return None
    try:
        decoded: dict[str, Any] = PassportService().verify(raw)
    except Unauthorized:
        return None
    if decoded.get("token_source") != OA_SESSION_TOKEN_SOURCE:
        return None
    return decoded


def is_oa_authenticated() -> bool:
    """True when the request carries a usable, signed-in OA session.

    Thin predicate over ``decode_oa_session_cookie`` for the webapp access
    gates (``controllers/web/wraps.py``, ``controllers/web/passport.py``,
    ``controllers/web/app.py``). A cookie that verifies but carries no
    workcode is not a usable identity, so it does not count as signed in.

    Outside a request context (background jobs, unit tests) there is no cookie
    to read; report "not signed in" rather than raising, so callers fail closed
    instead of blowing up with a 500.
    """
    if not has_request_context():
        return False

    decoded = decode_oa_session_cookie()
    return bool(decoded and decoded.get("workcode"))


@web_ns.route("/oa/login")
class OALoginResource(Resource):
    @web_ns.doc("oa_login")
    @web_ns.doc(description="Authenticate with OA account + password. Sets the oa_session cookie on success.")
    @web_ns.doc(
        responses={
            200: "Login successful",
            400: "Missing loginid or password",
            401: "Invalid OA credentials",
            503: "OA integration not configured",
        }
    )
    def post(self):
        # When OA_BASE_URL is empty, the service layer accepts admin/admin123
        # as a test credential so the chat gate can run without a real OA.
        body = request.get_json(silent=True) or {}
        loginid = (body.get("loginid") or "").strip()
        password = body.get("password") or ""
        if not loginid or not password:
            raise BadRequest("loginid and password are required.")

        ok, user_info, message = oa_login_with_password(loginid, password)
        if not ok or user_info is None:
            raise Unauthorized(message or "OA login failed")

        response = Response()
        response.status_code = 200
        response.headers["Content-Type"] = "application/json"
        _set_oa_session_cookie(response, user_info)
        response.set_data(
            '{"ok": true,'
            f' "workcode": "{user_info["workcode"]}",'
            f' "name": "{user_info.get("name", "")}",'
            f' "department": "{user_info.get("department", "")}"'
            "}"
        )
        return response


@web_ns.route("/oa/logout")
class OALogoutResource(Resource):
    @web_ns.doc("oa_logout")
    @web_ns.doc(description="Clear the oa_session cookie.")
    def post(self):
        response = Response()
        response.status_code = 200
        response.headers["Content-Type"] = "application/json"
        _clear_oa_session_cookie(response)
        response.set_data('{"ok": true}')
        return response


def _iso_utc(epoch_seconds: int) -> str:
    """Render a JWT ``exp`` claim as an ISO-8601 UTC string."""
    return datetime.fromtimestamp(epoch_seconds, tz=UTC).isoformat()


@web_ns.route("/oa/me")
class OAmeResource(Resource):
    @web_ns.doc("oa_me")
    @web_ns.doc(description="Return the current OA session user, or 401 if no valid session.")
    @web_ns.doc(
        responses={
            200: "OK",
            401: "No valid OA session",
        }
    )
    def get(self):
        decoded = decode_oa_session_cookie()
        if decoded is None:
            raise Unauthorized("No valid OA session")
        exp = decoded.get("exp")
        return {
            "workcode": decoded.get("workcode", ""),
            "name": decoded.get("name", ""),
            "department": decoded.get("department", ""),
            # ``expires_at`` lets the chat sidebar show until when the sign-in
            # is valid, and lets the client arm a timer so the visitor is sent
            # back to /oa-login the moment the window closes instead of
            # discovering it on their next chat request.
            "expires_at": _iso_utc(int(exp)) if exp else None,
            "session_expire_hours": dify_config.OA_SESSION_EXPIRE_HOURS,
        }


__all__ = [
    "OA_SESSION_COOKIE_NAME",
    "OA_SESSION_TOKEN_SOURCE",
    "decode_oa_session_cookie",
    "is_oa_authenticated",
]
