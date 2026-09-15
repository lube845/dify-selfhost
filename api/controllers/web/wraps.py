from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps
from typing import Concatenate

from flask import request
from flask_restx import Resource
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from werkzeug.exceptions import BadRequest, NotFound, Unauthorized

from constants import HEADER_NAME_APP_CODE
from controllers.web.error import (
    AppAccessPermissionDeniedError,
    WebAppAuthAccessDeniedError,
    WebAppAuthRequiredError,
    WebAppLoginRequiredError,
    WebAppPermissionExpiredError,
)
from extensions.ext_database import db
from libs.passport import PassportService
from libs.token import extract_webapp_passport
from models.model import App, EndUser, Site
from services.app_access_permission_service import AccessCheckResult, AppAccessPermissionService
from services.app_service import AppService
from services.enterprise.enterprise_service import EnterpriseService, WebAppSettings
from services.feature_service import FeatureService
from services.webapp_auth_service import WebAppAuthService


def validate_jwt_token[**P, R](
    view: Callable[Concatenate[App, EndUser, P], R] | None = None,
) -> Callable[P, R] | Callable[[Callable[Concatenate[App, EndUser, P], R]], Callable[P, R]]:
    def decorator(view: Callable[Concatenate[App, EndUser, P], R]) -> Callable[P, R]:
        @wraps(view)
        def decorated(*args: P.args, **kwargs: P.kwargs) -> R:
            app_model, end_user = decode_jwt_token()
            return view(app_model, end_user, *args, **kwargs)

        return decorated

    if view:
        return decorator(view)
    return decorator


def decode_jwt_token(app_code: str | None = None, user_id: str | None = None) -> tuple[App, EndUser]:
    system_features = FeatureService.get_system_features()
    if not app_code:
        app_code = str(request.headers.get(HEADER_NAME_APP_CODE))
    try:
        tk = extract_webapp_passport(app_code, request)
        if not tk:
            raise Unauthorized("App token is missing.")
        decoded = PassportService().verify(tk)
        app_code = decoded.get("app_code")
        app_id = decoded.get("app_id")
        with sessionmaker(db.engine, expire_on_commit=False).begin() as session:
            app_model = session.scalar(select(App).where(App.id == app_id))
            site = session.scalar(select(Site).where(Site.code == app_code))
            if not app_model:
                raise NotFound()
            if not app_code or not site:
                raise BadRequest("Site URL is no longer valid.")
            if app_model.enable_site is False:
                raise BadRequest("Site is disabled.")
            end_user_id = decoded.get("end_user_id")
            end_user = session.scalar(select(EndUser).where(EndUser.id == end_user_id))
            if not end_user:
                raise NotFound()

            # Validate user_id against end_user's session_id if provided
            if user_id is not None and end_user.session_id != user_id:
                raise Unauthorized("Authentication has expired.")

        # for enterprise webapp auth
        app_web_auth_enabled = False
        webapp_settings = None
        if system_features.webapp_auth.enabled:
            app_id = AppService.get_app_id_by_code(app_code)
            webapp_settings = EnterpriseService.WebAppAuth.get_app_access_mode_by_id(app_id)
            if not webapp_settings:
                raise NotFound("Web app settings not found.")
            app_web_auth_enabled = webapp_settings.access_mode != "public"

        _validate_webapp_token(decoded, app_web_auth_enabled, system_features.webapp_auth.enabled)
        _validate_user_accessibility(
            decoded,
            app_code,
            app_web_auth_enabled,
            system_features.webapp_auth.enabled,
            webapp_settings,
            app_model=app_model,
            end_user=end_user,
        )

        return app_model, end_user
    except Unauthorized as e:
        if system_features.webapp_auth.enabled:
            if not app_code:
                raise Unauthorized("Please re-login to access the web app.")
            app_id = AppService.get_app_id_by_code(app_code)
            app_web_auth_enabled = (
                EnterpriseService.WebAppAuth.get_app_access_mode_by_id(app_id=app_id).access_mode != "public"
            )
            if app_web_auth_enabled:
                raise WebAppAuthRequiredError()

        raise Unauthorized(e.description)


def _validate_webapp_token(decoded, app_web_auth_enabled: bool, system_webapp_auth_enabled: bool):
    # Check if authentication is enforced for web app, and if the token source is not webapp,
    # raise an error and redirect to login
    if system_webapp_auth_enabled and app_web_auth_enabled:
        source = decoded.get("token_source")
        if not source or source != "webapp":
            raise WebAppAuthRequiredError()

    # Check if authentication is not enforced for web, and if the token source is webapp,
    # raise an error and redirect to normal passport login
    if not system_webapp_auth_enabled or not app_web_auth_enabled:
        source = decoded.get("token_source")
        if source and source == "webapp":
            raise Unauthorized("webapp token expired.")


