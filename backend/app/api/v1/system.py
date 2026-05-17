from __future__ import annotations

from fastapi import APIRouter, Depends

from app.config import AppSettings, get_settings
from app.schemas.system import ConfigCheckResponse
from app.services.config_check import build_config_check


router = APIRouter()


@router.get("/config-check", response_model=ConfigCheckResponse)
def config_check(settings: AppSettings = Depends(get_settings)) -> ConfigCheckResponse:
    return build_config_check(settings)
