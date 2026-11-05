#!/usr/bin/env python3
"""Shared deployment settings loader for local helper scripts."""
from dataclasses import dataclass
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


PROJECT_ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class DeploymentSettings:
    host: str
    user: str
    password: str
    port: int
    remote_path: str
    service_name: str


def _load_local_env_files():
    if load_dotenv is None:
        return

    for file_name in ("deployment.env", ".env.deploy", ".env"):
        env_path = PROJECT_ROOT / file_name
        if env_path.exists():
            load_dotenv(env_path, override=False)


def require_deployment_settings() -> DeploymentSettings:
    _load_local_env_files()

    host = os.getenv("BOOKING_DEPLOY_HOST")
    user = os.getenv("BOOKING_DEPLOY_USER", "root")
    password = os.getenv("BOOKING_DEPLOY_PASSWORD")
    port = int(os.getenv("BOOKING_DEPLOY_PORT", "22"))
    remote_path = os.getenv("BOOKING_REMOTE_PATH", "/opt/booking_bot")
    service_name = os.getenv("BOOKING_SERVICE_NAME", "booking-bot")

    missing = []
    if not host:
        missing.append("BOOKING_DEPLOY_HOST")
    if not password:
        missing.append("BOOKING_DEPLOY_PASSWORD")

    if missing:
        missing_list = ", ".join(missing)
        raise RuntimeError(
            f"Missing deployment settings: {missing_list}. "
            "Add them to deployment.env, .env.deploy, or your shell environment."
        )

    return DeploymentSettings(
        host=host,
        user=user,
        password=password,
        port=port,
        remote_path=remote_path,
        service_name=service_name,
    )
