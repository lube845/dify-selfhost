import logging
from typing import Any, cast

from flask import request
from flask_restx import Resource
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from werkzeug.exceptions import Unauthorized

from constants import HEADER_NAME_APP_CODE
from controllers.common import fields
from controllers.common.schema import register_schema_models
from core.app.app_config.common.parameters_mapping import get_parameters_from_feature_dict
from extensions.ext_database import db
from libs.passport import PassportService
from libs.token import extract_webapp_passport
from models.model import App, AppMode, EndUser
from services.app_access_permission_service import (
    AccessCheckResult,
    AppAccessPermissionService,
)
from services.app_service import AppService
from services.enterprise.enterprise_service import EnterpriseService
from services.feature_service import FeatureService
from services.webapp_auth_service import WebAppAuthService

from . import web_ns
from .error import AppUnavailableError
from .wraps import WebApiResource

logger = logging.getLogger(__name__)


class AppAccessModeQuery(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    app_id: str | None = Field(default=None, alias="appId", description="Application ID")
    app_code: str | None = Field(default=None, alias="appCode", description="Application code")


register_schema_models(web_ns, AppAccessModeQuery)


@web_ns.route("/parameters")
class AppParameterApi(WebApiResource):
    """Resource for app variables."""

    @web_ns.doc("Get App Parameters")
    @web_ns.doc(description="Retrieve the parameters for a specific app.")
    @web_ns.doc(
        responses={
            200: "Success",
            400: "Bad Request",
            401: "Unauthorized",
            403: "Forbidden",
            404: "App Not Found",
            500: "Internal Server Error",
        }
    )
    def get(self, app_model: App, end_user):
        """Retrieve app parameters."""
        if app_model.mode in {AppMode.ADVANCED_CHAT, AppMode.WORKFLOW}:
            workflow = app_model.workflow
            if workflow is None:
                raise AppUnavailableError()

            features_dict: dict[str, Any] = workflow.features_dict
            user_input_form = workflow.user_input_form(to_old_structure=True)
        else:
            app_model_config = app_model.app_model_config
            if app_model_config is None:
                raise AppUnavailableError()

            features_dict = cast(dict[str, Any], app_model_config.to_dict())

            user_input_form = features_dict.get("user_input_form", [])

        parameters = get_parameters_from_feature_dict(features_dict=features_dict, user_input_form=user_input_form)
        return fields.Parameters.model_validate(parameters).model_dump(mode="json")


@web_ns.route("/meta")
class AppMeta(WebApiResource):
    @web_ns.doc("Get App Meta")
    @web_ns.doc(description="Retrieve the metadata for a specific app.")
    @web_ns.doc(
        responses={
            200: "Success",
            400: "Bad Request",
            401: "Unauthorized",
            403: "Forbidden",
            404: "App Not Found",
            500: "Internal Server Error",
        }
    )
    def get(self, app_model: App, end_user):
        """Get app meta"""
        return AppService().get_app_meta(app_model)


@web_ns.route("/webapp/access-mode")
class AppAccessMode(Resource):
    @web_ns.doc("Get App Access Mode")
    @web_ns.doc(description="Retrieve the access mode for a web application (public or restricted).")
    @web_ns.doc(
        params={
            "appId": {"description": "Application ID", "type": "string", "required": False},
            "appCode": {"description": "Application code", "type": "string", "required": False},
        }
    )
    @web_ns.doc(
        responses={
            200: "Success",
            400: "Bad Request",
            500: "Internal Server Error",
        }
    )
    def get(self):
        raw_args = request.args.to_dict()
        args = AppAccessModeQuery.model_validate(raw_args)

        features = FeatureService.get_system_features()
        if not features.webapp_auth.enabled:
            return {"accessMode": "public"}

        app_id = args.app_id
        if args.app_code:
            app_id = AppService.get_app_id_by_code(args.app_code)

        if not app_id:
            raise ValueError("appId or appCode must be provided")

        res = EnterpriseService.WebAppAuth.get_app_access_mode_by_id(app_id)

        return {"accessMode": res.access_mode}


@web_ns.route("/webapp/permission")
class AppWebAuthPermission(Resource):
    @web_ns.doc("Check App Permission")
    @web_ns.doc(description="Check if user has permission to access a web application.")
    @web_ns.doc(params={"appId": {"description": "Application ID", "type": "string", "required": True}})
    @web_ns.doc(
        responses={
            200: "Success",
            400: "Bad Request",
            401: "Unauthorized",
            500: "Internal Server Error",
        }
    )
    def get(self):
        user_id = "visitor"
        app_code = request.headers.get(HEADER_NAME_APP_CODE)
        app_id = request.args.get("appId")
        if not app_id or not app_code:
            raise ValueError("appId must be provided")

        features = FeatureService.get_system_features()
        # The access-mode lookup below lives on the enterprise service
        # (``ENTERPRISE_API_URL``), so it may only be attempted when the
        # enterprise feature is on. The community edition has no such URL
        # configured — ``EnterpriseRequest.base_url`` then falls back to the
        # literal string "ENTERPRISE_API_URL", httpx rejects it for having no
        # protocol, and the whole precheck 500s.
        #
        # Folding ``features.webapp_auth.enabled`` into the value (instead of
        # ANDing it into every consumer) keeps the gate single-sourced: every
        # branch below reads ``require_permission_check`` alone, and the
        # enterprise behaviour is unchanged because the value was already
        # ANDed with that flag at each use site.
        require_permission_check = (
            WebAppAuthService.is_app_require_permission_check(app_id=app_id)
            if features.webapp_auth.enabled
            else False
        )

        # Only resolve the passport when an enterprise check actually needs
        # the user id. Otherwise a missing/expired passport would block
        # anonymous visits to public apps, which the original code at the
        # top of this method avoided by short-circuiting on
        # `not require_permission_check`.
        if require_permission_check:
            try:
                tk = extract_webapp_passport(app_code, request)
                if not tk:
                    raise Unauthorized("Access token is missing.")
                decoded = PassportService().verify(tk)
                user_id = decoded.get("user_id", "visitor")
            except Unauthorized:
                raise
            except Exception:
                logger.exception("Unexpected error during auth verification")
                raise

        # Enterprise webapp_auth gate — preserves prior short-circuit semantics:
        # when the system feature is off, the enterprise check is skipped.
        # ``require_permission_check`` is now False whenever the feature is off
        # (see above), so the extra ``is_app_require_permission_check`` call the
        # old code repeated in here would always be True and only bought a
        # second round-trip to the enterprise API — plus a second chance to
        # 500 on a transient failure. Reuse the cached value instead.
        if require_permission_check:
            allowed = EnterpriseService.WebAppAuth.is_user_allowed_to_access_webapp(str(user_id), app_id)
            if not allowed:
                return {"result": False, "reason": "denied"}

        # Per-app explicit allowlist (independent of webapp_auth). Runs after
        # the enterprise check so that an enterprise rejection still wins,
        # but ALSO runs when webapp_auth is disabled — apps with
        # access_policy='deny_all_explicit' must be honored regardless.
        return AppWebAuthPermission._check_app_access_permission(
            app_id,
            user_id,
            enterprise_identity=require_permission_check,
        )

    @staticmethod
    def _check_app_access_permission(app_id: str, user_id: str, *, enterprise_identity: bool) -> dict[str, Any]:
        """Resolve (app, end_user) and run AppAccessPermissionService.

        The result is mapped to a ``reason`` string so the frontend can render
        distinct UI for each state: ``allowed`` / ``denied`` / ``expired`` and
        ``auth_required`` (the app refuses anonymous visitors — the visitor can
        self-recover by signing in, so the frontend routes them to /oa-login
        instead of the "no permission" page).

        ``enterprise_identity`` tells us the caller already came through the
        enterprise webapp-auth flow, where the user is identified by an SSO
        token rather than the ``oa_session`` cookie; such a caller counts as
        signed in for the per-app anonymous policy.

        Failure modes that fall back to ``{'result': True, 'reason': 'allowed'}``
        rather than 500'ing:
        - No App row (defensive; shouldn't happen in practice — wraps.py will
          reject the actual chat request anyway).
        - No EndUser row for this ``user_id`` (OA workcode, or any identifier
          that hasn't been seen by Dify yet). The strict gate at chat-time in
          ``controllers/web/wraps.py`` will still enforce the allowlist; here
          we just don't pre-emptively 500 the layout.
        """
        _REASON_FROM_RESULT = {
            AccessCheckResult.ALLOWED: "allowed",
            AccessCheckResult.DENIED: "denied",
            AccessCheckResult.EXPIRED: "expired",
            AccessCheckResult.AUTH_REQUIRED: "auth_required",
        }
        try:
            # Lazy import: ``controllers.web.oa_auth`` imports ``controllers.web``.
            from controllers.web.oa_auth import is_oa_authenticated

            signed_in = enterprise_identity or is_oa_authenticated()
            with sessionmaker(bind=db.engine, expire_on_commit=False).begin() as session:
                app_model = session.scalar(select(App).where(App.id == app_id))
                if app_model is None:
                    return {"result": True, "reason": "allowed"}
                if AppAccessPermissionService.requires_login(app=app_model) and not signed_in:
                    return {"result": False, "reason": "auth_required"}
                if app_model.access_policy != "deny_all_explicit":
                    return {"result": True, "reason": "allowed"}
                end_user = session.scalar(select(EndUser).where(EndUser.session_id == user_id))
                if end_user is None:
                    return {"result": True, "reason": "allowed"}
                result = AppAccessPermissionService.check_access_with_reason(
                    app=app_model,
                    end_user=end_user,
                    authenticated=signed_in,
                )
        except Exception:
            logger.exception("AppAccessPermission precheck failed; failing closed")
            return {"result": False, "reason": "denied"}

        return {
            "result": result == AccessCheckResult.ALLOWED,
            "reason": _REASON_FROM_RESULT[result],
        }
