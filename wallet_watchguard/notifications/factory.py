from __future__ import annotations

from typing import Any

from ..config import notification_provider_config
from ..nostr import ensure_nostr_helper_available
from ..ntfy import NtfyNotifier, decrypt_ntfy_config
from .manager import NotificationManager
from .ntfy_provider import NtfyNotificationProvider


def build_notification_manager(config: dict[str, Any], passphrase: str) -> tuple[NotificationManager, dict[str, Any]]:
    decrypted_ntfy_config = decrypt_ntfy_config(config["ntfy"], passphrase)

    providers = []
    ntfy_provider = notification_provider_config(config, "ntfy")
    if bool(ntfy_provider.get("enabled", True)):
        providers.append(NtfyNotificationProvider(NtfyNotifier(decrypted_ntfy_config)))

    nostr_provider = notification_provider_config(config, "nostr")
    if bool(nostr_provider.get("enabled", False)):
        # PR3 only wires up runtime capability checks. The real Nostr provider is
        # added once the Rust helper exists, but enabling Nostr on a slim image
        # should already fail with a clear message instead of silently doing
        # nothing.
        ensure_nostr_helper_available(config)

    return NotificationManager(providers), decrypted_ntfy_config
