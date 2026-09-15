"""Unit tests for AppAccessPermissionService.

Coverage:
- ``check_access`` honours the app's ``access_policy``:
  - ``ALLOW_ALL`` always grants access regardless of permission rows
  - ``DENY_ALL_EXPLICIT`` requires an active (non-expired) permission row
    keyed on ``end_user.session_id``
- CRUD: ``grant`` / ``update`` / ``revoke``
- Queries: ``list_by_app``, ``list_by_user``

Mocking strategy: patch both ``db`` (so ``db.engine`` is never touched and
never triggers Flask-context errors) and ``sessionmaker`` (so the service's
``sessionmaker(...).begin()`` chain yields a controlled MagicMock session).
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from services.app_access_permission_service import (
    UNSET,
    AccessCheckResult,
    AppAccessPermissionService,
    AppAccessPolicy,
)


def _sessionmaker_mock(session: MagicMock) -> MagicMock:
    """Stand-in for ``sessionmaker(bind=..., expire_on_commit=False).begin()``.

    Real service code does::

        with sessionmaker(bind=db.engine, expire_on_commit=False).begin() as session:
            ...

    so the mock must expose ``.begin()`` returning a context manager that
    yields the supplied ``session``.
    """
    sm = MagicMock()
    sm.begin.return_value.__enter__.return_value = session
    sm.begin.return_value.__exit__.return_value = False
    return sm


class TestCheckAccess:
    """AppAccessPermissionService.check_access behaviour."""

    def _make_app(self, policy: str, app_id: str = "app-1") -> MagicMock:
        app = MagicMock()
        app.id = app_id
        app.access_policy = policy
        return app

    def _make_end_user(self, session_id: str = "user-123") -> MagicMock:
        user = MagicMock()
        user.session_id = session_id
        user.external_user_id = session_id
        return user

    def test_allow_all_policy_always_returns_true(self):
        app = self._make_app(AppAccessPolicy.ALLOW_ALL.value)
        end_user = self._make_end_user()

        assert AppAccessPermissionService.check_access(app=app, end_user=end_user) is True

    def test_allow_all_does_not_touch_db(self):
        """allow_all short-circuits — neither db nor sessionmaker should be called."""
        app = self._make_app(AppAccessPolicy.ALLOW_ALL.value)
        end_user = self._make_end_user()

        with patch("services.app_access_permission_service.db") as mock_db, patch(
            "services.app_access_permission_service.sessionmaker"
        ) as mock_sessionmaker:
            result = AppAccessPermissionService.check_access(app=app, end_user=end_user)

        assert result is True
        mock_db.engine.assert_not_called()
        mock_sessionmaker.assert_not_called()

    def test_deny_all_explicit_no_record_returns_false(self):
        app = self._make_app(AppAccessPolicy.DENY_ALL_EXPLICIT.value)
        end_user = self._make_end_user()

        session = MagicMock()
        session.scalar.return_value = None

        with patch("services.app_access_permission_service.db"), patch(
            "services.app_access_permission_service.sessionmaker",
            return_value=_sessionmaker_mock(session),
        ):
            result = AppAccessPermissionService.check_access(app=app, end_user=end_user)

        assert result is False

    def test_deny_all_explicit_active_record_returns_true(self):
        app = self._make_app(AppAccessPolicy.DENY_ALL_EXPLICIT.value)
        end_user = self._make_end_user()

        session = MagicMock()
        session.scalar.return_value = MagicMock()  # row found

        with patch("services.app_access_permission_service.db"), patch(
            "services.app_access_permission_service.sessionmaker",
            return_value=_sessionmaker_mock(session),
        ):
            result = AppAccessPermissionService.check_access(app=app, end_user=end_user)

        assert result is True
        assert session.scalar.call_count == 1

    def test_deny_all_explicit_match_key_is_session_id(self):
        """The query should be keyed on end_user.session_id."""
        app = self._make_app(AppAccessPolicy.DENY_ALL_EXPLICIT.value)
        end_user = self._make_end_user(session_id="session-xyz")

        session = MagicMock()
        session.scalar.return_value = None

        with patch("services.app_access_permission_service.db"), patch(
            "services.app_access_permission_service.sessionmaker",
            return_value=_sessionmaker_mock(session),
        ):
            AppAccessPermissionService.check_access(app=app, end_user=end_user)

        # Two lookups: the active window first, then the expired one.
        assert session.scalar.call_count == 2
        active_query = session.scalar.call_args_list[0].args[0].compile(
            compile_kwargs={"literal_binds": True}
        )
        assert "session-xyz" in str(active_query)

    def test_unknown_policy_fails_closed(self):
        """Defensive: an unrecognised policy value must NOT grant access."""
        app = self._make_app("something_else")
        end_user = self._make_end_user()

        with patch("services.app_access_permission_service.db"), patch(
            "services.app_access_permission_service.sessionmaker"
        ) as mock_sessionmaker:
            result = AppAccessPermissionService.check_access(app=app, end_user=end_user)

        assert result is False
        mock_sessionmaker.assert_not_called()


class TestAnonymousPolicy:
    """``requires_login`` + the AUTH_REQUIRED short-circuit.

    Second level of the access policy: an app can keep default access on while
    still refusing visitors that have no signed-in identity.
    """

    def _make_app(self, policy: str, *, allow_anonymous: bool | None = None) -> MagicMock:
        app = MagicMock()
        app.id = "app-1"
        app.access_policy = policy
        if allow_anonymous is not None:
            app.allow_anonymous = allow_anonymous
        return app

    def test_allow_all_with_anonymous_on_needs_no_login(self):
        assert AppAccessPermissionService.requires_login(
            app=self._make_app(AppAccessPolicy.ALLOW_ALL.value, allow_anonymous=True)
        ) is False

    def test_allow_all_with_anonymous_off_requires_login(self):
        assert AppAccessPermissionService.requires_login(
            app=self._make_app(AppAccessPolicy.ALLOW_ALL.value, allow_anonymous=False)
        ) is True

    def test_allowlist_only_app_requires_login(self):
        # deny_all_explicit presupposes an identified user, so it forces a
        # sign-in no matter how allow_anonymous is set.
        assert AppAccessPermissionService.requires_login(
            app=self._make_app(AppAccessPolicy.DENY_ALL_EXPLICIT.value, allow_anonymous=True)
        ) is True

    def test_missing_flag_keeps_historical_behaviour(self):
        # Rows written before the column existed (or an attribute that was
        # never loaded) must not start locking visitors out.
        assert AppAccessPermissionService.requires_login(
            app=self._make_app(AppAccessPolicy.ALLOW_ALL.value)
        ) is False

    def test_anonymous_caller_gets_auth_required_without_touching_db(self):
        app = self._make_app(AppAccessPolicy.ALLOW_ALL.value, allow_anonymous=False)
        end_user = self._make_end_user()

        with patch("services.app_access_permission_service.sessionmaker") as mock_sessionmaker:
            result = AppAccessPermissionService.check_access_with_reason(
                app=app, end_user=end_user, authenticated=False
            )

        assert result is AccessCheckResult.AUTH_REQUIRED
        # The policy short-circuits before the allowlist lookups: there is no
        # session to open, so the DB is never touched.
        mock_sessionmaker.assert_not_called()

    def test_authenticated_caller_with_anonymous_off_is_allowed(self):
        # Default access is on and the visitor is signed in: nothing to check.
        app = self._make_app(AppAccessPolicy.ALLOW_ALL.value, allow_anonymous=False)

        with patch("services.app_access_permission_service.sessionmaker") as mock_sessionmaker:
            result = AppAccessPermissionService.check_access_with_reason(
                app=app, end_user=self._make_end_user(), authenticated=True
            )

        assert result is AccessCheckResult.ALLOWED
        mock_sessionmaker.assert_not_called()

    def test_allowlist_only_app_still_consults_the_allowlist_when_signed_in(self):
        app = self._make_app(AppAccessPolicy.DENY_ALL_EXPLICIT.value, allow_anonymous=False)
        end_user = self._make_end_user(session_id="workcode-1")

        session = MagicMock()
        session.scalar.return_value = MagicMock()  # active row found

        with patch("services.app_access_permission_service.db"), patch(
            "services.app_access_permission_service.sessionmaker",
            return_value=_sessionmaker_mock(session),
        ):
            result = AppAccessPermissionService.check_access_with_reason(
                app=app, end_user=end_user, authenticated=True
            )

        assert result is AccessCheckResult.ALLOWED

    def _make_end_user(self, session_id: str = "user-123") -> MagicMock:
        user = MagicMock()
        user.session_id = session_id
        user.external_user_id = session_id
        return user


class TestGrant:
    def test_grant_creates_new_permission_row(self):
        expires_at = date.today() + timedelta(days=30)

        session = MagicMock()

        with patch("services.app_access_permission_service.db"), patch(
            "services.app_access_permission_service.sessionmaker",
            return_value=_sessionmaker_mock(session),
        ):
            result = AppAccessPermissionService.grant(
                app_id="app-1",
                user_id="user-123",
                expires_at=expires_at,
            )

        session.add.assert_called_once()
        added = session.add.call_args[0][0]
        assert added.app_id == "app-1"
        assert added.user_id == "user-123"
        assert added.expires_at == expires_at
        assert result is added

    def test_grant_without_expires_at_allowed(self):
        """expires_at=None means permanent access."""
        session = MagicMock()

        with patch("services.app_access_permission_service.db"), patch(
            "services.app_access_permission_service.sessionmaker",
            return_value=_sessionmaker_mock(session),
        ):
            AppAccessPermissionService.grant(app_id="app-1", user_id="user-123")

        added = session.add.call_args[0][0]
        assert added.expires_at is None


class TestRevoke:
    def test_revoke_returns_true_when_row_deleted(self):
        session = MagicMock()
        session.scalar.return_value = MagicMock()  # row exists

        with patch("services.app_access_permission_service.db"), patch(
            "services.app_access_permission_service.sessionmaker",
            return_value=_sessionmaker_mock(session),
        ):
            result = AppAccessPermissionService.revoke(perm_id="perm-1", app_id="app-1")

        assert result is True
        session.delete.assert_called_once()

    def test_revoke_returns_false_when_no_row(self):
        session = MagicMock()
        session.scalar.return_value = None

        with patch("services.app_access_permission_service.db"), patch(
            "services.app_access_permission_service.sessionmaker",
            return_value=_sessionmaker_mock(session),
        ):
            result = AppAccessPermissionService.revoke(perm_id="perm-1", app_id="app-1")

        assert result is False
        session.delete.assert_not_called()


class TestUpdate:
    def test_update_mutates_expires_at_and_returns_row(self):
        new_expiry = date.today() + timedelta(days=60)
        existing = MagicMock()
        existing.expires_at = date.today() + timedelta(days=30)
        session = MagicMock()
        session.scalar.return_value = existing

        with patch("services.app_access_permission_service.db"), patch(
            "services.app_access_permission_service.sessionmaker",
            return_value=_sessionmaker_mock(session),
        ):
            result = AppAccessPermissionService.update(
                perm_id="perm-1", app_id="app-1", expires_at=new_expiry
            )

        assert result is existing
        assert existing.expires_at == new_expiry

    def test_update_with_none_clears_expiry(self):
        """`expires_at=None` means 'clear the expiry' — matches controller semantics."""
        existing = MagicMock()
        existing.expires_at = date.today() + timedelta(days=30)
        session = MagicMock()
        session.scalar.return_value = existing

        with patch("services.app_access_permission_service.db"), patch(
            "services.app_access_permission_service.sessionmaker",
            return_value=_sessionmaker_mock(session),
        ):
            result = AppAccessPermissionService.update(
                perm_id="perm-1", app_id="app-1", expires_at=None
            )

        assert result is existing
        assert existing.expires_at is None

    def test_update_without_expires_at_leaves_value_unchanged(self):
        """Omitting `expires_at` (default UNSET) leaves the existing value intact."""
        original_expiry = date.today() + timedelta(days=30)
        existing = MagicMock()
        existing.expires_at = original_expiry
        session = MagicMock()
        session.scalar.return_value = existing

        with patch("services.app_access_permission_service.db"), patch(
            "services.app_access_permission_service.sessionmaker",
            return_value=_sessionmaker_mock(session),
        ):
            result = AppAccessPermissionService.update(perm_id="perm-1", app_id="app-1")

        assert result is existing
        assert existing.expires_at == original_expiry

    def test_update_with_explicit_unset_leaves_value_unchanged(self):
        """Passing UNSET explicitly behaves the same as omitting the kwarg."""
        original_expiry = date.today() + timedelta(days=30)
        existing = MagicMock()
        existing.expires_at = original_expiry
        session = MagicMock()
        session.scalar.return_value = existing

        with patch("services.app_access_permission_service.db"), patch(
            "services.app_access_permission_service.sessionmaker",
            return_value=_sessionmaker_mock(session),
        ):
            result = AppAccessPermissionService.update(
                perm_id="perm-1", app_id="app-1", expires_at=UNSET
            )

        assert result is existing
        assert existing.expires_at == original_expiry

    def test_update_returns_none_when_no_row(self):
        session = MagicMock()
        session.scalar.return_value = None

        with patch("services.app_access_permission_service.db"), patch(
            "services.app_access_permission_service.sessionmaker",
            return_value=_sessionmaker_mock(session),
        ):
            result = AppAccessPermissionService.update(
                perm_id="perm-1", app_id="app-1", expires_at=date.today()
            )

        assert result is None


class TestGrantMany:
    """Batch insert with dedup + skip-existing semantics."""

    def test_empty_input_returns_empty_and_does_not_touch_db(self):
        with patch("services.app_access_permission_service.db") as mock_db, patch(
            "services.app_access_permission_service.sessionmaker"
        ) as mock_sessionmaker:
            rows, skipped = AppAccessPermissionService.grant_many(
                app_id="app-1", user_ids=[]
            )

        assert rows == []
        assert skipped == []
        mock_sessionmaker.assert_not_called()
        mock_db.engine.assert_not_called()

    def test_only_whitespace_inputs_are_dropped(self):
        """Trim then filter empties; the DB is never opened for an all-whitespace input."""
        with patch("services.app_access_permission_service.db") as mock_db, patch(
            "services.app_access_permission_service.sessionmaker"
        ) as mock_sessionmaker:
            rows, skipped = AppAccessPermissionService.grant_many(
                app_id="app-1", user_ids=["   ", "\t", ""]
            )

        assert rows == []
        assert skipped == []
        mock_sessionmaker.assert_not_called()
        mock_db.engine.assert_not_called()

    def test_all_new_inserts(self):
        """Every input is missing from the table → all rows added, none skipped."""
        session = MagicMock()
        # existing lookup returns nothing
        session.scalars.return_value.all.return_value = []

        with patch("services.app_access_permission_service.db"), patch(
            "services.app_access_permission_service.sessionmaker",
            return_value=_sessionmaker_mock(session),
        ):
            rows, skipped = AppAccessPermissionService.grant_many(
                app_id="app-1", user_ids=["a", "b", "c"]
            )

        assert skipped == []
        assert len(rows) == 3
        # Each user_id gets its own row.
        assert {r.user_id for r in rows} == {"a", "b", "c"}
        # All rows share the same app_id + expires_at.
        for r in rows:
            assert r.app_id == "app-1"
            assert r.expires_at is None
        session.add_all.assert_called_once()
        # Per-row refresh to load server-default created_at/updated_at.
        assert session.refresh.call_count == 3

    def test_dedup_within_input(self):
        """Duplicates in the input collapse to one insert."""
        session = MagicMock()
        session.scalars.return_value.all.return_value = []

        with patch("services.app_access_permission_service.db"), patch(
            "services.app_access_permission_service.sessionmaker",
            return_value=_sessionmaker_mock(session),
        ):
            rows, skipped = AppAccessPermissionService.grant_many(
                app_id="app-1", user_ids=["a", "b", "a", "a", "b"]
            )

        assert skipped == []
        assert len(rows) == 2
        assert {r.user_id for r in rows} == {"a", "b"}

    def test_trim_surrounding_whitespace(self):
        session = MagicMock()
        session.scalars.return_value.all.return_value = []

        with patch("services.app_access_permission_service.db"), patch(
            "services.app_access_permission_service.sessionmaker",
            return_value=_sessionmaker_mock(session),
        ):
            rows, skipped = AppAccessPermissionService.grant_many(
                app_id="app-1", user_ids=["  a  ", " b ", "a"]
            )

        assert skipped == []
        assert [r.user_id for r in rows] == ["a", "b"]

    def test_all_existing_skipped_no_insert(self):
        """If every input is already whitelisted, return ([], skipped) without inserting."""
        session = MagicMock()
        session.scalars.return_value.all.return_value = ["a", "b"]

        with patch("services.app_access_permission_service.db"), patch(
            "services.app_access_permission_service.sessionmaker",
            return_value=_sessionmaker_mock(session),
        ):
            rows, skipped = AppAccessPermissionService.grant_many(
                app_id="app-1", user_ids=["a", "b"]
            )

        assert rows == []
        assert skipped == ["a", "b"]
        session.add_all.assert_not_called()
        session.refresh.assert_not_called()

    def test_mixed_new_and_existing(self):
        """Some new + some existing → new ones inserted, existing reported as skipped."""
        session = MagicMock()
        session.scalars.return_value.all.return_value = ["b"]  # only "b" already exists

        with patch("services.app_access_permission_service.db"), patch(
            "services.app_access_permission_service.sessionmaker",
            return_value=_sessionmaker_mock(session),
        ):
            rows, skipped = AppAccessPermissionService.grant_many(
                app_id="app-1", user_ids=["a", "b", "c"]
            )

        inserted_ids = {r.user_id for r in rows}
        assert inserted_ids == {"a", "c"}
        assert skipped == ["b"]
        session.add_all.assert_called_once()
        assert session.refresh.call_count == 2

    def test_expires_at_propagates_to_all_rows(self):
        session = MagicMock()
        session.scalars.return_value.all.return_value = []
        expiry = date.today() + timedelta(days=7)

        with patch("services.app_access_permission_service.db"), patch(
            "services.app_access_permission_service.sessionmaker",
            return_value=_sessionmaker_mock(session),
        ):
            rows, _ = AppAccessPermissionService.grant_many(
                app_id="app-1", user_ids=["a", "b"], expires_at=expiry
            )

        for r in rows:
            assert r.expires_at == expiry


class TestListByApp:
    def test_list_by_app_returns_rows(self):
        rows = [MagicMock(), MagicMock()]
        session = MagicMock()
        session.scalars.return_value.all.return_value = rows

        with patch("services.app_access_permission_service.db"), patch(
            "services.app_access_permission_service.sessionmaker",
            return_value=_sessionmaker_mock(session),
        ):
            result = AppAccessPermissionService.list_by_app(app_id="app-1")

        assert result == rows


class TestListByUser:
    def test_list_by_user_returns_rows(self):
        rows = [MagicMock()]
        session = MagicMock()
        session.scalars.return_value.all.return_value = rows

        with patch("services.app_access_permission_service.db"), patch(
            "services.app_access_permission_service.sessionmaker",
            return_value=_sessionmaker_mock(session),
        ):
            result = AppAccessPermissionService.list_by_user(user_id="user-123")

        assert result == rows