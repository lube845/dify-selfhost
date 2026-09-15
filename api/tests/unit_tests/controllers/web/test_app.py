"""Unit tests for controllers.web.app endpoints."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from controllers.web.app import AppAccessMode, AppMeta, AppParameterApi, AppWebAuthPermission
from controllers.web.error import AppUnavailableError
from services.app_access_permission_service import AccessCheckResult


# ---------------------------------------------------------------------------
# AppParameterApi
# ---------------------------------------------------------------------------
class TestAppParameterApi:
    def test_advanced_chat_mode_uses_workflow(self, app: Flask) -> None:
        features_dict = {"opening_statement": "Hello"}
        workflow = SimpleNamespace(
            features_dict=features_dict,
            user_input_form=lambda to_old_structure=False: [],
        )
        app_model = SimpleNamespace(mode="advanced-chat", workflow=workflow)

        with (
            app.test_request_context("/parameters"),
            patch("controllers.web.app.get_parameters_from_feature_dict", return_value={}) as mock_params,
            patch("controllers.web.app.fields.Parameters") as mock_fields,
        ):
            mock_fields.model_validate.return_value.model_dump.return_value = {"result": "ok"}
            result = AppParameterApi().get(app_model, SimpleNamespace())

        mock_params.assert_called_once_with(features_dict=features_dict, user_input_form=[])
        assert result == {"result": "ok"}

    def test_workflow_mode_uses_workflow(self, app: Flask) -> None:
        features_dict = {}
        workflow = SimpleNamespace(
            features_dict=features_dict,
            user_input_form=lambda to_old_structure=False: [{"var": "x"}],
        )
        app_model = SimpleNamespace(mode="workflow", workflow=workflow)

        with (
            app.test_request_context("/parameters"),
            patch("controllers.web.app.get_parameters_from_feature_dict", return_value={}) as mock_params,
            patch("controllers.web.app.fields.Parameters") as mock_fields,
        ):
            mock_fields.model_validate.return_value.model_dump.return_value = {}
            AppParameterApi().get(app_model, SimpleNamespace())

        mock_params.assert_called_once_with(features_dict=features_dict, user_input_form=[{"var": "x"}])

    def test_advanced_chat_mode_no_workflow_raises(self, app: Flask) -> None:
        app_model = SimpleNamespace(mode="advanced-chat", workflow=None)
        with app.test_request_context("/parameters"):
            with pytest.raises(AppUnavailableError):
                AppParameterApi().get(app_model, SimpleNamespace())

    def test_standard_mode_uses_app_model_config(self, app: Flask) -> None:
        config = SimpleNamespace(to_dict=lambda: {"user_input_form": [{"var": "y"}], "key": "val"})
        app_model = SimpleNamespace(mode="chat", app_model_config=config)

        with (
            app.test_request_context("/parameters"),
            patch("controllers.web.app.get_parameters_from_feature_dict", return_value={}) as mock_params,
            patch("controllers.web.app.fields.Parameters") as mock_fields,
        ):
            mock_fields.model_validate.return_value.model_dump.return_value = {}
            AppParameterApi().get(app_model, SimpleNamespace())

        call_kwargs = mock_params.call_args
        assert call_kwargs.kwargs["user_input_form"] == [{"var": "y"}]

    def test_standard_mode_no_config_raises(self, app: Flask) -> None:
        app_model = SimpleNamespace(mode="chat", app_model_config=None)
        with app.test_request_context("/parameters"):
            with pytest.raises(AppUnavailableError):
                AppParameterApi().get(app_model, SimpleNamespace())


# ---------------------------------------------------------------------------
# AppMeta
# ---------------------------------------------------------------------------
class TestAppMeta:
    @patch("controllers.web.app.AppService")
    def test_get_returns_meta(self, mock_service_cls: MagicMock, app: Flask) -> None:
        mock_service_cls.return_value.get_app_meta.return_value = {"tool_icons": {}}
        app_model = SimpleNamespace(id="app-1")

        with app.test_request_context("/meta"):
            result = AppMeta().get(app_model, SimpleNamespace())

        assert result == {"tool_icons": {}}


# ---------------------------------------------------------------------------
# AppAccessMode
# ---------------------------------------------------------------------------
class TestAppAccessMode:
    @patch("controllers.web.app.FeatureService.get_system_features")
    def test_returns_public_when_webapp_auth_disabled(self, mock_features: MagicMock, app: Flask) -> None:
        mock_features.return_value = SimpleNamespace(webapp_auth=SimpleNamespace(enabled=False))

        with app.test_request_context("/webapp/access-mode?appId=app-1"):
            result = AppAccessMode().get()

        assert result == {"accessMode": "public"}

    @patch("controllers.web.app.EnterpriseService.WebAppAuth.get_app_access_mode_by_id")
    @patch("controllers.web.app.FeatureService.get_system_features")
    def test_returns_access_mode_with_app_id(
        self, mock_features: MagicMock, mock_access: MagicMock, app: Flask
    ) -> None:
        mock_features.return_value = SimpleNamespace(webapp_auth=SimpleNamespace(enabled=True))
        mock_access.return_value = SimpleNamespace(access_mode="internal")

        with app.test_request_context("/webapp/access-mode?appId=app-1"):
            result = AppAccessMode().get()

        assert result == {"accessMode": "internal"}
        mock_access.assert_called_once_with("app-1")

    @patch("controllers.web.app.AppService.get_app_id_by_code", return_value="resolved-id")
    @patch("controllers.web.app.EnterpriseService.WebAppAuth.get_app_access_mode_by_id")
    @patch("controllers.web.app.FeatureService.get_system_features")
    def test_resolves_app_code_to_id(
        self, mock_features: MagicMock, mock_access: MagicMock, mock_resolve: MagicMock, app: Flask
    ) -> None:
        mock_features.return_value = SimpleNamespace(webapp_auth=SimpleNamespace(enabled=True))
        mock_access.return_value = SimpleNamespace(access_mode="external")

        with app.test_request_context("/webapp/access-mode?appCode=code1"):
            result = AppAccessMode().get()

        mock_resolve.assert_called_once_with("code1")
        mock_access.assert_called_once_with("resolved-id")
        assert result == {"accessMode": "external"}

    @patch("controllers.web.app.FeatureService.get_system_features")
    def test_raises_when_no_app_id_or_code(self, mock_features: MagicMock, app: Flask) -> None:
        mock_features.return_value = SimpleNamespace(webapp_auth=SimpleNamespace(enabled=True))

        with app.test_request_context("/webapp/access-mode"):
            with pytest.raises(ValueError, match="appId or appCode"):
                AppAccessMode().get()


# ---------------------------------------------------------------------------
# AppWebAuthPermission
# ---------------------------------------------------------------------------
class TestAppWebAuthPermission:
    @patch("controllers.web.app.AppWebAuthPermission._check_app_access_permission")
    @patch("controllers.web.app.FeatureService.get_system_features")
    @patch("controllers.web.app.WebAppAuthService.is_app_require_permission_check", return_value=False)
    def test_returns_true_when_no_permission_check_required(
        self,
        mock_check: MagicMock,
        mock_features: MagicMock,
        mock_allowlist: MagicMock,
        app: Flask,
    ) -> None:
        mock_features.return_value = SimpleNamespace(webapp_auth=SimpleNamespace(enabled=True))
        mock_allowlist.return_value = {"result": True, "reason": "allowed"}

        with app.test_request_context("/webapp/permission?appId=app-1", headers={"X-App-Code": "code1"}):
            result = AppWebAuthPermission().get()

        assert result == {"result": True, "reason": "allowed"}
        mock_allowlist.assert_called_once_with("app-1", "visitor", enterprise_identity=False)

    def test_raises_when_missing_app_id(self, app: Flask) -> None:
        with app.test_request_context("/webapp/permission", headers={"X-App-Code": "code1"}):
            with pytest.raises(ValueError, match="appId"):
                AppWebAuthPermission().get()

    @patch("controllers.web.app.AppWebAuthPermission._check_app_access_permission")
    @patch("controllers.web.app.FeatureService.get_system_features")
    @patch("controllers.web.app.WebAppAuthService.is_app_require_permission_check", return_value=False)
    def test_short_circuits_allowlist_three_states(
        self,
        mock_check: MagicMock,
        mock_features: MagicMock,
        mock_allowlist: MagicMock,
        app: Flask,
    ) -> None:
        """The static-method helper is the single source of truth for reason.

        Four cases: ALLOWED, DENIED, EXPIRED and AUTH_REQUIRED. The controller
        passes them through verbatim, so a single parametrized-style test is
        enough.
        """
        mock_features.return_value = SimpleNamespace(webapp_auth=SimpleNamespace(enabled=False))

        for reason, result_flag in [
            ("allowed", True),
            ("denied", False),
            ("expired", False),
            ("auth_required", False),
        ]:
            mock_allowlist.return_value = {"result": result_flag, "reason": reason}
            with app.test_request_context(
                "/webapp/permission?appId=app-1", headers={"X-App-Code": "code1"}
            ):
                result = AppWebAuthPermission().get()
            assert result == {"result": result_flag, "reason": reason}, (
                f"reason {reason} should pass through"
            )

    @patch("controllers.web.app.AppWebAuthPermission._check_app_access_permission")
    @patch("controllers.web.app.FeatureService.get_system_features")
    @patch(
        "controllers.web.app.WebAppAuthService.is_app_require_permission_check",
        side_effect=AssertionError("community edition must not hit the enterprise access-mode API"),
    )
    def test_community_edition_never_calls_enterprise_access_mode(
        self,
        mock_check: MagicMock,
        mock_features: MagicMock,
        mock_allowlist: MagicMock,
        app: Flask,
    ) -> None:
        """The access-mode lookup must stay behind ``webapp_auth.enabled``.

        ``EnterpriseRequest.base_url`` falls back to the literal string
        "ENTERPRISE_API_URL" when the env var is unset, so on a community
        edition deployment (where it is never set) httpx raises
        ``UnsupportedProtocol``. Unguarded, that 500s the precheck and the
        share layout renders "应用不可用" on every ``/chat/<code>`` page — the
        share layout probes this endpoint unconditionally.
        """
        mock_features.return_value = SimpleNamespace(webapp_auth=SimpleNamespace(enabled=False))
        mock_allowlist.return_value = {"result": False, "reason": "auth_required"}

        with app.test_request_context("/webapp/permission?appId=app-1", headers={"X-App-Code": "code1"}):
            result = AppWebAuthPermission().get()

        # The enterprise service was never consulted…
        mock_check.assert_not_called()
        # …and the per-app anonymous policy still answers on its own.
        assert result == {"result": False, "reason": "auth_required"}
        mock_allowlist.assert_called_once_with("app-1", "visitor", enterprise_identity=False)

    @patch("controllers.web.app.EnterpriseService.WebAppAuth.is_user_allowed_to_access_webapp", return_value=False)
    @patch("controllers.web.app.AppWebAuthPermission._check_app_access_permission")
    @patch("controllers.web.app.PassportService")
    @patch("controllers.web.app.extract_webapp_passport", return_value="tk")
    @patch("controllers.web.app.FeatureService.get_system_features")
    @patch("controllers.web.app.WebAppAuthService.is_app_require_permission_check", return_value=True)
    def test_enterprise_denial_wins_and_access_mode_is_looked_up_once(
        self,
        mock_check: MagicMock,
        mock_features: MagicMock,
        mock_passport: MagicMock,
        mock_passport_cls: MagicMock,
        mock_allowlist: MagicMock,
        mock_allowed: MagicMock,
        app: Flask,
    ) -> None:
        """An enterprise rejection still short-circuits the per-app allowlist.

        Also locks in the de-duplication: the old code called
        ``is_app_require_permission_check`` a second time inside the gate, which
        could only ever be True there and cost a redundant enterprise
        round-trip. The value is now computed once per request.
        """
        mock_features.return_value = SimpleNamespace(webapp_auth=SimpleNamespace(enabled=True))
        mock_passport_cls.return_value.verify.return_value = {"user_id": "u1"}

        with app.test_request_context("/webapp/permission?appId=app-1", headers={"X-App-Code": "code1"}):
            result = AppWebAuthPermission().get()

        assert result == {"result": False, "reason": "denied"}
        mock_allowlist.assert_not_called()
        mock_allowed.assert_called_once_with("u1", "app-1")
        mock_check.assert_called_once_with(app_id="app-1")


# ---------------------------------------------------------------------------
# AppWebAuthPermission._check_app_access_permission (static helper)
# ---------------------------------------------------------------------------
class TestCheckAppAccessPermission:
    """Exercise the three policy branches of the static allowlist helper.

    The helper opens its own SQLAlchemy session; we mock ``db.engine`` and
    ``sessionmaker`` so the helper sees a fake session that returns whatever
    App / EndUser the test wants. The service call itself is mocked to
    return the desired AccessCheckResult.
    """

    @staticmethod
    def _fake_session(app_obj, end_user_obj):
        """Build a session-like object whose ``scalar()`` returns model objects
        in the order the helper calls them: App first, EndUser second.
        """
        session = MagicMock()
        scalars = [app_obj, end_user_obj]
        session.scalar.side_effect = scalars
        return session

    @staticmethod
    def _fake_sessionmaker(session):
        """Build a stand-in for ``sessionmaker(bind=..., expire_on_commit=False)``.

        The controller does::

            with sessionmaker(bind=db.engine, expire_on_commit=False).begin() as session:
                ...

        so we need an object whose ``.begin()`` is a context manager yielding
        the given ``session``.
        """
        sm = MagicMock()
        sm.begin.return_value = _TestCheckAppAccessPermissionCtx(session)
        return sm

    @staticmethod
    def _patch_sessionmaker(session):
        """Patch ``sessionmaker``, ``db`` and the request-scoped OA identity.

        ``db.engine`` is a property descriptor that requires an active Flask
        app context, so we cannot set it directly. Patching the whole ``db``
        object to a SimpleNamespace with a sentinel ``engine`` attribute is
        the simplest way to keep ``sessionmaker(bind=db.engine, ...)`` happy
        inside a test that has no Flask-SQLAlchemy app registered.

        ``is_oa_authenticated`` reads the ``oa_session`` cookie, so it needs a
        request context these tests do not have. Pinned to True so the tests
        exercise the allowlist mapping for an identified caller; the tests that
        cover the anonymous path re-patch it to False.
        """
        from contextlib import ExitStack

        fake_db = SimpleNamespace(engine=object())

        stack = ExitStack()
        stack.enter_context(
            patch(
                "controllers.web.app.sessionmaker",
                return_value=TestCheckAppAccessPermission._fake_sessionmaker(session),
            )
        )
        stack.enter_context(patch("controllers.web.app.db", new=fake_db))
        stack.enter_context(patch("controllers.web.oa_auth.is_oa_authenticated", return_value=True))
        return stack

    @staticmethod
    def _make_app(policy: str, *, allow_anonymous: bool = True) -> SimpleNamespace:
        return SimpleNamespace(id="app-1", access_policy=policy, allow_anonymous=allow_anonymous)

    @staticmethod
    def _make_end_user(session_id: str = "workcode-1") -> SimpleNamespace:
        return SimpleNamespace(session_id=session_id)

    @patch("controllers.web.app.AppAccessPermissionService.check_access_with_reason")
    def test_allow_all_policy_short_circuits_to_allowed(
        self, mock_check: MagicMock
    ) -> None:
        session = self._fake_session(app_obj=self._make_app("allow_all"), end_user_obj=None)
        with self._patch_sessionmaker(session):
            result = AppWebAuthPermission._check_app_access_permission(
                "app-1", "workcode-1", enterprise_identity=False
            )

        assert result == {"result": True, "reason": "allowed"}
        mock_check.assert_not_called()

    def test_allow_all_with_anonymous_off_requires_login(self) -> None:
        # Second level of the policy: default access is on, but the app owner
        # turned anonymous visitors off. The precheck reports `auth_required`
        # so the client routes the visitor to /oa-login instead of the
        # "no permission" page (which would blame the wrong problem).
        session = self._fake_session(
            app_obj=self._make_app("allow_all", allow_anonymous=False), end_user_obj=None
        )
        with (
            self._patch_sessionmaker(session),
            patch("controllers.web.oa_auth.is_oa_authenticated", return_value=False),
        ):
            result = AppWebAuthPermission._check_app_access_permission(
                "app-1", "workcode-1", enterprise_identity=False
            )

        assert result == {"result": False, "reason": "auth_required"}

    def test_anonymous_off_but_signed_in_is_allowed(self) -> None:
        session = self._fake_session(
            app_obj=self._make_app("allow_all", allow_anonymous=False), end_user_obj=None
        )
        with (
            self._patch_sessionmaker(session),
            patch("controllers.web.oa_auth.is_oa_authenticated", return_value=True),
        ):
            result = AppWebAuthPermission._check_app_access_permission(
                "app-1", "workcode-1", enterprise_identity=False
            )

        assert result == {"result": True, "reason": "allowed"}

    def test_enterprise_identity_skips_the_anonymous_gate(self) -> None:
        # Enterprise SSO callers carry a token instead of an oa_session cookie,
        # so the cookie-based gate must not lock them out.
        session = self._fake_session(
            app_obj=self._make_app("allow_all", allow_anonymous=False), end_user_obj=None
        )
        with (
            self._patch_sessionmaker(session),
            patch("controllers.web.oa_auth.is_oa_authenticated", return_value=False),
        ):
            result = AppWebAuthPermission._check_app_access_permission(
                "app-1", "workcode-1", enterprise_identity=True
            )

        assert result == {"result": True, "reason": "allowed"}

    @patch("controllers.web.app.AppAccessPermissionService.check_access_with_reason")
    def test_deny_all_explicit_without_end_user_falls_back_to_allowed(
        self, mock_check: MagicMock
    ) -> None:
        # OA workcode without a pre-existing end_users row shouldn't break
        # the layout precheck — wraps.py at chat-time is the strict gate.
        session = self._fake_session(
            app_obj=self._make_app("deny_all_explicit"), end_user_obj=None
        )
        with self._patch_sessionmaker(session):
            result = AppWebAuthPermission._check_app_access_permission(
                "app-1", "workcode-1", enterprise_identity=False
            )

        assert result == {"result": True, "reason": "allowed"}
        mock_check.assert_not_called()

    def test_deny_all_explicit_without_row_returns_denied(self) -> None:
        end_user = self._make_end_user()
        session = self._fake_session(
            app_obj=self._make_app("deny_all_explicit"), end_user_obj=end_user
        )
        with (
            self._patch_sessionmaker(session),
            patch(
                "controllers.web.app.AppAccessPermissionService.check_access_with_reason",
                return_value=AccessCheckResult.DENIED,
            ),
        ):
            result = AppWebAuthPermission._check_app_access_permission(
                "app-1", "workcode-1", enterprise_identity=False
            )

        assert result == {"result": False, "reason": "denied"}

    def test_deny_all_explicit_with_expired_row_returns_expired(self) -> None:
        end_user = self._make_end_user()
        session = self._fake_session(
            app_obj=self._make_app("deny_all_explicit"), end_user_obj=end_user
        )
        with (
            self._patch_sessionmaker(session),
            patch(
                "controllers.web.app.AppAccessPermissionService.check_access_with_reason",
                return_value=AccessCheckResult.EXPIRED,
            ),
        ):
            result = AppWebAuthPermission._check_app_access_permission(
                "app-1", "workcode-1", enterprise_identity=False
            )

        assert result == {"result": False, "reason": "expired"}

    def test_deny_all_explicit_with_active_row_returns_allowed(self) -> None:
        end_user = self._make_end_user()
        session = self._fake_session(
            app_obj=self._make_app("deny_all_explicit"), end_user_obj=end_user
        )
        with (
            self._patch_sessionmaker(session),
            patch(
                "controllers.web.app.AppAccessPermissionService.check_access_with_reason",
                return_value=AccessCheckResult.ALLOWED,
            ),
        ):
            result = AppWebAuthPermission._check_app_access_permission(
                "app-1", "workcode-1", enterprise_identity=False
            )

        assert result == {"result": True, "reason": "allowed"}

    def test_missing_app_falls_back_to_allowed(self) -> None:
        session = self._fake_session(app_obj=None, end_user_obj=None)
        with (
            self._patch_sessionmaker(session),
            patch(
                "controllers.web.app.AppAccessPermissionService.check_access_with_reason",
            ) as mock_check,
        ):
            result = AppWebAuthPermission._check_app_access_permission(
                "missing", "workcode-1", enterprise_identity=False
            )

        assert result == {"result": True, "reason": "allowed"}
        mock_check.assert_not_called()

    def test_db_failure_fails_closed_to_denied(self) -> None:
        @contextmanager
        def raising_sessionmaker(*_args, **_kwargs):
            raise RuntimeError("db down")
            yield  # pragma: no cover  -- unreachable, makes this a generator

        with patch(
            "controllers.web.app.sessionmaker",
            side_effect=raising_sessionmaker,
        ):
            result = AppWebAuthPermission._check_app_access_permission(
                "app-1", "workcode-1", enterprise_identity=False
            )

        assert result == {"result": False, "reason": "denied"}


class _TestCheckAppAccessPermissionCtx:
    """Context manager returned by ``sessionmaker().begin()`` in the helper."""

    def __init__(self, session):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, exc_type, exc, tb):
        return False
