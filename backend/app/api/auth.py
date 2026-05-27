from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status, Response
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
    UniversityCreate,
    UniversityRead,
    UniversityIntegrationUpdate,
    ReviewerCreateRequest,
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
    read = AuthUserRead.model_validate(user)
    if user.university_id:
        from app.models.admissions import University
        uni = db.get(University, user.university_id)
        if uni:
            read.university_logo_data_url = uni.logo_data_url
            read.pages_per_billable_unit = uni.pages_per_billable_unit
    return AuthResponse(token=token, user=read)


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> AuthResponse:
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Public self-registration is disabled. Please contact your administrator to provision an account."
    )


@router.post("/logout")
def logout(
    authenticated: AuthenticatedUser = Depends(get_authenticated_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    delete_session(db=db, token=authenticated.token)
    return {"ok": True}


@router.get("/me", response_model=AuthUserRead)
def me(
    db: Session = Depends(get_db),
    authenticated: AuthenticatedUser = Depends(get_authenticated_user),
) -> AuthUserRead:
    user = authenticated.user
    read = AuthUserRead.model_validate(user)
    if user.university_id:
        from app.models.admissions import University
        uni = db.get(University, user.university_id)
        if uni:
            read.university_logo_data_url = uni.logo_data_url
            read.pages_per_billable_unit = uni.pages_per_billable_unit
    return read


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
    
    user = authenticated.user
    if not user.university_id:
        raise HTTPException(status_code=400, detail="User does not belong to a university.")
        
    if UserRole(user.role) not in {UserRole.SUPERADMIN, UserRole.ADMIN}:
        raise HTTPException(status_code=403, detail="Only Admins can update branding logo.")
        
    from app.models.admissions import University
    uni = db.get(University, user.university_id)
    if not uni:
        raise HTTPException(status_code=404, detail="University not found.")
        
    uni.logo_data_url = logo_data_url
    db.commit()
    db.refresh(uni)
    
    read = AuthUserRead.model_validate(user)
    read.university_logo_data_url = uni.logo_data_url
    return read


@router.get("/users", response_model=list[ManagedUserRead])
def list_users(
    db: Session = Depends(get_db),
    authenticated: AuthenticatedUser = Depends(require_local_roles(UserRole.SUPERADMIN, UserRole.ADMIN)),
) -> list[ManagedUserRead]:
    user = authenticated.user
    ensure_demo_user(db)
    
    if UserRole(user.role) == UserRole.SUPERADMIN:
        users = list(db.scalars(select(LocalUser).order_by(LocalUser.created_at.asc())).all())
    else:
        users = list(
            db.scalars(
                select(LocalUser)
                .where(LocalUser.university_id == user.university_id)
                .order_by(LocalUser.created_at.asc())
            ).all()
        )
        
    return [_managed_user_read(db, u) for u in users]


@router.put("/users/{user_id}/billing-rate", response_model=ManagedUserRead)
def update_user_billing_rate(
    user_id: str,
    payload: BillingRateUpdate,
    db: Session = Depends(get_db),
    authenticated: AuthenticatedUser = Depends(require_local_roles(UserRole.SUPERADMIN)),
) -> ManagedUserRead:
    _ = authenticated
    user = db.get(LocalUser, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    user.billing_rate_per_unit = payload.billing_rate_per_unit
    db.commit()
    db.refresh(user)
    return _managed_user_read(db, user)


@router.get("/universities", response_model=list[UniversityRead])
def list_universities(
    db: Session = Depends(get_db),
    authenticated: AuthenticatedUser = Depends(require_local_roles(UserRole.SUPERADMIN)),
) -> list[UniversityRead]:
    from app.models.admissions import University
    return list(db.scalars(select(University).order_by(University.created_at.asc())).all())


@router.post("/universities", response_model=UniversityRead, status_code=status.HTTP_201_CREATED)
def create_university(
    payload: UniversityCreate,
    db: Session = Depends(get_db),
    authenticated: AuthenticatedUser = Depends(require_local_roles(UserRole.SUPERADMIN)),
) -> UniversityRead:
    from app.models.admissions import University
    existing = db.scalar(select(University).where(University.name == payload.name.strip()))
    if existing:
        raise HTTPException(status_code=400, detail="University already exists.")
        
    uni = University(
        name=payload.name.strip(),
        logo_data_url=None,
        pages_per_billable_unit=1,
    )
    db.add(uni)
    db.commit()
    db.refresh(uni)
    
    # Provision initial Admin
    default_admin_email = f"admin@{uni.name.lower().replace(' ', '')}.edu"
    requested_email = (payload.admin_email or "").strip().lower()
    admin_email = requested_email or default_admin_email
    if "@" not in admin_email:
        raise HTTPException(status_code=400, detail="Provisioned admin email must include '@'.")
    existing_admin = db.scalar(select(LocalUser).where(LocalUser.email == admin_email))
    if existing_admin:
        raise HTTPException(status_code=400, detail="A user with that admin email already exists.")

    admin_password = "EverydayAI"
    admin = LocalUser(
        email=admin_email,
        password_hash=hash_password(admin_password),
        role=UserRole.ADMIN.value,
        university_id=uni.university_id,
    )
    db.add(admin)
    db.commit()
    
    return uni


@router.post("/users/create-reviewer", response_model=ManagedUserRead)
def create_reviewer(
    payload: ReviewerCreateRequest,
    db: Session = Depends(get_db),
    authenticated: AuthenticatedUser = Depends(require_local_roles(UserRole.ADMIN)),
) -> ManagedUserRead:
    user = authenticated.user
    if not user.university_id:
        raise HTTPException(status_code=400, detail="Admin does not belong to a university.")
        
    existing = db.scalar(select(LocalUser).where(LocalUser.email == payload.email.strip()))
    if existing:
        raise HTTPException(status_code=400, detail="Reviewer already exists.")
        
    new_user = LocalUser(
        email=payload.email.strip(),
        password_hash=hash_password(payload.password),
        role=UserRole.ADMISSIONS_REVIEWER.value,
        university_id=user.university_id,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return _managed_user_read(db, new_user)


@router.put("/universities/integration", response_model=UniversityRead)
def update_university_integration(
    payload: UniversityIntegrationUpdate,
    db: Session = Depends(get_db),
    authenticated: AuthenticatedUser = Depends(require_local_roles(UserRole.ADMIN)),
) -> UniversityRead:
    user = authenticated.user
    if not user.university_id:
        raise HTTPException(status_code=400, detail="Admin does not belong to a university.")
        
    from app.models.admissions import University
    uni = db.get(University, user.university_id)
    if not uni:
        raise HTTPException(status_code=404, detail="University not found.")
        
    uni.webhook_url = payload.webhook_url
    db.commit()
    db.refresh(uni)
    return uni


@router.post("/universities/rotate-key", response_model=UniversityRead)
def rotate_university_key(
    db: Session = Depends(get_db),
    authenticated: AuthenticatedUser = Depends(require_local_roles(UserRole.ADMIN)),
) -> UniversityRead:
    user = authenticated.user
    if not user.university_id:
        raise HTTPException(status_code=400, detail="Admin does not belong to a university.")
        
    from app.models.admissions import University
    uni = db.get(University, user.university_id)
    if not uni:
        raise HTTPException(status_code=404, detail="University not found.")
        
    import secrets
    uni.api_key = secrets.token_hex(32)
    db.commit()
    db.refresh(uni)
    return uni


@router.put("/universities/{university_id}/pages-per-unit", response_model=UniversityRead)
def update_pages_per_unit(
    university_id: str,
    pages_per_billable_unit: int,
    db: Session = Depends(get_db),
    authenticated: AuthenticatedUser = Depends(require_local_roles(UserRole.SUPERADMIN)),
) -> UniversityRead:
    from app.models.admissions import University
    uni = db.get(University, university_id)
    if not uni:
        raise HTTPException(status_code=404, detail="University not found.")
    if pages_per_billable_unit < 1:
        raise HTTPException(status_code=400, detail="Pages per billable unit must be at least 1.")
        
    uni.pages_per_billable_unit = pages_per_billable_unit
    db.commit()
    db.refresh(uni)
    return uni


@router.delete("/universities/{university_id}", response_class=Response, status_code=status.HTTP_204_NO_CONTENT)
def delete_university(
    university_id: str,
    db: Session = Depends(get_db),
    authenticated: AuthenticatedUser = Depends(require_local_roles(UserRole.SUPERADMIN)),
) -> Response:
    from app.models.admissions import University, Application, SavedRubric, LocalUser, IntakeJob
    from sqlalchemy import delete
    
    uni = db.get(University, university_id)
    if not uni:
        raise HTTPException(status_code=404, detail="University not found.")
    
    if uni.name == "Default University":
        raise HTTPException(status_code=400, detail="Default University cannot be deleted.")

    # 1. Fetch and cascade delete all applications and related models
    apps = db.scalars(select(Application).where(Application.university_id == university_id)).all()
    for app in apps:
        db.execute(delete(IntakeJob).where(IntakeJob.application_id == app.application_id))
        db.delete(app)
    
    # 2. Delete all saved rubrics
    db.execute(delete(SavedRubric).where(SavedRubric.university_id == university_id))
    
    # 3. Delete all local users (AuthSessions will be deleted by SQLAlchemy cascade on LocalUser.sessions)
    users = db.scalars(select(LocalUser).where(LocalUser.university_id == university_id)).all()
    for u in users:
        db.delete(u)
    
    # 4. Delete the university itself
    db.delete(uni)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)



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
    
    logo_data_url = user.university_logo_data_url
    if user.university_id:
        from app.models.admissions import University
        uni = db.get(University, user.university_id)
        if uni:
            logo_data_url = uni.logo_data_url
            
    return ManagedUserRead(
        user_id=user.user_id,
        email=user.email,
        role=user.role,
        verification_status="verified",
        document_count=document_count,
        rubric_count=rubric_count,
        billing_rate_per_unit=user.billing_rate_per_unit,
        university_logo_data_url=logo_data_url,
        university_id=user.university_id,
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
