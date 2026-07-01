from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import notification_provider_config
from .crypto import decrypt_string_with_passphrase, metadata_from_config

logger = logging.getLogger("wwg.nostr")

NOSTR_HELPER_BINARY = "wwg-nostr"
DEFAULT_NOSTR_HELPER_PATH = f"./{NOSTR_HELPER_BINARY}"


class NostrSupportUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class NostrHelperAvailability:
    configured_path: str
    available: bool
    resolved_path: str | None = None
    source: str | None = None
    detail: str = ""

    @property
    def display_path(self) -> str:
        return self.resolved_path or self.configured_path

    @property
    def status_text(self) -> str:
        if self.available:
            source = f" via {self.source}" if self.source else ""
            return f"available{source}: {self.resolved_path}"
        return f"unavailable: {self.detail}" if self.detail else "unavailable"


def _configured_helper_path(nostr_config: dict[str, Any] | None) -> str:
    value = str((nostr_config or {}).get("helper_path") or DEFAULT_NOSTR_HELPER_PATH).strip()
    return value or DEFAULT_NOSTR_HELPER_PATH


def nostr_helper_availability(nostr_config: dict[str, Any] | None = None) -> NostrHelperAvailability:
    configured_path = _configured_helper_path(nostr_config)
    configured_candidate = Path(configured_path).expanduser()

    if configured_candidate.exists():
        if configured_candidate.is_file() and os.access(configured_candidate, os.X_OK):
            return NostrHelperAvailability(
                configured_path=configured_path,
                available=True,
                resolved_path=str(configured_candidate),
                source="configured path",
            )

        detail = f"configured helper exists but is not an executable file: {configured_candidate}"
        return NostrHelperAvailability(configured_path=configured_path, available=False, detail=detail)

    path_candidate = shutil.which(NOSTR_HELPER_BINARY)
    if path_candidate:
        return NostrHelperAvailability(
            configured_path=configured_path,
            available=True,
            resolved_path=path_candidate,
            source="PATH",
        )

    detail = f"{NOSTR_HELPER_BINARY} was not found at {configured_path} or on PATH"
    return NostrHelperAvailability(configured_path=configured_path, available=False, detail=detail)


def nostr_helper_availability_from_config(config: dict[str, Any]) -> NostrHelperAvailability:
    return nostr_helper_availability(notification_provider_config(config, "nostr"))


def nostr_support_unavailable_message(availability: NostrHelperAvailability) -> str:
    detail = availability.detail or f"{NOSTR_HELPER_BINARY} was not found at {availability.configured_path} or on PATH"
    return (
        "Nostr support is not available in this build.\n"
        f"{detail}.\n"
        "Slim Docker images are ntfy-only. Use the full image to enable Nostr notifications, "
        "or disable the Nostr provider in config.yaml."
    )


def decrypt_nostr_sender_nsec(nostr_config: dict[str, Any], passphrase: str) -> str:
    sender = nostr_config.get("sender") or {}
    if not isinstance(sender, dict):
        raise ValueError("notifications.providers.nostr.sender must be an object")

    encrypted_nsec = str(sender.get("encrypted_nsec") or "").strip()
    if not encrypted_nsec:
        raise ValueError(
            "Nostr notifications are enabled but no encrypted sender nsec is configured"
        )

    encryption_config = sender.get("nsec_encryption") or {}
    if not isinstance(encryption_config, dict):
        raise ValueError(
            "notifications.providers.nostr.sender.nsec_encryption must be an object"
        )

    return decrypt_string_with_passphrase(
        encrypted_value_b64=encrypted_nsec,
        passphrase=passphrase,
        metadata=metadata_from_config(encryption_config),
        secret_name="Nostr sender nsec",
    )


def ensure_nostr_helper_available(config: dict[str, Any]) -> NostrHelperAvailability:
    availability = nostr_helper_availability_from_config(config)
    if not availability.available:
        logger.warning("Nostr helper unavailable: %s", availability.detail)
        raise NostrSupportUnavailable(nostr_support_unavailable_message(availability))
    logger.debug("Nostr helper %s (%s)", availability.resolved_path, availability.source)
    return availability