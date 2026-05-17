from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from fastapi import Depends, HTTPException, Request, status
from starlette.responses import JSONResponse

from app.config import AppSettings


class UserRole(str, Enum):
    ADMIN = "admin"
    ADMISSIONS_REVIEWER = "admissions_reviewer"
    READ_ONLY_AUDITOR = "read_only_auditor"


@dataclass(frozen=True)
class AuthenticatedActor:
    actor_id: str
    role: UserRole
    auth_mode: str = "development"


WRITE_ROLES: tuple[UserRole, ...] = (
    UserRole.ADMIN,
    UserRole.ADMISSIONS_REVIEWER,
)
READ_ROLES: tuple[UserRole, ...] = (
    UserRole.ADMIN,
    UserRole.ADMISSIONS_REVIEWER,
    UserRole.READ_ONLY_AUDITOR,
)


def actor_from_request_headers(request: Request, settings: AppSettings) -> AuthenticatedActor:
    actor_id = request.headers.get("x-dev-actor") or settings.dev_auth_default_actor
    role_value = request.headers.get("x-dev-role") or settings.dev_auth_default_role
    try:
        role = UserRole(role_value)
    except ValueError as exc:
        allowed = ", ".join(role.value for role in UserRole)
        raise ValueError(f"Unsupported development role '{role_value}'. Allowed roles: {allowed}.") from exc

    return AuthenticatedActor(
        actor_id=actor_id.strip() or settings.dev_auth_default_actor,
        role=role,
        auth_mode="development",
    )


def system_actor() -> AuthenticatedActor:
    return AuthenticatedActor(
        actor_id="system",
        role=UserRole.ADMIN,
        auth_mode="system",
    )


def actor_with_id(actor: AuthenticatedActor, actor_id: str | None) -> AuthenticatedActor:
    if not actor_id:
        return actor
    return AuthenticatedActor(
        actor_id=actor_id,
        role=actor.role,
        auth_mode=actor.auth_mode,
    )


def get_current_actor(request: Request) -> AuthenticatedActor:
    actor = getattr(request.state, "current_actor", None)
    if isinstance(actor, AuthenticatedActor):
        return actor
    return system_actor()


def require_roles(*roles: UserRole) -> Callable[[AuthenticatedActor], AuthenticatedActor]:
    allowed_roles = set(roles)

    def dependency(
        actor: AuthenticatedActor = Depends(get_current_actor),
    ) -> AuthenticatedActor:
        if actor.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Actor role is not allowed to perform this action.",
            )
        return actor

    return dependency


async def development_auth_middleware(request: Request, call_next, settings: AppSettings):
    """Attach a replaceable development actor to each request.

    Production authentication should replace this with identity-provider
    integration. The middleware intentionally uses headers only for local and
    test workflows.
    """

    if settings.dev_auth_enabled:
        try:
            request.state.current_actor = actor_from_request_headers(request, settings)
        except ValueError as exc:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": str(exc)},
            )
    else:
        request.state.current_actor = system_actor()
    return await call_next(request)
