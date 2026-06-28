from __future__ import annotations
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from .crypto import SCHEME


# Placeholder topic written into a brand new config. It is intentionally not a
# usable topic: the setup wizard treats it as "no topic chosen yet" and never
# shows it to the user.
PLACEHOLDER_NTFY_TOPIC = "wallet-watchguard-replace-me"

# Wallet Watchguard keeps its persistent local state under one data directory.
# Local users default to ./data. Docker users can set WWG_DATA_DIR=/app/data,
# which should point at the Docker-managed named volume.
DEFAULT_DATA_DIR = os.getenv("WWG_DATA_DIR", "./data")
DEFAULT_CONFIG_PATH = f"{DEFAULT_DATA_DIR}/config.yaml"
DEFAULT_DATABASE_PATH = f"{DEFAULT_DATA_DIR}/watchguard.sqlite3"

# The notification provider section is intentionally separate from the existing
# top level ntfy section for now. Existing configs that only have `ntfy:` keep
# behaving as ntfy-enabled, while newer configs can opt into additional providers
# without moving the ntfy auth/configuration fields yet.
DEFAULT_NOTIFICATION_PROVIDERS: dict[str, Any] = {
    "ntfy": {
        "enabled": True,
    },
    "nostr": {
        "enabled": False,
        "helper_path": "./wwg-nostr",
        "sender": {
            "encrypted_nsec": "",
            "nsec_encryption": {},
        },
        "recipients": [],
        "relays": [],
        "min_successful_relays": 1,
        "send_copy_to_self": True,
    },
}


class ConfigError(ValueError):
    pass


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """
    Merge override into base recursively and return base.

    Lists are replaced rather than merged. This is useful for config editing,
    where sections such as wallets should remain exactly as written by the user.
    """
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    validate_config(data)
    return data


def load_config_for_edit(path: str | Path) -> dict[str, Any]:
    """
    Load a config file for editing/upgrading without strict validation.

    `wwg run` should stay strict, but `wwg init` needs to be able to repair,
    extend, or migrate older/partial config files.
    """
    config_path = Path(path)
    if not config_path.exists():
        return default_config()

    if config_path.is_dir():
        raise ConfigError(f"Config path is a directory, not a file: {config_path}")

    with config_path.open("r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}

    if not isinstance(loaded, dict):
        raise ConfigError("Config YAML must contain an object at the top level")

    return _deep_merge(default_config(), loaded)


def save_config(path: str | Path, config: dict[str, Any]) -> None:
    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    with config_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, sort_keys=False)


def _validate_encryption_metadata(config: dict[str, Any], label: str) -> None:
    if not isinstance(config, dict):
        raise ConfigError(f"{label} encryption metadata must be an object")

    for key in ["scheme", "kdf", "opslimit", "memlimit", "salt"]:
        if key not in config:
            raise ConfigError(f"{label} encryption metadata is missing required field: {key}")

    if config["scheme"] != SCHEME:
        raise ConfigError(f"Unsupported {label} encryption scheme: {config['scheme']}")

    if config["kdf"] != "argon2id":
        raise ConfigError(f"Unsupported {label} KDF: {config['kdf']}")


def _validate_boolish(config: dict[str, Any], key: str, label: str) -> None:
    if key in config and not isinstance(config[key], bool):
        raise ConfigError(f"{label}.{key} must be true or false")


def _validate_int_at_least(config: dict[str, Any], key: str, label: str, minimum: int) -> None:
    if key not in config:
        return

    value = config[key]
    if isinstance(value, bool):
        raise ConfigError(f"{label}.{key} must be an integer")

    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{label}.{key} must be an integer") from exc

    if parsed < minimum:
        raise ConfigError(f"{label}.{key} must be at least {minimum}")


def notification_providers_config(config: dict[str, Any]) -> dict[str, Any]:
    """
    Return notification providers merged with backwards-compatible defaults.

    Older configs do not have a `notifications:` section. Treat those as
    ntfy-enabled so existing installs keep their current behaviour until the
    setup wizard writes the newer config shape.
    """
    notifications = config.get("notifications") or {}
    if isinstance(notifications, dict):
        providers = notifications.get("providers") or {}
    else:
        providers = {}

    merged = deepcopy(DEFAULT_NOTIFICATION_PROVIDERS)
    if isinstance(providers, dict):
        _deep_merge(merged, providers)
    return merged


