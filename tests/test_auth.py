from __future__ import annotations

from pathlib import Path

import pytest

from src.auth import ADMIN, USER, AppDatabase, AuthorizationError, UserInputError


@pytest.fixture
def database(tmp_path: Path) -> AppDatabase:
    database = AppDatabase(f"sqlite:///{tmp_path / 'app.db'}")
    database.initialize()
    return database


@pytest.fixture
def admin(database: AppDatabase):
    return database.create_user(
        None,
        "Report Admin",
        "admin@ltu.edu",
        ADMIN,
        password="AdminPassword1!",
        must_change_password=False,
    )


def test_authenticate_and_force_password_change(database: AppDatabase, admin) -> None:
    user = database.create_user(
        admin,
        "Bowler User",
        "bowler@ltu.edu",
        USER,
        password="TemporaryPass1!",
    )

    authenticated = database.authenticate("BOWLER@LTU.EDU", "TemporaryPass1!")
    assert authenticated == user
    assert authenticated.must_change_password is True
    token, expires_at = database.create_auth_session(authenticated)
    assert expires_at.tzinfo is not None
    assert database.authenticate_session(token) == authenticated

    changed = database.change_password(authenticated, "TemporaryPass1!", "PermanentPass2!")
    assert changed.must_change_password is False
    assert database.authenticate_session(token) is None
    assert database.authenticate("bowler@ltu.edu", "PermanentPass2!") is not None
    assert database.authenticate("bowler@ltu.edu", "TemporaryPass1!") is None


def test_auth_session_can_be_revoked(database: AppDatabase, admin) -> None:
    token, _ = database.create_auth_session(admin)

    assert database.authenticate_session(token) == admin
    database.revoke_auth_session(token)
    assert database.authenticate_session(token) is None
    assert database.authenticate_session("invalid") is None


def test_create_user_rejects_duplicate_email_case_insensitively(database: AppDatabase, admin) -> None:
    database.create_user(
        admin,
        "Existing User",
        "existing@ltu.edu",
        USER,
        password="TemporaryPass1!",
    )

    with pytest.raises(UserInputError, match="already exists"):
        database.create_user(
            admin,
            "Duplicate User",
            " EXISTING@LTU.EDU ",
            USER,
            password="TemporaryPass2!",
        )


def test_user_cannot_manage_users_or_uploads(database: AppDatabase, admin) -> None:
    user = database.create_user(
        admin,
        "Read Only",
        "readonly@ltu.edu",
        USER,
        password="ReadOnlyPass1!",
    )

    with pytest.raises(AuthorizationError):
        database.list_users(user)
    with pytest.raises(AuthorizationError):
        database.save_uploads(user, [("event.csv", b"event\nWHAC\n")])


def test_admin_crud_and_last_admin_guard(database: AppDatabase, admin) -> None:
    user = database.create_user(
        admin,
        "First Name",
        "first@ltu.edu",
        USER,
        password="TemporaryPass1!",
    )
    updated = database.update_user(
        admin,
        user.id,
        name="Updated Name",
        email="updated@ltu.edu",
        role=ADMIN,
        active=True,
    )
    assert updated.name == "Updated Name"
    assert updated.is_admin

    database.delete_user(admin, updated.id)
    assert database.get_user(updated.id) is None
    with pytest.raises(UserInputError, match="own account"):
        database.delete_user(admin, admin.id)
    with pytest.raises(UserInputError, match="At least one"):
        database.update_user(
            admin,
            admin.id,
            name=admin.name,
            email=admin.email,
            role=USER,
            active=True,
        )


def test_admin_upload_update_and_delete(database: AppDatabase, admin) -> None:
    first = database.save_uploads(admin, [("WHAC.csv", b"name\nMatt\n")])[0]
    replacement = database.save_uploads(admin, [("WHAC.csv", b"name\nMatthew\n")])[0]

    uploads = database.list_uploads()
    assert len(uploads) == 1
    assert uploads[0].stored_name == replacement.stored_name
    assert uploads[0].content == b"name\nMatthew\n"
    assert first.stored_name != replacement.stored_name

    assert database.delete_upload(admin, replacement.stored_name) is True
    assert database.list_uploads() == []


def test_admin_reset_password_requires_change(database: AppDatabase, admin) -> None:
    user = database.create_user(
        admin,
        "Reset User",
        "reset@ltu.edu",
        USER,
        password="TemporaryPass1!",
    )
    token, _ = database.create_auth_session(user)
    temporary_password = database.reset_password(admin, user.id)

    authenticated = database.authenticate(user.email, temporary_password)
    assert authenticated is not None
    assert authenticated.must_change_password is True
    assert database.authenticate_session(token) is None
