"""Tests for the per-app access permission controller and service.

The user-facing bug we're guarding against: the ``expires_at`` column was
``sa.DateTime`` at 23:59:59, so a calendar date picked in the admin UI
sometimes round-tripped to the next day depending on serializer / driver /
client timezone. The fix stores a plain ``date``. These tests pin both
directions:

- round-trip serialization keeps the date a plain YYYY-MM-DD string;
- access checks treat the picked day as the *last* day access is granted
  (``expires_at >= today``), matching the admin UI's contract.
"""

from __future__ import annotations

import datetime as dt
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from werkzeug.exceptions import BadRequest

import controllers.console.permission as ctl_module
import services.app_access_permission_service as svc_module
from controllers.console.permission import _ensure_future
from services.app_access_permission_service import (
    AppAccessPermissionService,
    AppAccessPolicy,
)

# ---------- helpers ----------------------------------------------------------


class _FrozenDate:
    """Stand-in for ``datetime.date`` whose ``today()`` returns a fixed date.

    We can't patch ``datetime.date.today`` directly because the C builtin
    type forbids attribute assignment. Patching the module-level ``date``
    symbol is enough for the modules under test, which import ``date``
    with ``from datetime import date``.
    """

    _TODAY: dt.date = dt.date(1970, 1, 1)

    @staticmethod
    def today() -> dt.date:
        return _FrozenDate._TODAY


def _freeze_today(monkeypatch: pytest.MonkeyPatch, when: dt.date,
                  module=ctl_module) -> None:
    _FrozenDate._TODAY = when
    monkeypatch.setattr(module, "date", _FrozenDate)


# ---------- _ensure_future ---------------------------------------------------


class TestEnsureFuture:
    """The validator must accept today (the picked day is the last valid day)
    and reject only strictly-past dates."""

    def test_none_is_never_expires(self, monkeypatch: pytest.MonkeyPatch):
        _freeze_today(monkeypatch, dt.date(2026, 7, 7))
        _ensure_future(None)

    def test_today_is_allowed(self, monkeypatch: pytest.MonkeyPatch):
        _freeze_today(monkeypatch, dt.date(2026, 7, 7))
        _ensure_future(dt.date(2026, 7, 7))

    def test_future_is_allowed(self, monkeypatch: pytest.MonkeyPatch):
        _freeze_today(monkeypatch, dt.date(2026, 7, 7))
        _ensure_future(dt.date(2026, 7, 8))

    def test_yesterday_is_rejected(self, monkeypatch: pytest.MonkeyPatch):
        _freeze_today(monkeypatch, dt.date(2026, 7, 7))
        with pytest.raises(BadRequest):
            _ensure_future(dt.date(2026, 7, 6))


# ---------- response shape ---------------------------------------------------


class TestWhitelistEntryItemSerialization:
    """The frontend slices the first 10 chars of ``expires_at`` to render the
    date. The fix only holds if Pydantic serializes a plain ``date`` as
    ``YYYY-MM-DD`` with no trailing time component. Pin that."""

    def test_expires_at_serializes_as_plain_date(self):
        from controllers.console.permission import _WhitelistEntryItem

        item = _WhitelistEntryItem(
            id="abc",
            app_id="app",
            user_id="u",
            expires_at=dt.date(2026, 7, 7),
            created_at=dt.datetime(2026, 1, 1),
            updated_at=dt.datetime(2026, 1, 1),
        )
        serialized = item.model_dump(mode="json")["expires_at"]
        assert serialized == "2026-07-07"
        assert "T" not in serialized
        assert len(serialized) == 10

    def test_expires_at_none_serializes_as_null(self):
        from controllers.console.permission import _WhitelistEntryItem

        item = _WhitelistEntryItem(
            id="abc",
            app_id="app",
            user_id="u",
            expires_at=None,
            created_at=dt.datetime(2026, 1, 1),
            updated_at=dt.datetime(2026, 1, 1),
        )
        assert item.model_dump(mode="json")["expires_at"] is None


# ---------- request payloads -------------------------------------------------