def notification_provider_config(config: dict[str, Any], provider_name: str) -> dict[str, Any]:
    return notification_providers_config(config).get(provider_name, {})


def _validate_nostr_recipient(recipient: Any, index: int) -> None:
    label = f"notifications.providers.nostr.recipients[{index}]"

    if isinstance(recipient, str):
        npub = recipient.strip()
    elif isinstance(recipient, dict):
        npub = str(recipient.get("npub") or "").strip()
        if "name" in recipient and not isinstance(recipient["name"], str):
            raise ConfigError(f"{label}.name must be a string")
        if "relays" in recipient:
            _validate_relay_list(recipient["relays"], f"{label}.relays")
    else:
        raise ConfigError(f"{label} must be an npub string or an object")

    if not npub.startswith("npub1"):
        raise ConfigError(f"{label} must contain a recipient npub")


def _validate_relay_list(relays: Any, label: str) -> None:
    if not isinstance(relays, list):
        raise ConfigError(f"{label} must be a list")

    for index, relay in enumerate(relays):
        if not isinstance(relay, str) or not relay.strip():
            raise ConfigError(f"{label}[{index}] must be a non-blank relay URL")

        relay_url = relay.strip()
        if not relay_url.startswith(("wss://", "ws://")):
            raise ConfigError(f"{label}[{index}] must start with wss:// or ws://")


def _validate_notifications(config: dict[str, Any]) -> None:
    notifications = config.get("notifications")
    if notifications is None:
        return

    if not isinstance(notifications, dict):
        raise ConfigError("notifications must be an object")

    providers = notifications.get("providers")
    if providers is None:
        return

    if not isinstance(providers, dict):
        raise ConfigError("notifications.providers must be an object")

    unsupported = sorted(set(providers) - set(DEFAULT_NOTIFICATION_PROVIDERS))
    if unsupported:
        raise ConfigError(f"Unsupported notification provider: {', '.join(unsupported)}")

    ntfy_provider = providers.get("ntfy", {})
    if not isinstance(ntfy_provider, dict):
        raise ConfigError("notifications.providers.ntfy must be an object")
    _validate_boolish(ntfy_provider, "enabled", "notifications.providers.ntfy")

    nostr_provider = providers.get("nostr", {})
    if not isinstance(nostr_provider, dict):
        raise ConfigError("notifications.providers.nostr must be an object")
    _validate_boolish(nostr_provider, "enabled", "notifications.providers.nostr")
    _validate_boolish(nostr_provider, "send_copy_to_self", "notifications.providers.nostr")
    _validate_int_at_least(nostr_provider, "min_successful_relays", "notifications.providers.nostr", 1)

    helper_path = nostr_provider.get("helper_path")
    if helper_path is not None and (not isinstance(helper_path, str) or not helper_path.strip()):
        raise ConfigError("notifications.providers.nostr.helper_path must be a non-blank string")

    sender = nostr_provider.get("sender", {})
    if not isinstance(sender, dict):
        raise ConfigError("notifications.providers.nostr.sender must be an object")
    if str(sender.get("encrypted_nsec") or ""):
        if "nsec_encryption" not in sender:
            raise ConfigError("notifications.providers.nostr.sender has encrypted_nsec but no nsec_encryption")
        _validate_encryption_metadata(sender["nsec_encryption"], "Nostr nsec")

    recipients = nostr_provider.get("recipients") or []
    if not isinstance(recipients, list):
        raise ConfigError("notifications.providers.nostr.recipients must be a list")
    for index, recipient in enumerate(recipients):
        _validate_nostr_recipient(recipient, index)

    relays = nostr_provider.get("relays") or []
    _validate_relay_list(relays, "notifications.providers.nostr.relays")

    if bool(nostr_provider.get("enabled", False)):
        if not recipients:
            raise ConfigError("notifications.providers.nostr.enabled is true but no recipients are configured")
        if not relays:
            raise ConfigError("notifications.providers.nostr.enabled is true but no relays are configured")


