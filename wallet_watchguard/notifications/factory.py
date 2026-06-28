from __future__ import annotations

from typing import Any

from ..config import notification_provider_config
from ..ntfy import NtfyNotifier, decrypt_ntfy_config
from .manager import NotificationManager
from .ntfy_provider import NtfyNotificationProvider


def build_notification_manager(config: dict[str, Any], passphrase: str) -> tuple[NotificationManager, dict[str, Any]]:
    decrypted_ntfy_config = decrypt_ntfy_config(config["ntfy"], passphrase)

    providers = []
    ntfy_provider = notification_provider_config(config, "ntfy")
    if bool(ntfy_provider.get("enabled", True)):
        providers.append(NtfyNotificationProvider(NtfyNotifier(decrypted_ntfy_config)))

    return NotificationManager(providers), decrypted_ntfy_config
