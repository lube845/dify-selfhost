"""Per-app, per-end-user access permission service.

Scope
-----
This service gates webapp chat access (``controllers/web/wraps.py``). It is
intentionally NOT applied to:

- ``controllers/service_api/*`` (programmatic API callers)
- ``controllers/inner_api/*`` (webhook / plugin callbacks)
- ``controllers/console/*`` (admin / dashboard)

If the same policy needs to extend to those flows, call
``AppAccessPermissionService.check_access`` at the appropriate gate — the
``(app, end_user)`` tuple is the same shape.

Match key
---------
The match key is ``end_user.session_id``. Today this is identical to
``EndUser.external_user_id`` (see ``EndUserService.get_or_create_end_user_by_type``
in ``api/services/end_user_service.py``), so a single match covers both. If
those two fields ever diverge, this service should be revisited.

Edge cases
----------
- ``App.access_policy == 'allow_all'`` + ``App.allow_anonymous`` (default):
  always allow, no permission row required. The visitor may be an anonymous
  end_user created with a random ``session_id``.
- ``App.access_policy == 'allow_all'`` + ``App.allow_anonymous is False``: the
  visitor must carry a signed-in identity (OA session) before chatting. No
  allowlist row is consulted — any signed-in user is accepted. Anonymous
  callers get ``AUTH_REQUIRED``.
- ``App.access_policy == 'deny_all_explicit'``: sign in *and* hold an active
  ``AppAccessPermission`` row for the (app, user) tuple; anonymous callers get
  ``AUTH_REQUIRED`` first.
  ``expires_at`` is treated as "active" when NULL or on/after the server's
  local calendar date — the picked day is the **last** day access is granted.
- Switching ``allow_all`` → ``deny_all_explicit`` on an existing app will
  start denying previously-authenticated end_users on their next request.
  No auto-backfill — manage via DB / scripts.
- ``user_id`` in a permission row is an opaque string. It need not correspond
  to any existing ``end_users`` row; admins can pre-grant IDs ahead of
  first login.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from extensions.ext_database import db
from models.model import App, AppAccessPermission, EndUser


class AppAccessPolicy(StrEnum):
    ALLOW_ALL = "allow_all"
    DENY_ALL_EXPLICIT = "deny_all_explicit"


class AccessCheckResult(StrEnum):
    """Four-way result of ``AppAccessPermissionService.check_access_with_reason``.

    Distinguishing the failure modes lets the webapp gate surface a precise
    user-facing message:

    - ``DENIED``        — the (app, user) tuple has no row at all. The user was
      never granted access; admin must explicitly grant it.
    - ``EXPIRED``       — a row exists but its ``expires_at`` is in the past. The
      user *was* on the allowlist, so the most accurate message is "permission
      expired, contact admin to renew" rather than the generic "not authorised".
    - ``AUTH_REQUIRED`` — the app does not accept anonymous visitors and the
      caller has no signed-in identity. Not a permission problem: the user can
      self-recover by signing in through ``/oa-login``.
    """

    ALLOWED = "allowed"
    DENIED = "denied"
    EXPIRED = "expired"
    AUTH_REQUIRED = "auth_required"


class _Unset:
    """Singleton sentinel for "argument not provided".

    Used in service methods whose nullable kwargs need to distinguish three
    states — "field is set to null (clear it)" vs. "field is left untouched".
    The default value of the kwarg is ``UNSET``; callers explicitly pass
    ``None`` to mean "clear". Compare with ``is`` / ``is not``.
    """

    _instance: _Unset | None = None

    def __new__(cls) -> _Unset:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"


UnsetType = _Unset
UNSET = _Unset()


class AppAccessPermissionService:
    """CRUD + access check for the ``app_access_permissions`` table."""

    @classmethod
    def requires_login(cls, *, app: App) -> bool:
        """Return True when an anonymous visitor may not use ``app``.

        Two independent reasons:

        - ``access_policy == 'deny_all_explicit'`` — the app is allowlist-only,
          which presupposes an identified user (the allowlist is keyed on
          ``end_user.session_id``, so an anonymous random uuid could never
          match an admin-entered value).
        - ``allow_anonymous is False`` — the admin explicitly turned anonymous
          access off while keeping default access on.

        Only an explicit ``False`` counts as opt-out: a missing / ``None``
        value (rows written before the column existed, or an unloaded
        attribute) keeps the historical permissive behaviour.
        """
        if app.access_policy == AppAccessPolicy.DENY_ALL_EXPLICIT.value:
            return True
        return getattr(app, "allow_anonymous", True) is False

    @classmethod
    def check_access(cls, *, app: App, end_user: EndUser, authenticated: bool = True) -> bool:
        """Return True if ``end_user`` may chat on ``app``.

        Short-circuits on ``ALLOW_ALL`` without touching the permission table.
        """
        return (
            cls.check_access_with_reason(app=app, end_user=end_user, authenticated=authenticated)
            == AccessCheckResult.ALLOWED
        )

    @classmethod
    def check_access_with_reason(
        cls, *, app: App, end_user: EndUser, authenticated: bool = True
    ) -> AccessCheckResult:
        """Access check returning the reason for a rejection.

        ``DENIED`` and ``EXPIRED`` look identical to ``check_access`` (both
        return False) but mean different things on the webapp permission page:
        ``DENIED`` is "you've never been on the allowlist", ``EXPIRED`` is "you
        were on it but your row's ``expires_at`` is in the past". The latter is
        communicated as "权限已过期"; the former as "您未被授权".
        ``AUTH_REQUIRED`` means the caller is anonymous on an app that does not
        accept anonymous visitors.

        ``authenticated`` is the caller's *identity* claim — True when the
        request carries a signed-in user (OA session) rather than an anonymous
        random-uuid end_user. It defaults to True so that callers predating the
        ``allow_anonymous`` column keep their previous semantics; the webapp
        controllers all pass it explicitly.

        Implementation note: we issue two queries (active row, then expired row)
        rather than one with ``OR`` to keep each statement's plan simple and
        to avoid a single composite index on the (app_id, user_id, expires_at)
        triple. The first query is the common path and hits the active index.
        """
        if cls.requires_login(app=app) and not authenticated:
            return AccessCheckResult.AUTH_REQUIRED

        policy = app.access_policy
        if policy == AppAccessPolicy.ALLOW_ALL.value:
            return AccessCheckResult.ALLOWED

        if policy != AppAccessPolicy.DENY_ALL_EXPLICIT.value:
            # Unknown policy value — fail closed to avoid accidental open access.
            return AccessCheckResult.DENIED

        # `expires_at` is the last day access is granted. Comparing against the
        # server-local calendar date (not a wall-clock instant) keeps the
        # boundary stable regardless of what timezone the gateway, the app
        # server, or the end_user happen to share.
        today = date.today()
        with sessionmaker(bind=db.engine, expire_on_commit=False).begin() as session:
            active_row = session.scalar(
                select(AppAccessPermission).where(
                    AppAccessPermission.app_id == app.id,
                    AppAccessPermission.user_id == end_user.session_id,
                    (AppAccessPermission.expires_at.is_(None)) | (AppAccessPermission.expires_at >= today),
                )
            )
            if active_row is not None:
                return AccessCheckResult.ALLOWED

            expired_row = session.scalar(
                select(AppAccessPermission).where(
                    AppAccessPermission.app_id == app.id,
                    AppAccessPermission.user_id == end_user.session_id,
                    AppAccessPermission.expires_at.is_not(None),
                    AppAccessPermission.expires_at < today,
                )
            )
        if expired_row is not None:
            return AccessCheckResult.EXPIRED
        return AccessCheckResult.DENIED

    @classmethod
    def grant(
        cls,
        *,
        app_id: str,
        user_id: str,
        expires_at: date | None = None,
    ) -> AppAccessPermission:
        """Insert a new permission row and return the persisted instance.

        Caller is responsible for handling unique-constraint collisions on
        ``(app_id, user_id)`` (e.g., call ``revoke`` first or use ``update``).
        The DB will raise ``IntegrityError`` on duplicates.
        """
        with sessionmaker(bind=db.engine, expire_on_commit=False).begin() as session:
            row = AppAccessPermission(
                app_id=app_id,
                user_id=user_id,
                expires_at=expires_at,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
        return row

    @classmethod
    def grant_many(
        cls,
        *,
        app_id: str,
        user_ids: list[str],
        expires_at: date | None = None,
    ) -> tuple[list[AppAccessPermission], list[str]]:
        """Insert multiple permission rows in a single transaction.

        Behaviour:
        - De-duplicates ``user_ids`` (preserves first-seen order) and drops
          empties. Caller is expected to trim; we trim again defensively.
        - Skips ``(app_id, user_id)`` pairs that already exist; no exception
          is raised. The caller gets back ``(inserted, skipped)`` so the UI
          can surface a partial-success message.
        - Returns ``([], [])`` if ``user_ids`` is empty after dedup.
        """
        seen: set[str] = set()
        deduped: list[str] = []
        for raw in user_ids:
            uid = raw.strip()
            if not uid or uid in seen:
                continue
            seen.add(uid)
            deduped.append(uid)
        if not deduped:
            return [], []

        with sessionmaker(bind=db.engine, expire_on_commit=False).begin() as session:
            existing = set(
                session.scalars(
                    select(AppAccessPermission.user_id).where(
                        AppAccessPermission.app_id == app_id,
                        AppAccessPermission.user_id.in_(deduped),
                    )
                ).all()
            )
            to_insert = [uid for uid in deduped if uid not in existing]
            skipped = [uid for uid in deduped if uid in existing]
            if not to_insert:
                return [], skipped
            rows = [
                AppAccessPermission(
                    app_id=app_id,
                    user_id=uid,
                    expires_at=expires_at,
                )
                for uid in to_insert
            ]
            session.add_all(rows)
            session.flush()
            for r in rows:
                session.refresh(r)
        return rows, skipped

    @classmethod
    def revoke(cls, *, perm_id: str, app_id: str) -> bool:
        """Delete the row identified by ``(perm_id, app_id)``.

        The ``app_id`` clause is defence-in-depth: a perm_id from a
        different app cannot be deleted by guessing the URL.

        Returns True if a row was removed, False if no row matched.
        """
        with sessionmaker(bind=db.engine, expire_on_commit=False).begin() as session:
            row = session.scalar(
                select(AppAccessPermission).where(
                    AppAccessPermission.id == perm_id,
                    AppAccessPermission.app_id == app_id,
                )
            )
            if row is None:
                return False
            session.delete(row)
            return True

    @classmethod
    def update(
        cls,
        *,
        perm_id: str,
        app_id: str,
        expires_at: date | None | UnsetType = UNSET,
    ) -> AppAccessPermission | None:
        """Update ``expires_at`` on an existing permission row.

        The row is identified by ``(perm_id, app_id)`` — the ``app_id`` clause
        is defence-in-depth so callers cannot update an entry that belongs
        to a different app even if a perm_id leaks.

        Semantics for ``expires_at`` (aligned with the console controller's
        PATCH payload, where ``null`` means "clear"):

        - pass a ``date``  → set ``expires_at`` to that date
        - pass ``None``    → clear ``expires_at`` (row never expires)
        - omit / pass ``UNSET`` → leave the existing value untouched

        Returns None if no row matches ``(perm_id, app_id)``.
        """
        with sessionmaker(bind=db.engine, expire_on_commit=False).begin() as session:
            row = session.scalar(
                select(AppAccessPermission).where(
                    AppAccessPermission.id == perm_id,
                    AppAccessPermission.app_id == app_id,
                )
            )
            if row is None:
                return None
            if expires_at is not UNSET:
                row.expires_at = expires_at
                session.flush()
                session.refresh(row)
            return row

    @classmethod
    def list_by_app(cls, *, app_id: str) -> list[AppAccessPermission]:
        """List permission rows for an app."""
        with sessionmaker(bind=db.engine, expire_on_commit=False).begin() as session:
            return list(
                session.scalars(
                    select(AppAccessPermission).where(AppAccessPermission.app_id == app_id)
                ).all()
            )

    @classmethod
    def list_by_user(cls, *, user_id: str) -> list[AppAccessPermission]:
        """List all permission rows held by ``user_id`` across all apps."""
        with sessionmaker(bind=db.engine, expire_on_commit=False).begin() as session:
            return list(
                session.scalars(
                    select(AppAccessPermission).where(AppAccessPermission.user_id == user_id)
                ).all()
            )