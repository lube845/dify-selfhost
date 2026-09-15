"""Console API for per-app access permissions.

Endpoints are tenant-scoped via ``current_account_with_tenant()``. Whitelist
CRUD talks to ``AppAccessPermission`` directly (this is admin tooling, not
the chat hot path). The controls on ``/permissions/apps/<app_id>`` mutate
``App.access_policy`` (default-allow vs explicit-deny) and
``App.allow_anonymous`` (whether a visitor with no signed-in identity may
chat). ``allow_anonymous`` only matters while ``access_policy`` is
``allow_all`` — under ``deny_all_explicit`` the visitor must sign in *and*
hold an allowlist row, so anonymous access is impossible either way.

``expires_at`` is a calendar date (no time, no timezone) — the picked day is
the **last** day access is granted. Storing a ``datetime`` here used to round
badly across date-picker serialization and server timezones, which led to the
displayed date shifting to the day after the user picked. Keeping it a pure
``date`` makes the read/write path timezone-proof.

Pairing
-------
Frontend at ``web/app/(commonLayout)/permissions/page.tsx`` and
``web/service/permissions.ts``. Access checks live in
``services.app_access_permission_service.AppAccessPermissionService.check_access``
(uses ``date.today() >= expires_at``, so the picked day itself is still valid)
and are enforced on the webapp side in ``controllers/web/wraps.py`` (chat
requests), ``controllers/web/passport.py`` (passport issuance) and
``controllers/web/app.py`` (``/webapp/permission`` precheck).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from flask_restx import Resource
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from werkzeug.exceptions import BadRequest, NotFound

from controllers.common.schema import register_schema_models
from extensions.ext_database import db
from fields.base import ResponseModel
from libs.login import current_account_with_tenant, login_required
from models.model import App, AppAccessPermission
from services.app_access_permission_service import AppAccessPermissionService

from . import console_ns
from .wraps import account_initialization_required, setup_required

# --- Request payloads ---------------------------------------------------------


class _AppPermissionUpdatePayload(BaseModel):
    """Partial update of an app's access policy.

    Both fields are optional so the console can flip one switch without having
    to echo the other one back; at least one must be present. Unknown / empty
    payloads are rejected rather than silently no-opped.
    """

    access_policy: Literal["allow_all", "deny_all_explicit"] | None = None
    allow_anonymous: bool | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> _AppPermissionUpdatePayload:
        if not self.model_fields_set:
            raise ValueError("At least one of `access_policy` / `allow_anonymous` must be provided.")
        return self


class _WhitelistCreatePayload(BaseModel):
    user_ids: list[str] = Field(min_length=1)
    expires_at: date | None = None

    @field_validator("user_ids")
    @classmethod
    def _validate_user_ids(cls, v: list[str]) -> list[str]:
        # Per-item length check + trim. Service does the dedup + skip-existing
        # pass; we only enforce shape here.
        for u in v:
            stripped = u.strip()
            if not stripped:
                raise ValueError("user_ids cannot contain empty strings")
            if len(stripped) > 255:
                raise ValueError(f"user_id exceeds max length 255: {stripped!r}")
        return [u.strip() for u in v]


class _WhitelistUpdatePayload(BaseModel):
    expires_at: date | None = Field(default=None)

    @model_validator(mode="after")
    def _expires_at_required(self) -> _WhitelistUpdatePayload:
        # Pydantic treats an empty Pydantic model as valid (no-op update). Catch
        # that here so we don't silently no-op when the client sends `{}`.
        if "expires_at" not in self.model_fields_set:
            raise ValueError("`expires_at` is required (use null to clear).")
        return self


# --- Response shapes ----------------------------------------------------------


class _PermissionAppItem(ResponseModel):
    id: str
    name: str
    access_policy: str
    allow_anonymous: bool


class _PermissionAppList(ResponseModel):
    data: list[_PermissionAppItem]


class _WhitelistEntryItem(ResponseModel):
    id: str
    app_id: str
    user_id: str
    expires_at: date | None = None
    created_at: datetime
    updated_at: datetime


class _WhitelistEntryList(ResponseModel):
    data: list[_WhitelistEntryItem]


class _WhitelistCreateResult(ResponseModel):
    data: list[_WhitelistEntryItem]
    # user_ids the caller submitted that were already whitelisted and were
    # therefore skipped. Lets the UI surface a partial-success message.
    skipped: list[str]


class _ResultResponse(ResponseModel):
    result: Literal["success"] = "success"


register_schema_models(
    console_ns,
    _AppPermissionUpdatePayload,
    _WhitelistCreatePayload,
    _WhitelistUpdatePayload,
    _PermissionAppItem,
    _PermissionAppList,
    _WhitelistEntryItem,
    _WhitelistEntryList,
    _WhitelistCreateResult,
    _ResultResponse,
)


# --- Helpers ------------------------------------------------------------------


def _ensure_future(expires_at: date | None) -> None:
    """Reject ``expires_at`` values strictly before today; allow ``None`` (never expires)."""
    if expires_at is None:
        return
    if expires_at < date.today():
        raise BadRequest("expires_at must be today or later (or null).")


def _load_tenant_app(app_id: str, tenant_id: str) -> App:
    """Return the app if it belongs to ``tenant_id``, else 404."""
    with sessionmaker(bind=db.engine, expire_on_commit=False).begin() as session:
        app = session.scalar(select(App).where(App.id == app_id, App.tenant_id == tenant_id))
    if app is None:
        raise NotFound("App not found.")
    return app


# --- Endpoints ----------------------------------------------------------------


@console_ns.route("/permissions/apps")
class PermissionAppListApi(Resource):
    """List all apps in the current tenant with their access policy."""

    method_decorators = [setup_required, login_required, account_initialization_required]

    def get(self):
        _, current_tenant_id = current_account_with_tenant()
        with sessionmaker(bind=db.engine, expire_on_commit=False).begin() as session:
            rows = session.execute(
                select(App.id, App.name, App.access_policy, App.allow_anonymous)
                .where(App.tenant_id == current_tenant_id)
                .order_by(App.created_at.desc())
            ).all()
        items = [
            _PermissionAppItem(
                id=app_id,
                name=app_name,
                access_policy=access_policy,
                allow_anonymous=allow_anonymous,
            )
            for app_id, app_name, access_policy, allow_anonymous in rows
        ]
        return _PermissionAppList(data=items).model_dump(mode="json")


@console_ns.route("/permissions/apps/<uuid:app_id>")
class PermissionAppApi(Resource):
    """Update an app's access policy (default-allow vs explicit-deny, and
    whether anonymous visitors may chat)."""

    method_decorators = [setup_required, login_required, account_initialization_required]

    def patch(self, app_id):
        _, current_tenant_id = current_account_with_tenant()
        payload = _AppPermissionUpdatePayload.model_validate(console_ns.payload or {})

        _load_tenant_app(str(app_id), current_tenant_id)

        with sessionmaker(bind=db.engine, expire_on_commit=False).begin() as session:
            app_row = session.get(App, str(app_id))
            # _load_tenant_app already checked tenant ownership. Re-read here
            # because we need a *write* session; without it access_policy
            # assignment is on a detached instance.
            # No-op guard: skip the UPDATE (and the consequent `updated_at`
            # bump from `onupdate=current_timestamp`) when the policy is
            # already in the requested state.
            changed = False
            if payload.access_policy is not None and app_row.access_policy != payload.access_policy:
                app_row.access_policy = payload.access_policy
                changed = True
            if payload.allow_anonymous is not None and app_row.allow_anonymous != payload.allow_anonymous:
                app_row.allow_anonymous = payload.allow_anonymous
                changed = True
            if changed:
                session.flush()
            response = _PermissionAppItem(
                id=app_row.id,
                name=app_row.name,
                access_policy=app_row.access_policy,
                allow_anonymous=app_row.allow_anonymous,
            )
        return response.model_dump(mode="json")


@console_ns.route("/permissions/apps/<uuid:app_id>/whitelist")
class WhitelistCollectionApi(Resource):
    """List and create whitelist entries for a single app."""

    method_decorators = [setup_required, login_required, account_initialization_required]

    def get(self, app_id):
        _, current_tenant_id = current_account_with_tenant()
        _load_tenant_app(str(app_id), current_tenant_id)

        with sessionmaker(bind=db.engine, expire_on_commit=False).begin() as session:
            rows = session.scalars(
                select(AppAccessPermission)
                .where(AppAccessPermission.app_id == str(app_id))
                .order_by(AppAccessPermission.created_at.desc())
            ).all()
        items = [
            _WhitelistEntryItem(
                id=r.id,
                app_id=r.app_id,
                user_id=r.user_id,
                expires_at=r.expires_at,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]
        return _WhitelistEntryList(data=items).model_dump(mode="json")

    def post(self, app_id):
        _, current_tenant_id = current_account_with_tenant()
        payload = _WhitelistCreatePayload.model_validate(console_ns.payload or {})

        _ensure_future(payload.expires_at)

        _load_tenant_app(str(app_id), current_tenant_id)

        rows, skipped = AppAccessPermissionService.grant_many(
            app_id=str(app_id),
            user_ids=payload.user_ids,
            expires_at=payload.expires_at,
        )

        return (
            _WhitelistCreateResult(
                data=[
                    _WhitelistEntryItem(
                        id=r.id,
                        app_id=r.app_id,
                        user_id=r.user_id,
                        expires_at=r.expires_at,
                        created_at=r.created_at,
                        updated_at=r.updated_at,
                    )
                    for r in rows
                ],
                skipped=skipped,
            ).model_dump(mode="json"),
            201,
        )


@console_ns.route("/permissions/apps/<uuid:app_id>/whitelist/<uuid:perm_id>")
class WhitelistEntryApi(Resource):
    """Update or revoke a single whitelist entry."""

    method_decorators = [setup_required, login_required, account_initialization_required]

    def patch(self, app_id, perm_id):
        _, current_tenant_id = current_account_with_tenant()
        _load_tenant_app(str(app_id), current_tenant_id)

        payload = _WhitelistUpdatePayload.model_validate(console_ns.payload or {})
        _ensure_future(payload.expires_at)

        row = AppAccessPermissionService.update(
            perm_id=str(perm_id),
            app_id=str(app_id),
            expires_at=payload.expires_at,
        )
        if row is None:
            raise NotFound("Whitelist entry not found.")

        return _WhitelistEntryItem(
            id=row.id,
            app_id=row.app_id,
            user_id=row.user_id,
            expires_at=row.expires_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        ).model_dump(mode="json")

    def delete(self, app_id, perm_id):
        _, current_tenant_id = current_account_with_tenant()
        _load_tenant_app(str(app_id), current_tenant_id)

        if not AppAccessPermissionService.revoke(perm_id=str(perm_id), app_id=str(app_id)):
            raise NotFound("Whitelist entry not found.")

        return _ResultResponse().model_dump(mode="json"), 200