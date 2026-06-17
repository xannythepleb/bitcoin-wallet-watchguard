from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from .crypto import SCHEME


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


def validate_config(config: dict[str, Any]) -> None:
    for key in ["app", "electrum", "ntfy", "wallets"]:
        if key not in config:
            raise ConfigError(f"Missing required config section: {key}")

    ntfy = config["ntfy"]
    for key in ["server", "topic", "auth"]:
        if key not in ntfy:
            raise ConfigError(f"ntfy config is missing required field: {key}")

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

    if not isinstance(config["wallets"], list) or not config["wallets"]:
        raise ConfigError("Config must contain at least one wallet")

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
                "database_path": "./watchguard.sqlite3",
                "derivation_helper_path": "./wwg-derive",
                "lookahead": 100,
                "notify_on_mempool": True,
                "notify_on_confirmed": True,
            },
            "electrum": {
                "host": "127.0.0.1",
                "port": 50002,
                "tls": True,
                "socks_proxy": None,
                "timeout_seconds": 30,
            },
            "ntfy": {
                "server": "https://ntfy.example.com",
                "topic": "wallet-watchguard-replace-me",
                "auth": {
                    "type": "none",
                },
                "priority": "high",
                "tags": "bitcoin,watch",
            },
            "wallets": [],
        }
    )
