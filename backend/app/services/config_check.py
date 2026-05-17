from __future__ import annotations

from app.config import REQUIRED_CONFIG_KEYS, SECRET_CONFIG_KEYS, AppSettings
from app.schemas.system import ConfigCheckResponse, ConfigRequirementStatus


def build_config_check(settings: AppSettings) -> ConfigCheckResponse:
    required = [
        ConfigRequirementStatus(
            name=env_name,
            configured=settings.is_configured(env_name),
            secret=env_name in SECRET_CONFIG_KEYS,
        )
        for env_name in REQUIRED_CONFIG_KEYS
    ]
    missing = [item.name for item in required if not item.configured]

    return ConfigCheckResponse(
        ready=not missing
        and settings.require_human_final_decision
        and settings.ai_decision_support_only,
        environment=settings.app_env,
        required=required,
        missing=missing,
        safeguards={
            "require_human_final_decision": settings.require_human_final_decision,
            "ai_decision_support_only": settings.ai_decision_support_only,
        },
    )