def _is_signed_in_end_user(end_user: EndUser, *, system_webapp_auth_enabled: bool) -> bool:
    """True when the webapp request carries a live signed-in identity.

    The ``oa_session`` cookie is the **liveness** signal for the OA flow: its
    ``OA_SESSION_EXPIRE_HOURS`` window (8h by default) is what makes "re-login
    every 8 hours" enforceable. The ``EndUser`` row is deliberately NOT trusted
    on its own — ``_is_anonymous`` is flipped to False on the first OA login and
    never restored, so a visitor could otherwise keep chatting indefinitely on a
    passport cached in localStorage long after the cookie lapsed. That is the
    difference between "this browser is someone we know" (a permanent row
    attribute) and "this browser is signed in right now" (the cookie).

    (``_is_anonymous`` is the mapped column — the ``is_anonymous`` property on
    the model is overridden to always report False, so it cannot be used for
    this decision.)

    The enterprise edition is the exception: with ``webapp_auth`` enabled the
    identity arrives as an SSO / email-code token instead of a cookie, and those
    end_users are minted with ``is_anonymous=False`` and never receive an
    ``oa_session``. A non-anonymous row is therefore still honoured, but only
    while that feature is driving identity — so community-edition behaviour is
    unaffected by this clause.

    The enterprise SSO flow that does set ``is_anonymous=True`` is covered by
    the caller's own ``enterprise_identity`` flag, which it ORs with this result.
    """
    # Lazy import: ``controllers.web.oa_auth`` imports ``controllers.web``,
    # which imports this module.
    from controllers.web.oa_auth import is_oa_authenticated

    if is_oa_authenticated():
        return True

    return system_webapp_auth_enabled and not end_user._is_anonymous


def _validate_user_accessibility(
    decoded,
    app_code,
    app_web_auth_enabled: bool,
    system_webapp_auth_enabled: bool,
    webapp_settings: WebAppSettings | None,
    *,
    app_model: App,
    end_user: EndUser,
):
    if system_webapp_auth_enabled and app_web_auth_enabled:
        # Check if the user is allowed to access the web app
        user_id = decoded.get("user_id")
        if not user_id:
            raise WebAppAuthRequiredError()

        if not webapp_settings:
            raise WebAppAuthRequiredError("Web app settings not found.")

        if WebAppAuthService.is_app_require_permission_check(access_mode=webapp_settings.access_mode):
            app_id = AppService.get_app_id_by_code(app_code)
            if not EnterpriseService.WebAppAuth.is_user_allowed_to_access_webapp(user_id, app_id):
                raise WebAppAuthAccessDeniedError()

        auth_type = decoded.get("auth_type")
        granted_at = decoded.get("granted_at")
        if not auth_type:
            raise WebAppAuthRequiredError("Missing auth_type in the token.")
        if not granted_at:
            raise WebAppAuthRequiredError("Missing granted_at in the token.")
        # check if sso has been updated — the user *was* granted access, but
        # their token is now stale relative to the current SSO config. Surface
        # as "re-auth required" (frontend: "权限已过期" page) rather than the
        # generic "not authorised" page, because the user can self-recover
        # by signing in again.
        if auth_type == "external":
            last_update_time = EnterpriseService.get_app_sso_settings_last_update_time()
            if granted_at and datetime.fromtimestamp(granted_at, tz=UTC) < last_update_time:
                raise WebAppAuthRequiredError("SSO settings have been updated. Please re-login.")
        elif auth_type == "internal":
            last_update_time = EnterpriseService.get_workspace_sso_settings_last_update_time()
            if granted_at and datetime.fromtimestamp(granted_at, tz=UTC) < last_update_time:
                raise WebAppAuthRequiredError("SSO settings have been updated. Please re-login.")

    # Per-app anonymous policy. Kept ahead of the allowlist check because the
    # two failure modes have different remedies: "you are not signed in" is
    # self-service (go to /oa-login), "you are not on the allowlist" needs an
    # admin.
    #
    # While the enterprise webapp-auth flow is in charge the identity comes
    # from the SSO token instead of an ``oa_session`` cookie, and its end_users
    # are rows created with ``is_anonymous=True``; treat them as signed in so
    # the policy neither locks them out nor changes their allowlist behaviour.
    enterprise_identity = system_webapp_auth_enabled and app_web_auth_enabled
    signed_in = enterprise_identity or _is_signed_in_end_user(
        end_user,
        system_webapp_auth_enabled=system_webapp_auth_enabled,
    )

    if AppAccessPermissionService.requires_login(app=app_model) and not signed_in:
        raise WebAppLoginRequiredError()

    # Per-app explicit allowlist (independent of the enterprise webapp-auth flow above).
    # Distinguishing EXPIRED from DENIED lets the webapp gate tell the user
    # "your permission has expired, contact admin to renew" rather than the
    # generic "you are not authorised" — the latter is wrong for users who
    # *were* granted access and whose row just lapsed.
    result = AppAccessPermissionService.check_access_with_reason(
        app=app_model,
        end_user=end_user,
        authenticated=signed_in,
    )
    if result == AccessCheckResult.EXPIRED:
        raise WebAppPermissionExpiredError()
    if result == AccessCheckResult.AUTH_REQUIRED:
        raise WebAppLoginRequiredError()
    if result != AccessCheckResult.ALLOWED:
        raise AppAccessPermissionDeniedError()


class WebApiResource(Resource):
    method_decorators = [validate_jwt_token]
