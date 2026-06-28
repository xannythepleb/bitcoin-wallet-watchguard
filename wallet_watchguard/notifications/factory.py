from __future__ import annotations

from typing import Any

from ..ntfy import NtfyNotifier, decrypt_ntfy_config
from .manager import NotificationManager
from .ntfy_provider import NtfyNotificationProvider


def build_notification_manager(config: dict[str, Any], passphrase: str) -> tuple[NotificationManager, dict[str, Any]]:
    decrypted_ntfy_config = decrypt_ntfy_config(config["ntfy"], passphrase)
    manager = NotificationManager([NtfyNotificationProvider(NtfyNotifier(decrypted_ntfy_config))])
    return manager, decrypted_ntfy_config
