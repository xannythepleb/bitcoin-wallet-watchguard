from __future__ import annotations

import logging
from typing import Any

from ..config import notification_provider_config
from ..nostr import decrypt_nostr_sender_nsec, ensure_nostr_helper_available
from ..ntfy import NtfyNotifier, decrypt_ntfy_config
from .manager import NotificationManager
from .nostr_provider import NostrNotificationProvider
from .ntfy_provider import NtfyNotificationProvider

logger = logging.getLogger("wwg.notifications")


def build_notification_manager(config: dict[str, Any], passphrase: str) -> tuple[NotificationManager, dict[str, Any]]:
    providers = []

    decrypted_ntfy_config = dict(config["ntfy"])
    ntfy_provider = notification_provider_config(config, "ntfy")
    if bool(ntfy_provider.get("enabled", True)):
        decrypted_ntfy_config = decrypt_ntfy_config(config["ntfy"], passphrase)
        providers.append(NtfyNotificationProvider(NtfyNotifier(decrypted_ntfy_config)))
        logger.info("ntfy notification provider enabled")

    nostr_provider = notification_provider_config(config, "nostr")
    if bool(nostr_provider.get("enabled", False)):
        availability = ensure_nostr_helper_available(config)
        sender_nsec = decrypt_nostr_sender_nsec(nostr_provider, passphrase)
        sender = nostr_provider.get("sender") or {}
        providers.append(
            NostrNotificationProvider(
                helper_path=availability.resolved_path or availability.configured_path,
                sender_nsec=sender_nsec,
                recipients=list(nostr_provider.get("recipients") or []),
                relays=list(nostr_provider.get("relays") or []),
                min_successful_relays=int(nostr_provider.get("min_successful_relays", 1)),
                send_copy_to_self=bool(nostr_provider.get("send_copy_to_self", True)),
                connect_timeout_seconds=int(nostr_provider.get("connect_timeout_seconds", 10)),
                process_timeout_seconds=int(nostr_provider.get("timeout_seconds", 30)),
                configured_sender_npub=(
                    str(sender.get("npub") or "") if isinstance(sender, dict) else ""
                ),
            )
        )
        logger.info(
            "Nostr notification provider enabled with %d recipient(s) and %d relay(s)",
            len(nostr_provider.get("recipients") or []),
            len(nostr_provider.get("relays") or []),
        )

    logger.info("Notification manager ready with %d active provider(s)", len(providers))
    return NotificationManager(providers), decrypted_ntfy_config