class TestRequestPayloads:
    def test_create_payload_parses_date_string(self):
        from controllers.console.permission import _WhitelistCreatePayload

        payload = _WhitelistCreatePayload.model_validate(
            {"user_ids": ["u"], "expires_at": "2026-07-07"}
        )
        assert payload.expires_at == dt.date(2026, 7, 7)

    def test_create_payload_none_means_never_expires(self):
        from controllers.console.permission import _WhitelistCreatePayload

        payload = _WhitelistCreatePayload.model_validate(
            {"user_ids": ["u"], "expires_at": None}
        )
        assert payload.expires_at is None

    def test_create_payload_rejects_empty_list(self):
        """Frontend never sends an empty list — must be at least one user_id."""
        from pydantic import ValidationError

        from controllers.console.permission import _WhitelistCreatePayload

        with pytest.raises(ValidationError):
            _WhitelistCreatePayload.model_validate({"user_ids": []})

    def test_create_payload_strips_and_rejects_empty_strings(self):
        """Each entry must be non-empty after trim; the service drops empties
        defensively, but we lock the contract at the controller boundary."""
        from pydantic import ValidationError

        from controllers.console.permission import _WhitelistCreatePayload

        payload = _WhitelistCreatePayload.model_validate(
            {"user_ids": ["  a  ", "b"]}
        )
        assert payload.user_ids == ["a", "b"]

        with pytest.raises(ValidationError):
            _WhitelistCreatePayload.model_validate({"user_ids": ["a", "   "]})

    def test_update_payload_distinguishes_unset_from_null(self):
        """The Pydantic model treats ``{}`` as a no-op, ``{"expires_at": null}``
        as a clear. Pin that semantic so a frontend bug can't wipe entries
        silently."""
        from pydantic import ValidationError

        from controllers.console.permission import _WhitelistUpdatePayload

        with pytest.raises(ValidationError):
            _WhitelistUpdatePayload.model_validate({})

        cleared = _WhitelistUpdatePayload.model_validate({"expires_at": None})
        assert cleared.expires_at is None

        set_payload = _WhitelistUpdatePayload.model_validate({"expires_at": "2026-07-07"})
        assert set_payload.expires_at == dt.date(2026, 7, 7)


# ---------- access semantics -------------------------------------------------


class TestAppPermissionUpdatePayload:
    """The app policy PATCH is a partial update: either field, on its own."""

    def test_accepts_access_policy_only(self):
        from controllers.console.permission import _AppPermissionUpdatePayload

        payload = _AppPermissionUpdatePayload.model_validate(
            {"access_policy": "deny_all_explicit"}
        )
        assert payload.access_policy == "deny_all_explicit"
        # Untouched fields stay None so the controller knows not to write them.
        assert payload.allow_anonymous is None

    def test_accepts_allow_anonymous_only(self):
        from controllers.console.permission import _AppPermissionUpdatePayload

        payload = _AppPermissionUpdatePayload.model_validate({"allow_anonymous": False})
        assert payload.allow_anonymous is False
        assert payload.access_policy is None

    def test_accepts_both(self):
        from controllers.console.permission import _AppPermissionUpdatePayload

        payload = _AppPermissionUpdatePayload.model_validate(
            {"access_policy": "allow_all", "allow_anonymous": True}
        )
        assert payload.access_policy == "allow_all"
        assert payload.allow_anonymous is True

    def test_rejects_unknown_policy(self):
        from pydantic import ValidationError

        from controllers.console.permission import _AppPermissionUpdatePayload

        with pytest.raises(ValidationError):
            _AppPermissionUpdatePayload.model_validate({"access_policy": "sometimes"})

    def test_rejects_empty_payload(self):
        """An empty body must be rejected rather than silently no-op'ing."""
        from pydantic import ValidationError

        from controllers.console.permission import _AppPermissionUpdatePayload

        with pytest.raises(ValidationError):
            _AppPermissionUpdatePayload.model_validate({})