def validate_config(config: dict[str, Any]) -> None:
    for key in ["app", "electrum", "ntfy", "wallets"]:
        if key not in config:
            raise ConfigError(f"Missing required config section: {key}")

    _validate_notifications(config)

    electrum = config["electrum"]
    for key in ["host", "port", "tls"]:
        if key not in electrum:
            raise ConfigError(f"electrum config is missing required field: {key}")
    _validate_boolish(electrum, "tls", "electrum")
    _validate_boolish(electrum, "tls_verify", "electrum")

    ntfy = config["ntfy"]
    for key in ["server", "topic", "auth"]:
        if key not in ntfy:
            raise ConfigError(f"ntfy config is missing required field: {key}")
    _validate_boolish(ntfy, "tls_verify", "ntfy")

    auth = ntfy["auth"]
    if not isinstance(auth, dict):
        raise ConfigError("ntfy auth must be an object")

    auth_type = auth.get("type", "none")
    if auth_type not in ["none", "token", "basic"]:
        raise ConfigError(f"Unsupported ntfy auth type: {auth_type}")

    if auth_type == "token":
        for key in ["encrypted_token", "token_encryption"]:
            if key not in auth:
                raise ConfigError(f"ntfy token auth is missing required field: {key}")
        _validate_encryption_metadata(auth["token_encryption"], "ntfy token")

        # Optional metadata from Start9/StartOS's provision-publisher dialogue.
        if "encrypted_publisher_username" in auth and "publisher_username_encryption" not in auth:
            raise ConfigError("ntfy token auth has encrypted_publisher_username but no publisher_username_encryption")
        if "publisher_username_encryption" in auth and "encrypted_publisher_username" not in auth:
            raise ConfigError("ntfy token auth has publisher_username_encryption but no encrypted_publisher_username")
        if "publisher_username_encryption" in auth:
            _validate_encryption_metadata(auth["publisher_username_encryption"], "ntfy publisher username")

    if auth_type == "basic":
        for key in ["encrypted_username", "username_encryption", "encrypted_password", "password_encryption"]:
            if key not in auth:
                raise ConfigError(f"ntfy basic auth is missing required field: {key}")
        _validate_encryption_metadata(auth["username_encryption"], "ntfy username")
        _validate_encryption_metadata(auth["password_encryption"], "ntfy password")

    mempool = config.get("mempool") or {}
    if mempool:
        _validate_boolish(mempool, "enabled", "mempool")
        _validate_boolish(mempool, "tls_verify", "mempool")
        if mempool.get("enabled") and not mempool.get("base_url"):
            raise ConfigError("mempool.enabled is true but mempool.base_url is blank")

    tor = config.get("tor") or {}
    if tor:
        _validate_boolish(tor, "enabled", "tor")
        _validate_boolish(tor, "manage_process", "tor")
        _validate_boolish(tor, "test_on_startup", "tor")
        if tor.get("enabled") and not str(tor.get("socks_proxy") or "").strip():
            raise ConfigError("tor.enabled is true but tor.socks_proxy is blank")
        if int(tor.get("startup_timeout_seconds", 60)) < 1:
            raise ConfigError("tor.startup_timeout_seconds must be at least 1")

    conversation = config.get("conversation") or {}
    if conversation:
        _validate_boolish(conversation, "enabled", "conversation")
        _validate_boolish(conversation, "require_protected_topic", "conversation")
        _validate_boolish(conversation, "probe_anonymous_write", "conversation")
        _validate_boolish(conversation, "tls_verify", "conversation")

        conversation_auth = conversation.get("auth") or {"type": "same_as_ntfy"}
        if not isinstance(conversation_auth, dict):
            raise ConfigError("conversation.auth must be an object")

        conversation_auth_type = conversation_auth.get("type", "same_as_ntfy")
        if conversation_auth_type not in ["same_as_ntfy", "token", "basic"]:
            raise ConfigError(f"Unsupported conversation auth type: {conversation_auth_type}")

        if conversation_auth_type == "token":
            for key in ["encrypted_token", "token_encryption"]:
                if key not in conversation_auth:
                    raise ConfigError(f"conversation token auth is missing required field: {key}")
            _validate_encryption_metadata(conversation_auth["token_encryption"], "Conversation Mode ntfy token")

        if conversation_auth_type == "basic":
            for key in ["encrypted_username", "username_encryption", "encrypted_password", "password_encryption"]:
                if key not in conversation_auth:
                    raise ConfigError(f"conversation basic auth is missing required field: {key}")
            _validate_encryption_metadata(conversation_auth["username_encryption"], "Conversation Mode ntfy username")
            _validate_encryption_metadata(conversation_auth["password_encryption"], "Conversation Mode ntfy password")

        if int(conversation.get("max_addresses_per_response", 10)) < 1:
            raise ConfigError("conversation.max_addresses_per_response must be at least 1")
        if int(conversation.get("max_response_chars", 3900)) < 500:
            raise ConfigError("conversation.max_response_chars must be at least 500")

    autobalance = config.get("autobalance") or {}
    if autobalance:
        _validate_boolish(autobalance, "enabled", "autobalance")
        _validate_boolish(autobalance, "all_wallets", "autobalance")

        if int(autobalance.get("interval_hours", 12)) < 1:
            raise ConfigError("autobalance.interval_hours must be at least 1")

        configured_wallets = autobalance.get("wallets") or []
        if not isinstance(configured_wallets, list):
            raise ConfigError("autobalance.wallets must be a list")
        for wallet_name in configured_wallets:
            if not isinstance(wallet_name, str) or not wallet_name.strip():
                raise ConfigError("autobalance.wallets must contain non-blank wallet names")

        if bool(autobalance.get("enabled", False)) and not bool(autobalance.get("all_wallets", True)) and not configured_wallets:
            raise ConfigError("autobalance is enabled for selected wallets but autobalance.wallets is empty")

    if not isinstance(config["wallets"], list) or not config["wallets"]:
        raise ConfigError("Config must contain at least one wallet")

    configured_wallet_names = {str(wallet.get("name") or "").strip() for wallet in config["wallets"] if isinstance(wallet, dict)}
    if autobalance and not bool(autobalance.get("all_wallets", True)):
        for wallet_name in autobalance.get("wallets") or []:
            if wallet_name not in configured_wallet_names:
                raise ConfigError(f"autobalance references unknown wallet: {wallet_name}")

    for wallet in config["wallets"]:
        for key in ["name", "network", "wallet_type", "account_path", "encrypted_xpub", "xpub_encryption"]:
            if key not in wallet:
                raise ConfigError(f"Wallet is missing required field: {key}")

        wallet_type = wallet["wallet_type"]
        if wallet_type not in ["taproot", "native_segwit", "nested_segwit", "legacy"]:
            raise ConfigError(f"Unsupported wallet_type: {wallet_type}")

        network = wallet["network"]
        if network not in ["bitcoin", "testnet", "signet", "regtest"]:
            raise ConfigError(f"Unsupported network: {network}")

        _validate_encryption_metadata(wallet["xpub_encryption"], "wallet xpub")


