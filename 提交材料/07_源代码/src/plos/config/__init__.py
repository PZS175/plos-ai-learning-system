"""Configuration module for PLOS AI."""

from .loader import Config, load_config, save_config
from .defaults import DEFAULT_CONFIG
from .hardware import (
    MODEL_PROFILES,
    detect_hardware,
    get_recommended_model_profile,
    get_recommended_settings,
)
from .schema import AppConfig

__all__ = [
    "Config",
    "AppConfig",
    "load_config",
    "save_config",
    "DEFAULT_CONFIG",
    "detect_hardware",
    "get_recommended_model_profile",
    "get_recommended_settings",
    "MODEL_PROFILES",
]