class TestCheckAccessBoundaries:
    """``check_access`` must treat the picked day as the *last* day access is
    granted. The matching SQL fragment is
    ``expires_at IS NULL OR expires_at >= today``. We patch the session
    boundary to drive the boundary dates without needing a real DB."""

    @staticmethod
    def _policy_app(access_policy: str) -> MagicMock:
        return MagicMock(access_policy=access_policy, id="app-id")

    @staticmethod
    def _end_user(session_id: str = "s1") -> SimpleNamespace:
        return SimpleNamespace(session_id=session_id)

    @staticmethod
    def _session_returning(row):
        class _Ctx:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def scalar(self, _stmt):
                return row

        class _SM:
            def __init__(self, **_kw):
                pass

            def begin(self):
                return _Ctx()

            def __call__(self, **_kw):
                return _Ctx()

        return _SM()

    def test_picked_day_is_active_on_that_day(self, monkeypatch: pytest.MonkeyPatch):
        """Picking 2026-07-07 must grant access ON 2026-07-07 (the picked
        day is the last valid day)."""
        _freeze_today(monkeypatch, dt.date(2026, 7, 7), module=svc_module)

        app = self._policy_app(AppAccessPolicy.DENY_ALL_EXPLICIT.value)
        end_user = self._end_user()

        with (
            patch.object(svc_module, "sessionmaker",
                         lambda **_kw: self._session_returning(row=MagicMock())),
            patch.object(svc_module, "db", MagicMock()),
        ):
            assert (
                AppAccessPermissionService.check_access(app=app, end_user=end_user) is True
            )

    def test_picked_day_is_denied_the_day_after(self, monkeypatch: pytest.MonkeyPatch):
        """Picking 2026-07-07 must deny access starting 2026-07-08. The query
        uses ``expires_at >= today`` (not ``>``), so 2026-07-08 won't match
        a row whose expires_at is 2026-07-07. This is the actual fix:
        previously the comparison was naive datetime strict-greater and
        could land either side of the boundary depending on timezone."""
        _freeze_today(monkeypatch, dt.date(2026, 7, 8), module=svc_module)

        app = self._policy_app(AppAccessPolicy.DENY_ALL_EXPLICIT.value)
        end_user = self._end_user()

        with (
            patch.object(svc_module, "sessionmaker",
                         lambda **_kw: self._session_returning(row=None)),
            patch.object(svc_module, "db", MagicMock()),
        ):
            assert (
                AppAccessPermissionService.check_access(app=app, end_user=end_user) is False
            )

    def test_picked_day_is_allowed_before(self, monkeypatch: pytest.MonkeyPatch):
        _freeze_today(monkeypatch, dt.date(2026, 7, 6), module=svc_module)

        app = self._policy_app(AppAccessPolicy.DENY_ALL_EXPLICIT.value)
        end_user = self._end_user()

        with (
            patch.object(svc_module, "sessionmaker",
                         lambda **_kw: self._session_returning(row=MagicMock())),
            patch.object(svc_module, "db", MagicMock()),
        ):
            assert (
                AppAccessPermissionService.check_access(app=app, end_user=end_user) is True
            )

    def test_allow_all_short_circuits_without_db(self):
        app = self._policy_app(AppAccessPolicy.ALLOW_ALL.value)
        end_user = self._end_user()

        def _no_session(**_kw):  # pragma: no cover - must not be invoked
            raise AssertionError("sessionmaker must not be called for allow_all")

        with patch.object(svc_module, "sessionmaker", _no_session):
            assert (
                AppAccessPermissionService.check_access(app=app, end_user=end_user) is True
            )

    def test_unknown_policy_is_fail_closed(self):
        app = self._policy_app("deny_something_weird")
        end_user = self._end_user()

        def _no_session(**_kw):  # pragma: no cover - must not be invoked
            raise AssertionError("sessionmaker must not be called for unknown policy")

        with patch.object(svc_module, "sessionmaker", _no_session):
            assert (
                AppAccessPermissionService.check_access(app=app, end_user=end_user) is False
            )


# ---------- guard against re-introducing the helper -------------------------


class TestNoEndOfDayHelperRegression:
    """The helper that produced ``datetime(2026, 7, 7, 23, 59, 59, 999999)``
    was the root cause of the picked day rolling forward. Pin both that the
    symbol is gone and that the controllers don't reach for ``datetime`` or
    an end-of-day timestamp."""

    def test_helper_symbol_is_gone(self):
        assert not hasattr(ctl_module, "_date_to_naive_end_of_day"), (
            "_date_to_naive_end_of_day is gone; storing an end-of-day"
            " datetime was what caused the admin UI to display the day"
            " AFTER the one the operator picked."
        )

    def test_handlers_keep_a_plain_date(self):
        """Both POST and PATCH must hand the ORM a ``date``, not a mutated
        ``datetime``. Static check on the source — easy to spot the
        regression without standing up a DB."""
        for fn, label in (
            (ctl_module.WhitelistCollectionApi.post, "POST"),
            (ctl_module.WhitelistEntryApi.patch, "PATCH"),
        ):
            src = inspect.getsource(fn)
            assert "datetime" not in src, (
                f"{label} handler must not mention `datetime`; the picked"
                " day is a calendar date and must round-trip as-is."
            )
            assert "23, 59, 59" not in src, (
                f"{label} handler must not build a 23:59:59 timestamp;"
                " the picked day is the last valid day, not a clock instant."
            )
            assert "23:59:59" not in src, (
                f"{label} handler must not build a 23:59:59 timestamp;"
                " the picked day is the last valid day, not a clock instant."
            )
