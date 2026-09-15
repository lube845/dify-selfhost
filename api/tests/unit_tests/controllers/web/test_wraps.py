"""Unit tests for the AppAccessPermission checks in ``_validate_user_accessibility``.

Strategy: pass ``app_web_auth_enabled=False, system_webapp_auth_enabled=False`` so the
existing enterprise-webapp-auth branch short-circuits and we only exercise the
per-app checks at the end of the function.

Three layers are covered:

- the ``deny_all_explicit`` / ``allow_all`` allowlist check, by mocking
  ``AppAccessPermissionService.check_access_with_reason``;
- the ``allow_anonymous`` / ``deny_all_explicit`` "must sign in" gate, which
  runs *before* the allowlist check and is driven by the real ``requires_login``
  plus the caller's session liveness;
- ``_is_signed_in_end_user`` itself — the cookie-vs-row semantics that make the
  ``OA_SESSION_EXPIRE_HOURS`` re-login window enforceable.

Session liveness is decided by the ``oa_session`` cookie, which needs a request
context, so ``controllers.web.oa_auth.is_oa_authenticated`` is patched
throughout. The end_user fixtures still set ``_is_anonymous`` explicitly
(True = anonymous row) because the enterprise clause reads it.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from controllers.web.error import (
    AppAccessPermissionDeniedError,
    WebAppLoginRequiredError,
    WebAppPermissionExpiredError,
)
from controllers.web.wraps import _is_signed_in_end_user, _validate_user_accessibility
from services.app_access_permission_service import AccessCheckResult, AppAccessPolicy


def _end_user(session_id: str = "user-123", *, anonymous: bool = False) -> MagicMock:
    u = MagicMock()
    u.session_id = session_id
    # ``_is_anonymous`` is the mapped column; the public ``is_anonymous``
    # property is overridden to always report False, so tests must set the
    # private attribute the same way the model does.
    u._is_anonymous = anonymous
    return u


def _app(
    policy: str = AppAccessPolicy.ALLOW_ALL.value,
    *,
    allow_anonymous: bool = True,
) -> MagicMock:
    a = MagicMock()
    a.id = "app-1"
    a.tenant_id = "tenant-1"
    a.access_policy = policy
    a.allow_anonymous = allow_anonymous
    return a


def _call(
    app: MagicMock,
    end_user: MagicMock,
    *,
    oa_authenticated: bool,
    system_webapp_auth_enabled: bool = False,
) -> None:
    with patch("controllers.web.oa_auth.is_oa_authenticated", return_value=oa_authenticated):
        _validate_user_accessibility(
            decoded={},
            app_code="some-code",
            app_web_auth_enabled=False,
            system_webapp_auth_enabled=system_webapp_auth_enabled,
            webapp_settings=None,
            app_model=app,
            end_user=end_user,
        )


class TestAppAccessPermissionInValidateUserAccessibility:
    """Each test mocks ``AppAccessPermissionService.check_access_with_reason`` directly.

    The caller is signed in (valid ``oa_session``) throughout: these cases are
    about the *allowlist* mapping, not about session liveness.
    """

    def test_allowed_does_not_raise(self) -> None:
        app = _app(AppAccessPolicy.ALLOW_ALL.value)
        end_user = _end_user()

        with patch(
            "controllers.web.wraps.AppAccessPermissionService.check_access_with_reason",
            return_value=AccessCheckResult.ALLOWED,
        ) as mock_check:
            _call(app, end_user, oa_authenticated=True)

        mock_check.assert_called_once_with(app=app, end_user=end_user, authenticated=True)

    def test_deny_all_explicit_with_permission_passes(self) -> None:
        app = _app(AppAccessPolicy.DENY_ALL_EXPLICIT.value)
        end_user = _end_user()

        with patch(
            "controllers.web.wraps.AppAccessPermissionService.check_access_with_reason",
            return_value=AccessCheckResult.ALLOWED,
        ):
            _call(app, end_user, oa_authenticated=True)  # should not raise

    def test_deny_all_explicit_without_permission_raises_denied(self) -> None:
        # User is not on the allowlist at all -> "not authorised" page.
        app = _app(AppAccessPolicy.DENY_ALL_EXPLICIT.value)
        end_user = _end_user()

        with patch(
            "controllers.web.wraps.AppAccessPermissionService.check_access_with_reason",
            return_value=AccessCheckResult.DENIED,
        ):
            with pytest.raises(AppAccessPermissionDeniedError):
                _call(app, end_user, oa_authenticated=True)

    def test_deny_all_explicit_with_expired_permission_raises_expired(self) -> None:
        # User *was* on the allowlist but their row's expires_at is in the past
        # -> "permission expired" page. The user can only recover via admin
        # renewal; this is distinct from the never-granted DENIED case above.
        app = _app(AppAccessPolicy.DENY_ALL_EXPLICIT.value)
        end_user = _end_user()

        with patch(
            "controllers.web.wraps.AppAccessPermissionService.check_access_with_reason",
            return_value=AccessCheckResult.EXPIRED,
        ):
            with pytest.raises(WebAppPermissionExpiredError):
                _call(app, end_user, oa_authenticated=True)

    def test_auth_required_result_raises_login_required(self) -> None:
        # The service can also report AUTH_REQUIRED itself (it is the authority
        # on the policy); the wrapper must map it to the self-recoverable error
        # rather than the "ask an admin" one.
        app = _app(AppAccessPolicy.DENY_ALL_EXPLICIT.value)
        end_user = _end_user()

        with patch(
            "controllers.web.wraps.AppAccessPermissionService.check_access_with_reason",
            return_value=AccessCheckResult.AUTH_REQUIRED,
        ):
            with pytest.raises(WebAppLoginRequiredError):
                _call(app, end_user, oa_authenticated=True)

    def test_check_is_called_for_both_policies(self) -> None:
        """The check fires regardless of policy; the policy is read by the service."""
        for policy in (
            AppAccessPolicy.ALLOW_ALL.value,
            AppAccessPolicy.DENY_ALL_EXPLICIT.value,
        ):
            app = _app(policy)
            end_user = _end_user()

            with patch(
                "controllers.web.wraps.AppAccessPermissionService.check_access_with_reason",
                return_value=AccessCheckResult.ALLOWED,
            ) as mock_check:
                _call(app, end_user, oa_authenticated=True)

            mock_check.assert_called_once_with(app=app, end_user=end_user, authenticated=True)


class TestAnonymousPolicyGate:
    """The "app refuses anonymous visitors" gate, ahead of the allowlist check."""

    def test_anonymous_visitor_rejected_when_allow_anonymous_off(self) -> None:
        app = _app(AppAccessPolicy.ALLOW_ALL.value, allow_anonymous=False)

        with patch(
            "controllers.web.wraps.AppAccessPermissionService.check_access_with_reason",
        ) as mock_check:
            with pytest.raises(WebAppLoginRequiredError):
                _call(app, _end_user(anonymous=True), oa_authenticated=False)

        # Rejected before the allowlist is consulted: there is nothing to look up.
        mock_check.assert_not_called()

    def test_anonymous_visitor_allowed_when_allow_anonymous_on(self) -> None:
        app = _app(AppAccessPolicy.ALLOW_ALL.value, allow_anonymous=True)
        end_user = _end_user(anonymous=True)

        with patch(
            "controllers.web.wraps.AppAccessPermissionService.check_access_with_reason",
            return_value=AccessCheckResult.ALLOWED,
        ) as mock_check:
            _call(app, end_user, oa_authenticated=False)

        # Not signed in -> the service is told so, and ALLOW_ALL still wins.
        mock_check.assert_called_once_with(app=app, end_user=end_user, authenticated=False)

    def test_anonymous_visitor_rejected_for_allowlist_only_app(self) -> None:
        # deny_all_explicit presupposes an identified user: an anonymous random
        # uuid could never match an admin-entered allowlist value.
        app = _app(AppAccessPolicy.DENY_ALL_EXPLICIT.value, allow_anonymous=True)

        with pytest.raises(WebAppLoginRequiredError):
            _call(app, _end_user(session_id="9f1c6a2e-anon", anonymous=True), oa_authenticated=False)

    def test_signed_in_visitor_passes_the_gate(self) -> None:
        app = _app(AppAccessPolicy.ALLOW_ALL.value, allow_anonymous=False)

        with patch(
            "controllers.web.wraps.AppAccessPermissionService.check_access_with_reason",
            return_value=AccessCheckResult.ALLOWED,
        ):
            _call(app, _end_user(session_id="workcode-123"), oa_authenticated=True)  # should not raise

    def test_valid_oa_cookie_rescues_an_anonymous_row(self) -> None:
        # The end_user row predates the visitor's first OA login; the cookie is
        # the authority, so the gate lets them through.
        app = _app(AppAccessPolicy.ALLOW_ALL.value, allow_anonymous=False)

        with patch(
            "controllers.web.wraps.AppAccessPermissionService.check_access_with_reason",
            return_value=AccessCheckResult.ALLOWED,
        ) as mock_check:
            _call(app, _end_user(anonymous=True), oa_authenticated=True)

        assert mock_check.call_args.kwargs["authenticated"] is True

    def test_lapsed_session_rejected_even_for_a_known_end_user(self) -> None:
        """The 8-hour re-login window.

        A non-anonymous ``EndUser`` row is permanent: it is flipped to
        ``_is_anonymous = False`` on the first OA login and never restored. If
        the row alone counted as "signed in", a visitor could keep chatting
        forever on the passport cached in localStorage and
        ``OA_SESSION_EXPIRE_HOURS`` would never fire. Only the live cookie
        counts.
        """
        app = _app(AppAccessPolicy.ALLOW_ALL.value, allow_anonymous=False)
        known_user = _end_user(session_id="workcode-123", anonymous=False)

        with patch(
            "controllers.web.wraps.AppAccessPermissionService.check_access_with_reason",
        ) as mock_check:
            with pytest.raises(WebAppLoginRequiredError):
                _call(app, known_user, oa_authenticated=False)

        mock_check.assert_not_called()


class TestSignedInEndUser:
    """``_is_signed_in_end_user`` — cookie is liveness, row flag is identity."""

    def _call(self, end_user: MagicMock, *, oa_authenticated: bool, enterprise: bool) -> bool:
        with patch("controllers.web.oa_auth.is_oa_authenticated", return_value=oa_authenticated):
            return _is_signed_in_end_user(
                end_user,
                system_webapp_auth_enabled=enterprise,
            )

    def test_valid_cookie_is_signed_in_regardless_of_row(self) -> None:
        assert self._call(_end_user(anonymous=True), oa_authenticated=True, enterprise=False) is True
        assert self._call(_end_user(anonymous=False), oa_authenticated=True, enterprise=False) is True

    def test_non_anonymous_row_without_cookie_is_not_signed_in(self) -> None:
        # Community edition: the row flag never substitutes for the cookie.
        assert self._call(_end_user(anonymous=False), oa_authenticated=False, enterprise=False) is False

    def test_anonymous_row_without_cookie_is_not_signed_in(self) -> None:
        assert self._call(_end_user(anonymous=True), oa_authenticated=False, enterprise=False) is False

    def test_enterprise_keeps_trusting_the_non_anonymous_row(self) -> None:
        # Enterprise SSO / email-code end_users get no oa_session cookie, so the
        # pre-existing row-flag signal is preserved while that feature is on.
        assert self._call(_end_user(anonymous=False), oa_authenticated=False, enterprise=True) is True

    def test_enterprise_anonymous_row_without_cookie_is_not_signed_in(self) -> None:
        # The enterprise SSO flow creates is_anonymous=True rows and is covered
        # by the caller's own ``enterprise_identity`` flag instead.
        assert self._call(_end_user(anonymous=True), oa_authenticated=False, enterprise=True) is False
