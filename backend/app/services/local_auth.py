from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.dependencies import get_db
from app.models import AuthSession, LocalUser
from app.security import AuthenticatedActor, UserRole


DEMO_EMAIL = "superadmin"
DEMO_PASSWORD = "EverydayAI"
HASH_ITERATIONS = 120_000


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class AuthenticatedUser:
    user: LocalUser
    token: str

    @property
    def actor(self) -> AuthenticatedActor:
        return AuthenticatedActor(
            actor_id=self.user.email,
            role=UserRole(self.user.role),
            auth_mode="local_session",
        )


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        HASH_ITERATIONS,
    ).hex()
    return f"pbkdf2_sha256${HASH_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations_text, salt, expected = stored_hash.split("$", 3)
        iterations = int(iterations_text)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return hmac.compare_digest(actual, expected)


def ensure_demo_user(db: Session) -> LocalUser:
    from app.models.admissions import University
    default_uni = db.scalar(select(University).where(University.name == "Default University"))
    if default_uni is None:
        default_uni = University(
            name="Default University",
            logo_data_url=None,
            pages_per_billable_unit=1,
        )
        db.add(default_uni)
        db.commit()
        db.refresh(default_uni)

    user = db.scalar(select(LocalUser).where(LocalUser.email == DEMO_EMAIL))
    if user is not None:
        dirty = False
        if user.role != UserRole.SUPERADMIN.value:
            user.role = UserRole.SUPERADMIN.value
            dirty = True
        if user.university_id != default_uni.university_id:
            user.university_id = default_uni.university_id
            dirty = True
        if dirty:
            db.commit()
            db.refresh(user)
        return user
    user = LocalUser(
        email=DEMO_EMAIL,
        password_hash=hash_password(DEMO_PASSWORD),
        role=UserRole.SUPERADMIN.value,
        university_id=default_uni.university_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_user(*, db: Session, email: str, password: str, role: UserRole = UserRole.ADMISSIONS_REVIEWER) -> LocalUser:
    existing = db.scalar(select(LocalUser).where(LocalUser.email == email.strip()))
    if existing is not None:
        raise AuthError("User already exists.")
    user = LocalUser(
        email=email.strip(),
        password_hash=hash_password(password),
        role=role.value,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(*, db: Session, email: str, password: str) -> LocalUser:
    ensure_demo_user(db)
    user = db.scalar(select(LocalUser).where(LocalUser.email == email.strip()))
    if user is None or not verify_password(password, user.password_hash):
        raise AuthError("Invalid email or password.")
    return user


def create_session(*, db: Session, user: LocalUser) -> str:
    token = secrets.token_urlsafe(48)
    user.last_login_at = datetime.now(timezone.utc)
    db.add(AuthSession(token=token, user_id=user.user_id))
    db.commit()
    return token


def delete_session(*, db: Session, token: str) -> None:
    session = db.get(AuthSession, token)
    if session is not None:
        db.delete(session)
        db.commit()


def user_for_token(*, db: Session, token: str) -> LocalUser | None:
    session = db.get(AuthSession, token)
    if session is None:
        return None
    return session.user


def bearer_token_from_header(authorization: str | None) -> str | None:
    if not authorization:
        return None
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        return None
    token = authorization[len(prefix) :].strip()
    return token or None


def get_authenticated_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> AuthenticatedUser:
    if authorization and authorization.startswith("ApiKey "):
        api_key = authorization[len("ApiKey ") :].strip()
        from app.models.admissions import University
        university = db.scalar(select(University).where(University.api_key == api_key))
        if university is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API Key.")
        admin_user = db.scalar(
            select(LocalUser)
            .where(LocalUser.university_id == university.university_id, LocalUser.role == UserRole.ADMIN.value)
        )
        if admin_user is None:
            admin_user = db.scalar(
                select(LocalUser).where(LocalUser.university_id == university.university_id)
            )
        if admin_user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No user associated with university API key.")
        return AuthenticatedUser(user=admin_user, token=api_key)

    token = bearer_token_from_header(authorization)
    if token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    user = user_for_token(db=db, token=token)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session.")
    return AuthenticatedUser(user=user, token=token)


def require_local_roles(*roles: UserRole):
    allowed = set(roles)
    allowed.add(UserRole.SUPERADMIN)

    def dependency(authenticated: AuthenticatedUser = Depends(get_authenticated_user)) -> AuthenticatedUser:
        if UserRole(authenticated.user.role) not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Actor role is not allowed to perform this action.",
            )
        return authenticated

    return dependency