def default_config() -> dict[str, Any]:
    return deepcopy(
        {
            "app": {
                "name": "Bitcoin Wallet Watchguard",
                "database_path": DEFAULT_DATABASE_PATH,
                "derivation_helper_path": "./wwg-derive",
                "lookahead": 100,
                "notify_on_mempool": True,
                "notify_on_confirmed": True,
            },
            "electrum": {
                "host": "127.0.0.1",
                "port": 50002,
                "tls": True,
                "tls_verify": True,
                "socks_proxy": None,
                "timeout_seconds": 30,
            },
            "ntfy": {
                "server": "https://ntfy.example.com",
                "topic": PLACEHOLDER_NTFY_TOPIC,
                "auth": {
                    "type": "none",
                },
                "priority": "high",
                "tags": "bitcoin,watch",
                "tls_verify": True,
                "timeout_seconds": 15,
            },
            "notifications": {
                "providers": deepcopy(DEFAULT_NOTIFICATION_PROVIDERS),
            },
            "mempool": {
                "enabled": False,
                "base_url": "",
                "tls_verify": True,
                "timeout_seconds": 15,
                "enrich_notifications": True,
            },
            "tor": {
                "enabled": False,
                "socks_proxy": "127.0.0.1:9050",
                "manage_process": True,
                "startup_timeout_seconds": 60,
                "test_on_startup": True,
                "data_dir": "",
            },
            "conversation": {
                "enabled": False,
                "topic": "",
                "auth": {
                    "type": "same_as_ntfy",
                },
                "command_prefix": "wwg",
                "require_protected_topic": True,
                "probe_anonymous_write": True,
                "max_addresses_per_response": 10,
                "max_response_chars": 3900,
            },
            "autobalance": {
                "enabled": False,
                "interval_hours": 12,
                "all_wallets": True,
                "wallets": [],
            },
            "wallets": [],
        }
    )
