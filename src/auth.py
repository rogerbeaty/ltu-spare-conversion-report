from __future__ import annotations

import re
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import Boolean, DateTime, ForeignKey, LargeBinary, String, create_engine, delete, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

ADMIN = "admin"
USER = "user"
ROLES = (USER, ADMIN)


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "app_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(16), default=USER)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UploadRow(Base):
    __tablename__ = "app_uploads"

    id: Mapped[int] = mapped_column(primary_key=True)
    original_name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    stored_name: Mapped[str] = mapped_column(String(330), unique=True)
    digest: Mapped[str] = mapped_column(String(64), index=True)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    uploaded_by: Mapped[int | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


@dataclass(frozen=True)
class AppUser:
    id: int
    name: str
    email: str
    role: str
    active: bool
    must_change_password: bool

    @property
    def is_admin(self) -> bool:
        return self.role == ADMIN


@dataclass(frozen=True)
class StoredUpload:
    original_name: str
    stored_name: str
    digest: str
    content: bytes
    updated_at: datetime


class AuthorizationError(PermissionError):
    pass


class UserInputError(ValueError):
    pass


class AppDatabase:
    def __init__(self, url: str) -> None:
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine: Engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args)
        self.password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)
        if self.engine.dialect.name == "postgresql":
            self._align_postgres_sequences()

    def _align_postgres_sequences(self) -> None:
        # Explicit IDs copied during a migration do not advance PostgreSQL sequences.
        sequence_tables = {
            "app_users": "app_users_id_seq",
            "app_uploads": "app_uploads_id_seq",
        }
        with self.engine.begin() as connection:
            for table_name, sequence_name in sequence_tables.items():
                max_id = connection.scalar(text(f"SELECT MAX(id) FROM {table_name}"))
                if max_id is None:
                    continue
                last_value, is_called = connection.execute(
                    text(f"SELECT last_value, is_called FROM {sequence_name}")
                ).one()
                if max_id > last_value or (max_id == last_value and not is_called):
                    connection.execute(
                        text("SELECT setval(:sequence_name, :max_id, true)"),
                        {"sequence_name": sequence_name, "max_id": max_id},
                    )

    def user_count(self) -> int:
        with Session(self.engine) as session:
            return int(session.scalar(select(func.count()).select_from(UserRow)) or 0)

    def bootstrap_admin(self, name: str, email: str, password: str) -> AppUser | None:
        if self.user_count() != 0:
            return None
        return self.create_user(None, name, email, ADMIN, password=password, must_change_password=True)

    def authenticate(self, email: str, password: str) -> AppUser | None:
        normalized_email = normalize_email(email)
        with Session(self.engine) as session:
            row = session.scalar(select(UserRow).where(UserRow.email == normalized_email))
            if row is None or not row.active:
                return None
            try:
                self.password_hasher.verify(row.password_hash, password)
            except (InvalidHashError, VerificationError, VerifyMismatchError):
                return None
            if self.password_hasher.check_needs_rehash(row.password_hash):
                row.password_hash = self.password_hasher.hash(password)
                row.updated_at = utc_now()
                session.commit()
            return user_from_row(row)

    def get_user(self, user_id: int) -> AppUser | None:
        with Session(self.engine) as session:
            row = session.get(UserRow, user_id)
            return user_from_row(row) if row is not None else None

    def list_users(self, actor: AppUser) -> list[AppUser]:
        require_admin(actor)
        with Session(self.engine) as session:
            rows = session.scalars(select(UserRow).order_by(UserRow.name, UserRow.email)).all()
            return [user_from_row(row) for row in rows]

    def create_user(
        self,
        actor: AppUser | None,
        name: str,
        email: str,
        role: str,
        *,
        password: str | None = None,
        must_change_password: bool = True,
    ) -> AppUser:
        if actor is not None:
            require_admin(actor)
        clean_name = validate_name(name)
        normalized_email = normalize_email(email)
        validate_role(role)
        password = password or generate_password()
        validate_password(password)
        now = utc_now()
        with Session(self.engine) as session:
            if session.scalar(select(UserRow.id).where(UserRow.email == normalized_email)) is not None:
                raise UserInputError("A user with that email already exists.")
            row = UserRow(
                name=clean_name,
                email=normalized_email,
                password_hash=self.password_hasher.hash(password),
                role=role,
                active=True,
                must_change_password=must_change_password,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                if session.scalar(select(UserRow.id).where(UserRow.email == normalized_email)) is not None:
                    raise UserInputError("A user with that email already exists.") from exc
                raise UserInputError(
                    "The user could not be created because of a database conflict. Please try again."
                ) from exc
            session.refresh(row)
            return user_from_row(row)

    def update_user(
        self,
        actor: AppUser,
        user_id: int,
        *,
        name: str,
        email: str,
        role: str,
        active: bool,
    ) -> AppUser:
        require_admin(actor)
        clean_name = validate_name(name)
        normalized_email = normalize_email(email)
        validate_role(role)
        with Session(self.engine) as session:
            row = session.get(UserRow, user_id)
            if row is None:
                raise UserInputError("User not found.")
            duplicate = session.scalar(
                select(UserRow.id).where(UserRow.email == normalized_email, UserRow.id != user_id)
            )
            if duplicate is not None:
                raise UserInputError("A user with that email already exists.")
            removes_admin = row.role == ADMIN and (role != ADMIN or not active)
            if removes_admin and self._active_admin_count(session) <= 1:
                raise UserInputError("At least one active administrator is required.")
            row.name = clean_name
            row.email = normalized_email
            row.role = role
            row.active = active
            row.updated_at = utc_now()
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise UserInputError("A user with that email already exists.") from exc
            session.refresh(row)
            return user_from_row(row)

    def delete_user(self, actor: AppUser, user_id: int) -> None:
        require_admin(actor)
        if actor.id == user_id:
            raise UserInputError("You cannot delete your own account.")
        with Session(self.engine) as session:
            row = session.get(UserRow, user_id)
            if row is None:
                return
            if row.role == ADMIN and row.active and self._active_admin_count(session) <= 1:
                raise UserInputError("At least one active administrator is required.")
            session.delete(row)
            session.commit()

    def reset_password(self, actor: AppUser, user_id: int) -> str:
        require_admin(actor)
        temporary_password = generate_password()
        with Session(self.engine) as session:
            row = session.get(UserRow, user_id)
            if row is None:
                raise UserInputError("User not found.")
            row.password_hash = self.password_hasher.hash(temporary_password)
            row.must_change_password = True
            row.updated_at = utc_now()
            session.commit()
        return temporary_password

    def change_password(self, user: AppUser, current_password: str, new_password: str) -> AppUser:
        authenticated = self.authenticate(user.email, current_password)
        if authenticated is None or authenticated.id != user.id:
            raise UserInputError("Current password is incorrect.")
        validate_password(new_password)
        with Session(self.engine) as session:
            row = session.get(UserRow, user.id)
            if row is None or not row.active:
                raise AuthorizationError("This account is no longer active.")
            row.password_hash = self.password_hasher.hash(new_password)
            row.must_change_password = False
            row.updated_at = utc_now()
            session.commit()
            session.refresh(row)
            return user_from_row(row)

    def list_uploads(self) -> list[StoredUpload]:
        with Session(self.engine) as session:
            rows = session.scalars(select(UploadRow).order_by(UploadRow.updated_at.desc())).all()
            return [upload_from_row(row) for row in rows]

    def save_uploads(self, actor: AppUser, uploads: list[tuple[str, bytes]]) -> list[StoredUpload]:
        require_admin(actor)
        now = utc_now()
        saved: list[StoredUpload] = []
        with Session(self.engine) as session:
            for original_name, content in uploads:
                safe_name = safe_csv_name(original_name)
                digest = __import__("hashlib").sha256(content).hexdigest()
                stored_name = f"{digest}__{safe_name}"
                row = session.scalar(
                    select(UploadRow).where(func.lower(UploadRow.original_name) == safe_name.casefold())
                )
                if row is None:
                    row = UploadRow(
                        original_name=safe_name,
                        stored_name=stored_name,
                        digest=digest,
                        content=content,
                        uploaded_by=actor.id,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(row)
                else:
                    row.stored_name = stored_name
                    row.digest = digest
                    row.content = content
                    row.uploaded_by = actor.id
                    row.updated_at = now
                session.flush()
                saved.append(upload_from_row(row))
            session.commit()
        return saved

    def delete_upload(self, actor: AppUser, stored_name: str) -> bool:
        require_admin(actor)
        with Session(self.engine) as session:
            result = session.execute(delete(UploadRow).where(UploadRow.stored_name == stored_name))
            session.commit()
            return bool(result.rowcount)

    def delete_all_uploads(self, actor: AppUser) -> int:
        require_admin(actor)
        with Session(self.engine) as session:
            count = int(session.scalar(select(func.count()).select_from(UploadRow)) or 0)
            session.execute(delete(UploadRow))
            session.commit()
            return count

    @staticmethod
    def _active_admin_count(session: Session) -> int:
        return int(
            session.scalar(
                select(func.count()).select_from(UserRow).where(UserRow.role == ADMIN, UserRow.active.is_(True))
            )
            or 0
        )


def require_admin(user: AppUser) -> None:
    if not user.active or not user.is_admin:
        raise AuthorizationError("Administrator access is required.")


def generate_password(length: int = 18) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(char.islower() for char in password)
            and any(char.isupper() for char in password)
            and any(char.isdigit() for char in password)
            and any(char in "!@#$%" for char in password)
        ):
            return password


def validate_password(password: str) -> None:
    if len(password) < 12:
        raise UserInputError("Password must be at least 12 characters.")
    if not re.search(r"[a-z]", password) or not re.search(r"[A-Z]", password):
        raise UserInputError("Password must include upper- and lowercase letters.")
    if not re.search(r"\d", password) or not re.search(r"[^A-Za-z0-9]", password):
        raise UserInputError("Password must include a number and a symbol.")


def validate_name(name: str) -> str:
    clean_name = " ".join(name.split())
    if not clean_name or len(clean_name) > 160:
        raise UserInputError("Enter a valid name.")
    return clean_name


def normalize_email(email: str) -> str:
    normalized = email.strip().casefold()
    if len(normalized) > 320 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
        raise UserInputError("Enter a valid email address.")
    return normalized


def validate_role(role: str) -> None:
    if role not in ROLES:
        raise UserInputError("Invalid role.")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def user_from_row(row: UserRow) -> AppUser:
    return AppUser(
        id=row.id,
        name=row.name,
        email=row.email,
        role=row.role,
        active=row.active,
        must_change_password=row.must_change_password,
    )


def upload_from_row(row: UploadRow) -> StoredUpload:
    return StoredUpload(
        original_name=row.original_name,
        stored_name=row.stored_name,
        digest=row.digest,
        content=row.content,
        updated_at=row.updated_at,
    )


def safe_csv_name(original_name: str) -> str:
    from pathlib import Path

    name = Path(original_name).name.strip()
    stem = Path(name).stem or "upload"
    safe_stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", stem).strip(" ._") or "upload"
    return f"{safe_stem}.csv"
