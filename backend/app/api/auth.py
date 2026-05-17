from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db.dependencies import get_db
from app.models import AuditLog, LocalUser
from app.schemas.intake import (
    AuthResponse,
    AuthUserRead,
    BillingRateUpdate,
    LoginRequest,
    ManagedUserRead,
    PasswordChangeRequest,
    ProfileLogoUpdate,
    RegisterRequest,
)
from app.security import UserRole
from app.services.audit import AuditAction
from app.services.local_auth import (
    AuthError,
    authenticate_user,
    create_session,
    create_user,
    delete_session,
    get_authenticated_user,
    AuthenticatedUser,
    ensure_demo_user,
    hash_password,
    require_local_roles,
    verify_password,
)


router = APIRouter(prefix="/api/auth", tags=["local-auth"])


@router.post("/login", response_model=AuthResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> AuthResponse:
    try:
        user = authenticate_user(db=db, email=payload.email, password=payload.password)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        ) from exc
    token = create_session(db=db, user=user)
    return AuthResponse(token=token, user=AuthUserRead.model_validate(user))


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> AuthResponse:
    try:
        user = create_user(db=db, email=payload.email, password=payload.password)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already exists.",
        ) from exc
    token = create_session(db=db, user=user)
    return AuthResponse(token=token, user=AuthUserRead.model_validate(user))


@router.post("/logout")
def logout(
    authenticated: AuthenticatedUser = Depends(get_authenticated_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    delete_session(db=db, token=authenticated.token)
    return {"ok": True}


@router.get("/me", response_model=AuthUserRead)
def me(authenticated: AuthenticatedUser = Depends(get_authenticated_user)) -> AuthUserRead:
    return AuthUserRead.model_validate(authenticated.user)


@router.put("/password")
def update_password(
    payload: PasswordChangeRequest,
    db: Session = Depends(get_db),
    authenticated: AuthenticatedUser = Depends(get_authenticated_user),
) -> dict[str, bool]:
    if not verify_password(payload.current_password, authenticated.user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect.",
        )
    authenticated.user.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"ok": True}


@router.put("/profile-logo", response_model=AuthUserRead)
def update_profile_logo(
    payload: ProfileLogoUpdate,
    db: Session = Depends(get_db),
    authenticated: AuthenticatedUser = Depends(get_authenticated_user),
) -> AuthUserRead:
    logo_data_url = payload.university_logo_data_url
    if logo_data_url and not _is_allowed_logo_data_url(logo_data_url):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Logo must be a PNG, JPEG, or WebP image.",
        )
    authenticated.user.university_logo_data_url = logo_data_url
    db.commit()
    db.refresh(authenticated.user)
    return AuthUserRead.model_validate(authenticated.user)


@router.get("/users", response_model=list[ManagedUserRead])
def list_users(
    db: Session = Depends(get_db),
    authenticated: AuthenticatedUser = Depends(require_local_roles(UserRole.ADMIN)),
) -> list[ManagedUserRead]:
    _ = authenticated
    ensure_demo_user(db)
    users = list(db.scalars(select(LocalUser).order_by(LocalUser.created_at.asc())))
    return [_managed_user_read(db, user) for user in users]


@router.put("/users/{user_id}/billing-rate", response_model=ManagedUserRead)
def update_user_billing_rate(
    user_id: str,
    payload: BillingRateUpdate,
    db: Session = Depends(get_db),
    authenticated: AuthenticatedUser = Depends(require_local_roles(UserRole.ADMIN)),
) -> ManagedUserRead:
    _ = authenticated
    user = db.get(LocalUser, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    user.billing_rate_per_unit = payload.billing_rate_per_unit
    db.commit()
    db.refresh(user)
    return _managed_user_read(db, user)


def _managed_user_read(db: Session, user: LocalUser) -> ManagedUserRead:
    document_count = db.scalar(
        select(func.count(distinct(AuditLog.document_id))).where(
            AuditLog.actor == user.email,
            AuditLog.action == AuditAction.DOCUMENT_UPLOAD,
            AuditLog.document_id.is_not(None),
        )
    ) or 0
    rubric_count = db.scalar(
        select(func.count(AuditLog.audit_log_id)).where(
            AuditLog.actor == user.email,
            AuditLog.action == AuditAction.RUBRIC_SAVE,
        )
    ) or 0
    return ManagedUserRead(
        user_id=user.user_id,
        email=user.email,
        role=user.role,
        verification_status="verified",
        document_count=document_count,
        rubric_count=rubric_count,
        billing_rate_per_unit=user.billing_rate_per_unit,
        university_logo_data_url=user.university_logo_data_url,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


def _is_allowed_logo_data_url(value: str) -> bool:
    allowed_prefixes = (
        "data:image/png;base64,",
        "data:image/jpeg;base64,",
        "data:image/webp;base64,",
    )
    return value.startswith(allowed_prefixes)
