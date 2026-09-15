"""Unit tests for the OA session cookie and its endpoint surface.

The ``OA_SESSION_EXPIRE_HOURS`` window (default 8) is what makes "sign in again
every 8 hours" enforceable, so the expiry behaviour is asserted directly rather
than inferred from the gate tests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask, Response
from werkzeug.exceptions import Unauthorized

from configs import dify_config
from controllers.web.oa_auth import (
    OA_SESSION_COOKIE_NAME,
    OA_SESSION_TOKEN_SOURCE,
    OAmeResource,
    _set_oa_session_cookie,
    decode_oa_session_cookie,
    is_oa_authenticated,
)
from libs.passport import PassportService


def _session_token(
    *,
    workcode: str = "10086",
    name: str = "Zhang San",
    department: str = "Engineering",
    expires_in_hours: float = 8,
    token_source: str = OA_SESSION_TOKEN_SOURCE,
) -> str:
    """Mint an ``oa_session`` JWT, mirroring ``_set_oa_session_cookie``."""
    payload = {
        "iss": "oa_auth",
        "sub": workcode,
        "workcode": workcode,
        "name": name,
        "department": department,
        "token_source": token_source,
        "exp": int((datetime.now(UTC) + timedelta(hours=expires_in_hours)).timestamp()),
    }
    return PassportService().issue(payload)


def _cookie_header(token: str) -> dict[str, str]:
    return {"Cookie": f"{OA_SESSION_COOKIE_NAME}={token}"}


class TestDecodeOASessionCookie:
    def test_live_cookie_decodes_to_its_payload(self, app: Flask) -> None:
        with app.test_request_context("/oa/me", headers=_cookie_header(_session_token())):
            decoded = decode_oa_session_cookie()

        assert decoded is not None
        assert decoded["workcode"] == "10086"
        assert decoded["name"] == "Zhang San"
        assert decoded["department"] == "Engineering"

    def test_expired_cookie_decodes_to_none(self, app: Flask) -> None:
        """The 8-hour window closing must look like "no session" everywhere.

        ``decode_oa_session_cookie`` returning None is what makes the webapp
        gates in ``wraps.py`` / ``passport.py`` / ``app.py`` fall back to
        "anonymous", which in turn raises ``web_app_login_required`` and sends
        the visitor back to /oa-login.
        """
        token = _session_token(expires_in_hours=-1)

        with app.test_request_context("/oa/me", headers=_cookie_header(token)):
            assert decode_oa_session_cookie() is None

    def test_missing_cookie_decodes_to_none(self, app: Flask) -> None:
        with app.test_request_context("/oa/me"):
            assert decode_oa_session_cookie() is None

    def test_foreign_token_source_is_rejected(self, app: Flask) -> None:
        # A passport signed with the same secret but minted for a different
        # purpose must not be accepted as an OA session.
        token = _session_token(token_source="webapp")

        with app.test_request_context("/oa/me", headers=_cookie_header(token)):
            assert decode_oa_session_cookie() is None

    def test_garbage_cookie_decodes_to_none(self, app: Flask) -> None:
        with app.test_request_context("/oa/me", headers=_cookie_header("not-a-jwt")):
            assert decode_oa_session_cookie() is None


class TestIsOAAuthenticated:
    def test_true_for_a_live_cookie(self, app: Flask) -> None:
        with app.test_request_context("/oa/me", headers=_cookie_header(_session_token())):
            assert is_oa_authenticated() is True

    def test_false_for_an_expired_cookie(self, app: Flask) -> None:
        with app.test_request_context("/oa/me", headers=_cookie_header(_session_token(expires_in_hours=-1))):
            assert is_oa_authenticated() is False

    def test_false_when_the_payload_carries_no_workcode(self, app: Flask) -> None:
        token = _session_token(workcode="")

        with app.test_request_context("/oa/me", headers=_cookie_header(token)):
            assert is_oa_authenticated() is False

    def test_false_outside_a_request_context(self) -> None:
        # Background jobs and unit tests have no cookie to read; fail closed
        # instead of raising.
        assert is_oa_authenticated() is False


class TestOAMeEndpoint:
    def test_reports_the_session_user_and_expiry(self, app: Flask) -> None:
        token = _session_token(expires_in_hours=8)

        with app.test_request_context("/oa/me", headers=_cookie_header(token)):
            result = OAmeResource().get()

        assert result["workcode"] == "10086"
        assert result["name"] == "Zhang San"
        assert result["department"] == "Engineering"
        assert result["session_expire_hours"] == dify_config.OA_SESSION_EXPIRE_HOURS

        # ``expires_at`` is an ISO-8601 UTC instant roughly one window away.
        expires_at = datetime.fromisoformat(result["expires_at"])
        delta = expires_at - datetime.now(UTC)
        assert timedelta(hours=7, minutes=55) < delta <= timedelta(hours=8, minutes=1)

    def test_raises_unauthorized_without_a_session(self, app: Flask) -> None:
        with app.test_request_context("/oa/me"):
            with pytest.raises(Unauthorized):
                OAmeResource().get()

    def test_raises_unauthorized_for_an_expired_session(self, app: Flask) -> None:
        with app.test_request_context("/oa/me", headers=_cookie_header(_session_token(expires_in_hours=-1))):
            with pytest.raises(Unauthorized):
                OAmeResource().get()


class TestSessionCookieWindow:
    def test_max_age_matches_the_configured_window(self) -> None:
        response = Response()
        _set_oa_session_cookie(response, {"workcode": "10086", "name": "n", "department": "d"})

        cookie = response.headers.get("Set-Cookie") or ""
        assert cookie.startswith(f"{OA_SESSION_COOKIE_NAME}=")
        assert f"Max-Age={int(dify_config.OA_SESSION_EXPIRE_HOURS * 3600)}" in cookie

    def test_default_window_is_eight_hours(self) -> None:
        # Guards the product requirement: re-login every 8 hours.
        assert dify_config.OA_SESSION_EXPIRE_HOURS == 8
