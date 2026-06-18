from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import yaml

from .config import PLACEHOLDER_NTFY_TOPIC, default_config, load_config, load_config_for_edit, save_config
from .crypto import (
    decrypt_xpub_with_passphrase,
    encrypt_string_with_passphrase,
    encrypt_xpub_with_passphrase,
    metadata_from_config,
    metadata_to_config,
    prompt_existing_passphrase,
    prompt_new_passphrase,
)
from .derivation import derive_addresses
from .electrum import ElectrumClient, default_tls_verify_for_host
from .mempool import MempoolClient, format_mempool_fee_summary
from .ntfy import NtfyNotifier, decrypt_ntfy_config
from .status import build_status_text, format_server_version, tor_upstream_lines
from .tor import TorUpstreamManager, apply_tor_upstream, env_tor_upstream_enabled
from .watcher import Watcher, get_passphrase_from_env_or_prompt


INIT_SECTIONS = ["full", "electrum", "ntfy", "wallet", "app", "mempool", "tor", "conversation"]

# Docker Compose sets this to /data/config.yaml.
# When running directly on the OS, the default remains ./config.yaml.
# Explicit --config always overrides this value.
DEFAULT_CONFIG_PATH = os.environ.get("WWG_CONFIG_PATH", "./config.yaml")


def _prompt(label: str, default: str | None = None, *, display_default: str | None = None) -> str:
    # display_default lets us show a friendly hint (e.g. "not yet set") while
    # still falling back to a real value when the user just presses Enter.
    shown = display_default if display_default is not None else default
    suffix = f" [{shown}]" if shown is not None else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or (default or "")


def _print_feature_skipped(feature_label: str, init_section: str) -> None:
    print()
    print(f"Skipping {feature_label} setup for now.")
    print("You can configure it later with:")
    print(f"  wwg init --add {init_section}")


def _parse_address_count(raw: str | None) -> int:
    """Parse the count token accepted by `wwg next`.

    Accepts a number (`wwg next 3`), the words address/addresses
    (`wwg next address`), or nothing (`wwg next`). Anything non-numeric falls
    back to 1 so the documented natural forms all work.
    """
    if raw is None:
        return 1
    text = str(raw).strip().lower()
    if not text or text in {"address", "addresses", "receive"}:
        return 1
    try:
        return max(1, int(text))
    except ValueError:
        return 1


def _prompt_secret(label: str) -> str:
    return getpass.getpass(f"{label}: ").strip()


def _prompt_bool(label: str, default: bool = True) -> bool:
    default_text = "Y/n" if default else "y/N"
    value = input(f"{label} [{default_text}]: ").strip().lower()
    if not value:
        return default
    return value in ["y", "yes", "true", "1"]


def _prompt_choice(label: str, choices: dict[str, str], default: str | None = None) -> str:
    print(label)
    for key, description in choices.items():
        print(f"  {key}. {description}")

    while True:
        value = _prompt("Choose", default).lower()
        if value in choices:
            return value
        print(f"Please choose one of: {', '.join(choices)}")


def _encrypted_config_value(value: str, passphrase: str) -> tuple[str, dict[str, object]]:
    encrypted_value, metadata = encrypt_string_with_passphrase(value, passphrase)
    return encrypted_value, metadata_to_config(metadata)


def _print_missing_config_help(config_path: Path) -> None:
    print(file=sys.stderr)
    print(f"Config file not found: {config_path}", file=sys.stderr)
    print(file=sys.stderr)
    print("Wallet Watchguard needs a config file before the daemon can start.", file=sys.stderr)
    print(file=sys.stderr)
    print("Create one with the interactive setup wizard:", file=sys.stderr)
    print(f"  wwg init", file=sys.stderr)
    print(file=sys.stderr)
    print("If you are using Docker Compose, run:", file=sys.stderr)
    print(f"  docker compose run --rm wallet-watchguard wwg init", file=sys.stderr)
    print(file=sys.stderr)
    print("Then start the daemon with:", file=sys.stderr)
    print("  WWG_PASSPHRASE='your passphrase here' docker compose up -d", file=sys.stderr)
    print(file=sys.stderr)
    print("For local/manual use:", file=sys.stderr)
    print(f"  wwg run --config {config_path}", file=sys.stderr)
    print(file=sys.stderr)


def _config_has_encrypted_values(config: dict) -> bool:
    ntfy_auth = (config.get("ntfy") or {}).get("auth") or {}
    if ntfy_auth.get("encrypted_token") or ntfy_auth.get("encrypted_password"):
        return True

    conversation_auth = (config.get("conversation") or {}).get("auth") or {}
    if conversation_auth.get("encrypted_token") or conversation_auth.get("encrypted_password"):
        return True

    for wallet in config.get("wallets") or []:
        if wallet.get("encrypted_xpub"):
            return True

    return False


def _get_encryption_passphrase(args: argparse.Namespace, *, existing_secret: bool) -> str:
    if getattr(args, "passphrase", None):
        return args.passphrase

    if existing_secret:
        print()
        print("Enter the existing Wallet Watchguard encryption passphrase.")
        print("New sensitive values will be encrypted with the same passphrase.")
        return prompt_existing_passphrase()

    print()
    print("Set the passphrase used to encrypt sensitive values in config.yaml.")
    print("This passphrase encrypts your xpub and ntfy credentials at rest.")
    print("You will need it whenever Wallet Watchguard starts.")
    return prompt_new_passphrase()


def _server_from_ntfy_publish_url(publish_url: str, topic: str) -> str:
    parsed = urlparse(publish_url.strip())
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("publishUrl must be a full URL, for example https://ntfy.example.com/my-topic")

    topic = topic.strip().strip("/")
    path = parsed.path.rstrip("/")

    if topic and path.endswith(f"/{topic}"):
        path = path[: -(len(topic) + 1)].rstrip("/")
    elif topic and path.strip("/") == topic:
        path = ""

    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", "")).rstrip("/")


def _prompt_app_config(existing_app: dict | None = None) -> dict[str, object]:
    existing_app = existing_app or {}

    print()
    print("Application settings")

    return {
        "name": existing_app.get("name", "Bitcoin Wallet Watchguard"),
        "database_path": _prompt("SQLite database path", str(existing_app.get("database_path", "./watchguard.sqlite3"))),
        "derivation_helper_path": _prompt(
            "Rust derivation helper path",
            str(existing_app.get("derivation_helper_path", "./wwg-derive")),
        ),
        "lookahead": int(_prompt("Default lookahead", str(existing_app.get("lookahead", 100)))),
        "notify_on_mempool": _prompt_bool(
            "Notify on mempool/unconfirmed transactions",
            bool(existing_app.get("notify_on_mempool", True)),
        ),
        "notify_on_confirmed": _prompt_bool(
            "Notify on confirmed transactions",
            bool(existing_app.get("notify_on_confirmed", True)),
        ),
    }


def _prompt_electrum_config(existing_electrum: dict | None = None) -> dict[str, object]:
    existing_electrum = existing_electrum or {}

    print()
    print("Electrum/Fulcrum configuration")
    print("Privacy recommendation: use your own Bitcoin node + Fulcrum.")
    print("Using a public Electrum server with xpub-derived addresses can leak wallet activity.")
    print("For Start9/StartOS or LAN services with self-signed TLS certificates, disable TLS verification below.")
    print()

    current_tls = bool(existing_electrum.get("tls", True))
    electrum_host = _prompt("Electrum/Fulcrum host", str(existing_electrum.get("host", "127.0.0.1")))
    use_tls = _prompt_bool("Use TLS", current_tls)
    default_port = "50002" if use_tls else "50001"
    current_port = existing_electrum.get("port") or default_port
    electrum_port = int(_prompt("Electrum/Fulcrum port", str(current_port)))
    tls_verify = True
    if use_tls:
        recommended_tls_verify = default_tls_verify_for_host(electrum_host)
        existing_tls_verify = existing_electrum.get("tls_verify")

        # Start9/StartOS, LAN IPs, .local names and onion services commonly use
        # private/self-signed certificates. For those hosts, default to relaxed
        # verification even if an older config/default merged tls_verify: true.
        if recommended_tls_verify is False:
            tls_verify_default = False
            print()
            print("This looks like a local/self-hosted Electrum/Fulcrum host.")
            print("For Start9/StartOS or self-signed certificates, choose 'no' here.")
        elif isinstance(existing_tls_verify, bool):
            tls_verify_default = existing_tls_verify
        else:
            tls_verify_default = recommended_tls_verify

        tls_verify = _prompt_bool(
            "Verify Electrum/Fulcrum TLS certificate",
            tls_verify_default,
        )
    socks_proxy = _prompt(
        "SOCKS5 proxy for onion host, blank for none",
        str(existing_electrum.get("socks_proxy") or ""),
    )

    return {
        "host": electrum_host,
        "port": electrum_port,
        "tls": use_tls,
        "tls_verify": tls_verify,
        "socks_proxy": socks_proxy or None,
        "timeout_seconds": int(_prompt("Connection timeout seconds", str(existing_electrum.get("timeout_seconds", 30)))),
    }


def _prompt_ntfy_start9_config(passphrase: str, existing_ntfy: dict | None = None) -> dict[str, object]:
    existing_ntfy = existing_ntfy or {}
    existing_topic = str(existing_ntfy.get("topic") or "").strip()
    if existing_topic == PLACEHOLDER_NTFY_TOPIC:
        existing_topic = ""
    suggested_topic = existing_topic or f"wallet-watchguard-{secrets.token_hex(12)}"

    print()
    print("Start9 / StartOS ntfy publisher provisioning")
    print()
    print("This flow is ideal for normal Wallet Watchguard notifications.")
    print("Start9's 'Provision Publisher' creates a write-only publisher token for one topic.")
    print("That is good for alerts, but it cannot be used for Conversation Mode because Conversation Mode must read/subscribe too.")
    print()
    print("In your Start9 ntfy service UI, use 'Provision Publisher'.")
    print("For the reference name, something like 'wallet-watchguard' is fine.")
    print(f"For the topic name, use: {suggested_topic}")
    print()
    print("Start9 should then show a dialogue with:")
    print("  publishUrl")
    print("  token")
    print("  topic")
    print("  username")
    print()
    print("Paste those values below. Wallet Watchguard will encrypt the token at rest.")
    print("The username is stored encrypted as publisher metadata, but token auth is used for publishing.")
    print()
    print("For Conversation Mode on Start9, do not use this write-only publisher token.")
    print("Instead: create a regular ntfy user, grant it read-write access to the topic, log in as that user, create an access token, then configure Wallet Watchguard with that token.")

    publish_url = _prompt("publishUrl from Start9")
    topic = _prompt("topic from Start9", suggested_topic)
    username = _prompt("username from Start9, blank if none", "")
    token = _prompt_secret("token from Start9")

    if not token:
        raise ValueError("Start9 ntfy token must not be blank")

    server = _server_from_ntfy_publish_url(publish_url, topic)
    encrypted_token, token_encryption = _encrypted_config_value(token, passphrase)

    auth: dict[str, object] = {
        "type": "token",
        "encrypted_token": encrypted_token,
        "token_encryption": token_encryption,
    }

    if username:
        encrypted_username, username_encryption = _encrypted_config_value(username, passphrase)
        auth["encrypted_publisher_username"] = encrypted_username
        auth["publisher_username_encryption"] = username_encryption

    return {
        "server": server,
        "topic": topic,
        "auth": auth,
        "priority": str(existing_ntfy.get("priority", "high")),
        "tags": str(existing_ntfy.get("tags", "bitcoin,watch")),
        "tls_verify": _prompt_bool(
            "Verify ntfy TLS certificate",
            bool(existing_ntfy.get("tls_verify", False)),
        ),
        "timeout_seconds": int(_prompt("ntfy timeout seconds", str(existing_ntfy.get("timeout_seconds", 15)))),
    }


def _prompt_ntfy_config(passphrase: str, existing_ntfy: dict | None = None) -> dict[str, object]:
    existing_ntfy = existing_ntfy or {}

    print()
    print("ntfy configuration")
    print("Recommendation: use a self-hosted ntfy instance with a dedicated Wallet Watchguard topic.")
    print("For normal notifications, a write-only publisher token is enough and is preferred.")
    print("For Conversation Mode, Wallet Watchguard needs credentials that can both read and write the topic.")
    print("Avoid broad anonymous publish permissions unless you deliberately want that.")

    stored_topic = str(existing_ntfy.get("topic") or "").strip()
    if stored_topic and stored_topic != PLACEHOLDER_NTFY_TOPIC:
        print()
        print(f"Existing topic stored in config: {stored_topic}")
        print("Use this exact topic name when creating Start9 publisher credentials or read-write Conversation Mode credentials, unless you want to change topics.")

    if _prompt_bool("Are you using Start9/StartOS 'Provision Publisher' details", False):
        return _prompt_ntfy_start9_config(passphrase, existing_ntfy)

    ntfy_server = _prompt("ntfy server URL", str(existing_ntfy.get("server", "https://ntfy.example.com")))

    existing_topic = str(existing_ntfy.get("topic") or "").strip()
    if existing_topic and existing_topic != PLACEHOLDER_NTFY_TOPIC:
        ntfy_topic = _prompt("Private ntfy topic", existing_topic)
    else:
        # No real topic chosen yet. Never show the internal placeholder. Offer a
        # freshly generated private topic as the default the user gets on Enter.
        random_topic = f"wallet-watchguard-{secrets.token_hex(12)}"
        print()
        print("A private, hard to guess topic keeps your alerts and commands away from prying eyes.")
        print("Press Enter to accept a freshly generated private topic, or type your own.")
        ntfy_topic = _prompt(
            "Private ntfy topic",
            random_topic,
            display_default="not yet set; press Enter to generate one",
        )

    auth = existing_ntfy.get("auth") or {}
    plaintext_token = existing_ntfy.get("token")
    plaintext_username = existing_ntfy.get("username")
    plaintext_password = existing_ntfy.get("password")

    if plaintext_token and _prompt_bool("Detected old plaintext ntfy token. Encrypt and migrate it now", True):
        encrypted_token, token_encryption = _encrypted_config_value(str(plaintext_token), passphrase)
        return {
            "server": ntfy_server,
            "topic": ntfy_topic,
            "auth": {
                "type": "token",
                "encrypted_token": encrypted_token,
                "token_encryption": token_encryption,
            },
            "priority": str(existing_ntfy.get("priority", "high")),
            "tags": str(existing_ntfy.get("tags", "bitcoin,watch")),
            "tls_verify": _prompt_bool("Verify ntfy TLS certificate", bool(existing_ntfy.get("tls_verify", True))),
            "timeout_seconds": int(_prompt("ntfy timeout seconds", str(existing_ntfy.get("timeout_seconds", 15)))),
        }

    if plaintext_username and plaintext_password and _prompt_bool(
        "Detected old plaintext ntfy username/password. Encrypt and migrate them now",
        True,
    ):
        encrypted_username, username_encryption = _encrypted_config_value(str(plaintext_username), passphrase)
        encrypted_password, password_encryption = _encrypted_config_value(str(plaintext_password), passphrase)
        return {
            "server": ntfy_server,
            "topic": ntfy_topic,
            "auth": {
                "type": "basic",
                "encrypted_username": encrypted_username,
                "username_encryption": username_encryption,
                "encrypted_password": encrypted_password,
                "password_encryption": password_encryption,
            },
            "priority": str(existing_ntfy.get("priority", "high")),
            "tags": str(existing_ntfy.get("tags", "bitcoin,watch")),
            "tls_verify": _prompt_bool("Verify ntfy TLS certificate", bool(existing_ntfy.get("tls_verify", True))),
            "timeout_seconds": int(_prompt("ntfy timeout seconds", str(existing_ntfy.get("timeout_seconds", 15)))),
        }

    print()
    print("Choose ntfy authentication mode:")
    print("  none  - no credentials; only sensible for LAN-only testing or a very locked-down private network")
    print("  token - ntfy access token / bearer token; recommended where available")
    print("  basic - username and password")
    print()
    print("Start9 note:")
    print("  - Provision Publisher tokens are write-only and work for normal alerts.")
    print("  - Conversation Mode needs read-write credentials.")
    print("  - For Conversation Mode, create a regular ntfy user, grant it read-write access to the topic, then create an access token in the ntfy web UI.")

    existing_auth_type = auth.get("type", "none")
    if existing_auth_type not in ["none", "token", "basic"]:
        existing_auth_type = "token"

    # Default to token: it is the recommended mode and the only sensible choice
    # for Conversation Mode. "none" almost always appears only because it is the
    # unconfigured default, so we don't want to steer users towards it.
    default_auth_type = existing_auth_type if existing_auth_type in {"token", "basic"} else "token"

    auth_type = _prompt("Auth mode: none/token/basic", default_auth_type).lower()

    if auth_type not in ["none", "token", "basic"]:
        raise ValueError("ntfy auth mode must be one of: none, token, basic")

    new_auth: dict[str, object] = {"type": auth_type}

    if auth_type == "token":
        print()
        print("Enter the ntfy access token for Wallet Watchguard.")
        print("This will be encrypted in config.yaml using the same passphrase as your xpub.")
        token = _prompt_secret("ntfy access token")
        if not token:
            raise ValueError("ntfy token must not be blank when auth mode is token")

        encrypted_token, token_encryption = _encrypted_config_value(token, passphrase)
        new_auth.update(
            {
                "encrypted_token": encrypted_token,
                "token_encryption": token_encryption,
            }
        )

    elif auth_type == "basic":
        print()
        print("Enter the ntfy username/password for Wallet Watchguard.")
        print("Both values will be encrypted in config.yaml using the same passphrase as your xpub.")
        username = _prompt_secret("ntfy username")
        password = _prompt_secret("ntfy password")
        if not username:
            raise ValueError("ntfy username must not be blank when auth mode is basic")
        if not password:
            raise ValueError("ntfy password must not be blank when auth mode is basic")

        encrypted_username, username_encryption = _encrypted_config_value(username, passphrase)
        encrypted_password, password_encryption = _encrypted_config_value(password, passphrase)
        new_auth.update(
            {
                "encrypted_username": encrypted_username,
                "username_encryption": username_encryption,
                "encrypted_password": encrypted_password,
                "password_encryption": password_encryption,
            }
        )

    return {
        "server": ntfy_server,
        "topic": ntfy_topic,
        "auth": new_auth,
        "priority": _prompt("ntfy priority", str(existing_ntfy.get("priority", "high"))),
        "tags": _prompt("ntfy tags", str(existing_ntfy.get("tags", "bitcoin,watch"))),
        "tls_verify": _prompt_bool("Verify ntfy TLS certificate", bool(existing_ntfy.get("tls_verify", True))),
        "timeout_seconds": int(_prompt("ntfy timeout seconds", str(existing_ntfy.get("timeout_seconds", 15)))),
    }


def _prompt_mempool_config(existing_mempool: dict | None = None) -> dict[str, object]:
    existing_mempool = existing_mempool or {}

    print()
    print("Optional Mempool API configuration")
    print("This is only used to enrich notifications with decoded transaction data such as amount, fee and fee rate.")
    print("Fulcrum/Electrum remains the source of truth for wallet activity detection.")
    print("For Start9/StartOS or LAN services with self-signed TLS certificates, disable TLS verification below.")

    enabled = _prompt_bool("Enable Mempool API enrichment", bool(existing_mempool.get("enabled", False)))

    if not enabled:
        _print_feature_skipped("Mempool API enrichment", "mempool")
        default_mempool = default_config().get("mempool") or {}
        merged = {**default_mempool, **existing_mempool}
        merged["enabled"] = False
        return merged

    base_url = _prompt("Mempool API base URL", str(existing_mempool.get("base_url") or "https://mempool.example.com/api"))

    return {
        "enabled": True,
        "base_url": base_url.rstrip("/"),
        "tls_verify": _prompt_bool("Verify Mempool TLS certificate", bool(existing_mempool.get("tls_verify", True))),
        "timeout_seconds": int(_prompt("Mempool timeout seconds", str(existing_mempool.get("timeout_seconds", 15)))),
        "enrich_notifications": True,
    }


def _prompt_tor_config(existing_tor: dict | None = None) -> dict[str, object]:
    existing_tor = existing_tor or {}

    print()
    print("Tor upstream")
    print("Optional: start an internal Tor SOCKS proxy for Docker/Docker Compose runs.")
    print("Use this when your Electrum/Fulcrum node is only reachable as a .onion service.")
    print("This is OFF by default. Existing electrum.socks_proxy setups still work without this switch.")

    enabled = _prompt_bool("Enable internal Tor upstream", bool(existing_tor.get("enabled", False)))

    if not enabled:
        _print_feature_skipped("Tor upstream", "tor")
        default_tor = default_config().get("tor") or {}
        merged = {**default_tor, **existing_tor}
        merged["enabled"] = False
        return merged

    socks_proxy = _prompt("Internal Tor SOCKS proxy", str(existing_tor.get("socks_proxy") or "127.0.0.1:9050"))

    return {
        "enabled": True,
        "socks_proxy": socks_proxy,
        "manage_process": _prompt_bool("Let Wallet Watchguard start the Tor process", bool(existing_tor.get("manage_process", True))),
        "startup_timeout_seconds": int(_prompt("Tor startup timeout seconds", str(existing_tor.get("startup_timeout_seconds", 60)))),
        "test_on_startup": _prompt_bool("Test Tor connectivity on startup", bool(existing_tor.get("test_on_startup", True))),
        "data_dir": _prompt("Tor data directory, blank for temporary", str(existing_tor.get("data_dir") or "")),
    }


def _prompt_conversation_config(
    passphrase: str,
    existing_conversation: dict | None = None,
    existing_ntfy: dict | None = None,
) -> dict[str, object]:
    existing_conversation = existing_conversation or {}
    existing_ntfy = existing_ntfy or {}
    existing_ntfy_auth = existing_ntfy.get("auth") or {"type": "none"}
    existing_ntfy_auth_type = str(existing_ntfy_auth.get("type", "none"))
    raw_ntfy_topic = str(existing_ntfy.get("topic") or "").strip("/")
    existing_ntfy_topic = "" if raw_ntfy_topic == PLACEHOLDER_NTFY_TOPIC else raw_ntfy_topic
    ntfy_topic_display = existing_ntfy_topic or "not yet set; configure ntfy first with 'wwg init --add ntfy'"

    print()
    print("Conversation Mode")
    print("Conversation Mode lets you query Wallet Watchguard remotely via ntfy.")
    print("It is OFF by default and will only start if the ntfy topic passes protection checks:")
    print("  - Wallet Watchguard can read the topic with configured credentials")
    print("  - Wallet Watchguard can publish with configured credentials")
    print("  - anonymous read is blocked")
    print("  - anonymous write is blocked")
    print()
    print("Use only with a private, password/token-protected ntfy topic.")
    print()
    print("Start9 note: Provision Publisher tokens are write-only, so they cannot be used for Conversation Mode.")
    print("For Start9 Conversation Mode, create a regular ntfy user, grant it read-write topic access, log in as that user, create an access token, and configure Wallet Watchguard with that token.")
    print()
    print("You can use the same ntfy topic as normal alerts, or create a separate topic to keep Conversation Mode commands tidy.")

    enabled = _prompt_bool("Enable Conversation Mode in config", bool(existing_conversation.get("enabled", False)))

    if not enabled:
        _print_feature_skipped("Conversation Mode", "conversation")
        default_conversation = default_config().get("conversation") or {}
        merged = {**default_conversation, **existing_conversation}
        merged["enabled"] = False
        return merged

    existing_topic = str(existing_conversation.get("topic") or "").strip("/")
    use_separate_topic_default = bool(existing_topic)
    use_separate_topic = _prompt_bool("Use a separate ntfy topic for Conversation Mode", use_separate_topic_default)

    topic = ""
    if use_separate_topic:
        if existing_topic:
            suggested_topic = existing_topic
        elif existing_ntfy_topic:
            suggested_topic = f"{existing_ntfy_topic}-conversation"
        else:
            suggested_topic = f"wallet-watchguard-conversation-{secrets.token_hex(8)}"
        print()
        print("Create this topic in ntfy and grant your Conversation Mode credential read-write access to it.")
        print("Anonymous read and anonymous write must stay denied.")
        topic = _prompt("Conversation Mode topic", suggested_topic).strip().strip("/")
        if not topic:
            raise ValueError("Conversation Mode topic must not be blank when using a separate topic")
    else:
        print()
        print(f"Conversation Mode will use the normal ntfy topic: {ntfy_topic_display}")

    print()
    print("Conversation Mode credentials")
    print("The credential used here must have read and write access to the Conversation Mode topic.")
    print("A normal Start9 Provision Publisher token is write-only and will not work.")

    existing_conversation_auth = existing_conversation.get("auth") or {"type": "same_as_ntfy"}
    existing_conversation_auth_type = str(existing_conversation_auth.get("type", "same_as_ntfy"))

    can_reuse_ntfy_auth = existing_ntfy_auth_type in {"token", "basic"}
    auth: dict[str, object]

    if can_reuse_ntfy_auth:
        print()
        print(f"Existing ntfy auth type in config: {existing_ntfy_auth_type}")
        print("You may reuse this credential if, and only if, it has read-write access to the Conversation Mode topic.")
        reuse_default = existing_conversation_auth_type == "same_as_ntfy"
        if _prompt_bool("Reuse the existing ntfy credential for Conversation Mode", reuse_default):
            auth = {"type": "same_as_ntfy"}
        else:
            auth = _prompt_conversation_auth(passphrase, existing_conversation_auth)
    else:
        print()
        print("The existing ntfy config does not contain token/basic credentials that Conversation Mode can reuse.")
        auth = _prompt_conversation_auth(passphrase, existing_conversation_auth)

    command_prefix = _prompt("Command prefix", str(existing_conversation.get("command_prefix", "wwg")))

    return {
        "enabled": True,
        "topic": topic,
        "auth": auth,
        "command_prefix": command_prefix,
        "require_protected_topic": True,
        "probe_anonymous_write": _prompt_bool(
            "Probe anonymous write access on startup",
            bool(existing_conversation.get("probe_anonymous_write", True)),
        ),
        "max_addresses_per_response": int(_prompt(
            "Maximum addresses per ntfy response",
            str(existing_conversation.get("max_addresses_per_response", 10)),
        )),
        "max_response_chars": int(_prompt(
            "Maximum ntfy response characters",
            str(existing_conversation.get("max_response_chars", 3900)),
        )),
    }


def _prompt_conversation_auth(passphrase: str, existing_auth: dict | None = None) -> dict[str, object]:
    existing_auth = existing_auth or {}
    existing_auth_type = str(existing_auth.get("type", "token"))
    if existing_auth_type not in ["token", "basic"]:
        existing_auth_type = "token"

    print()
    print("Choose Conversation Mode authentication mode:")
    print("  token - ntfy access token / bearer token; recommended")
    print("  basic - username and password")
    auth_type = _prompt("Conversation Mode auth mode: token/basic", existing_auth_type).lower()

    if auth_type not in ["token", "basic"]:
        raise ValueError("Conversation Mode auth mode must be one of: token, basic")

    if auth_type == "token":
        print()
        print("Enter a ntfy access token with read-write access to the Conversation Mode topic.")
        print("This will be encrypted in config.yaml using the Wallet Watchguard passphrase.")
        token = _prompt_secret("Conversation Mode ntfy access token")
        if not token:
            raise ValueError("Conversation Mode ntfy token must not be blank")
        encrypted_token, token_encryption = _encrypted_config_value(token, passphrase)
        return {
            "type": "token",
            "encrypted_token": encrypted_token,
            "token_encryption": token_encryption,
        }

    print()
    print("Enter ntfy username/password credentials with read-write access to the Conversation Mode topic.")
    print("Both values will be encrypted in config.yaml using the Wallet Watchguard passphrase.")
    username = _prompt_secret("Conversation Mode ntfy username")
    password = _prompt_secret("Conversation Mode ntfy password")
    if not username:
        raise ValueError("Conversation Mode ntfy username must not be blank")
    if not password:
        raise ValueError("Conversation Mode ntfy password must not be blank")
    encrypted_username, username_encryption = _encrypted_config_value(username, passphrase)
    encrypted_password, password_encryption = _encrypted_config_value(password, passphrase)
    return {
        "type": "basic",
        "encrypted_username": encrypted_username,
        "username_encryption": username_encryption,
        "encrypted_password": encrypted_password,
        "password_encryption": password_encryption,
    }


def _prompt_wallet_config(passphrase: str) -> dict[str, object]:
    print()
    print("Wallet configuration")
    wallet_name = _prompt("Wallet name", "Main Taproot wallet")
    wallet_type = _prompt("Wallet type: taproot/native_segwit/nested_segwit/legacy", "taproot").lower()

    if wallet_type not in ["taproot", "native_segwit", "nested_segwit", "legacy"]:
        raise ValueError("wallet_type must be one of: taproot, native_segwit, nested_segwit, legacy")

    network = _prompt("Network: bitcoin/testnet/signet/regtest", "bitcoin").lower()
    if network not in ["bitcoin", "testnet", "signet", "regtest"]:
        raise ValueError("network must be one of: bitcoin, testnet, signet, regtest")

    account_path_default = {
        "taproot": "m/86'/0'/0'",
        "native_segwit": "m/84'/0'/0'",
        "nested_segwit": "m/49'/0'/0'",
        "legacy": "m/44'/0'/0'",
    }[wallet_type]

    account_path = _prompt("Account derivation path metadata", account_path_default)
    xpub = _prompt("Account xpub")
    encrypted_xpub, metadata = encrypt_xpub_with_passphrase(xpub, passphrase)

    return {
        "name": wallet_name,
        "network": network,
        "wallet_type": wallet_type,
        "account_path": account_path,
        "xpub_encryption": metadata_to_config(metadata),
        "encrypted_xpub": encrypted_xpub,
        "receive_path_template": _prompt("Receive path template", "0/*"),
        "change_path_template": _prompt("Change path template", "1/*"),
        "lookahead": int(_prompt("Lookahead", "100")),
    }


def _apply_full_setup(config: dict, args: argparse.Namespace, *, existing_secret: bool) -> dict:
    passphrase = _get_encryption_passphrase(args, existing_secret=existing_secret)
    config["app"] = _prompt_app_config(config.get("app"))
    config["electrum"] = _prompt_electrum_config(config.get("electrum"))
    config["ntfy"] = _prompt_ntfy_config(passphrase, config.get("ntfy"))
    config["mempool"] = _prompt_mempool_config(config.get("mempool"))
    config["tor"] = _prompt_tor_config(config.get("tor"))
    config["conversation"] = _prompt_conversation_config(passphrase, config.get("conversation"), config.get("ntfy"))
    config["wallets"] = [_prompt_wallet_config(passphrase)]
    return config


def _apply_section(config: dict, section: str, args: argparse.Namespace) -> dict:
    if section == "full":
        return _apply_full_setup(config, args, existing_secret=_config_has_encrypted_values(config))

    if section == "app":
        config["app"] = _prompt_app_config(config.get("app"))
        return config

    if section == "electrum":
        config["electrum"] = _prompt_electrum_config(config.get("electrum"))
        return config

    if section == "mempool":
        config["mempool"] = _prompt_mempool_config(config.get("mempool"))
        return config

    if section == "tor":
        config["tor"] = _prompt_tor_config(config.get("tor"))
        return config

    if section == "conversation":
        passphrase = _get_encryption_passphrase(args, existing_secret=_config_has_encrypted_values(config))
        config["conversation"] = _prompt_conversation_config(passphrase, config.get("conversation"), config.get("ntfy"))
        return config

    if section == "ntfy":
        passphrase = _get_encryption_passphrase(args, existing_secret=_config_has_encrypted_values(config))
        config["ntfy"] = _prompt_ntfy_config(passphrase, config.get("ntfy"))
        return config

    if section == "wallet":
        passphrase = _get_encryption_passphrase(args, existing_secret=_config_has_encrypted_values(config))
        config.setdefault("wallets", [])
        config["wallets"].append(_prompt_wallet_config(passphrase))
        return config

    raise ValueError(f"Unsupported init section: {section}")


def _choose_existing_config_action(config_path: Path) -> str:
    print()
    print(f"Existing config found: {config_path}")
    print()
    return _prompt_choice(
        "What would you like to do?",
        {
            "1": "Add or update part of the existing config",
            "2": "Reset and create a new config",
            "3": "Exit without changing anything",
        },
        default="1",
    )


def _choose_section_to_add() -> str:
    print()
    return _prompt_choice(
        "What would you like to configure?",
        {
            "1": "ntfy credentials/server/topic",
            "2": "Electrum/Fulcrum node",
            "3": "Add wallet xpub",
            "4": "Application settings",
            "5": "Optional Mempool API enrichment",
            "6": "Tor upstream for onion Electrum/Fulcrum nodes",
            "7": "Conversation Mode",
            "8": "Full setup wizard",
            "9": "Cancel",
        },
        default="1",
    )


def _section_from_menu_choice(choice: str) -> str | None:
    return {
        "1": "ntfy",
        "2": "electrum",
        "3": "wallet",
        "4": "app",
        "5": "mempool",
        "6": "tor",
        "7": "conversation",
        "8": "full",
        "9": None,
    }[choice]


def _print_save_summary(config_path: Path, config: dict) -> None:
    print()
    print(f"Wrote config to {config_path}")

    if not config.get("wallets"):
        print()
        print("Note: this config does not contain any wallet xpubs yet.")
        print("Add one with:")
        print(f"  wwg init --config {config_path} --add wallet")
        print()
        print("Docker Compose:")
        print(f"  docker compose run --rm wallet-watchguard wwg init --config {config_path} --add wallet")
        return

    print("Run locally with:")
    print(f"  wwg run --config {config_path}")
    print()
    print("Run via Docker Compose with:")
    print("  WWG_PASSPHRASE='your passphrase here' docker compose up -d")
    print()
    print("Useful checks:")
    print(f"  wwg status --config {config_path}")
    print(f"  wwg test-ntfy --config {config_path}")
    print(f"  wwg test-tor --config {config_path}")
    print(f"  wwg addresses --config {config_path} --limit 20")


def _make_electrum_client(config: dict) -> ElectrumClient:
    electrum = config["electrum"]
    return ElectrumClient(
        electrum["host"],
        int(electrum["port"]),
        use_tls=bool(electrum.get("tls", True)),
        tls_verify=electrum.get("tls_verify"),
        socks_proxy=electrum.get("socks_proxy"),
        timeout_seconds=int(electrum.get("timeout_seconds", 30)),
    )


def _wallet_xpub(wallet: dict, passphrase: str) -> str:
    return decrypt_xpub_with_passphrase(
        encrypted_xpub_b64=wallet["encrypted_xpub"],
        passphrase=passphrase,
        metadata=metadata_from_config(wallet["xpub_encryption"]),
    )


def _derive_for_cli(config: dict, wallet: dict, passphrase: str, *, branch: int, limit: int):
    xpub = _wallet_xpub(wallet, passphrase)
    helper_path = config["app"].get("derivation_helper_path", "./wwg-derive")
    path_template = wallet.get("receive_path_template", "0/*") if branch == 0 else wallet.get("change_path_template", "1/*")

    return derive_addresses(
        helper_path=helper_path,
        wallet_name=wallet["name"],
        xpub=xpub,
        network=wallet["network"],
        wallet_type=wallet["wallet_type"],
        account_path=wallet["account_path"],
        path_template=path_template,
        branch=branch,
        start=0,
        end=limit - 1,
    )


def _runtime_config(config: dict, args: argparse.Namespace) -> dict:
    force_tor = bool(getattr(args, "tor_upstream", False)) or env_tor_upstream_enabled()
    return apply_tor_upstream(config, force_enabled=force_tor)


def _add_tor_upstream_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--tor-upstream",
        action="store_true",
        help="Enable Wallet Watchguard's internal Tor upstream for this command",
    )


def _load_config_for_persistent_tor_edit(config_path: Path) -> dict:
    if not config_path.exists():
        _print_missing_config_help(config_path)
        raise FileNotFoundError(f"Config file not found: {config_path}")

    if config_path.is_dir():
        raise IsADirectoryError(f"Config path is a directory, not a file: {config_path}")

    return load_config_for_edit(config_path)


def _set_persistent_tor_enabled(config: dict, *, enabled: bool) -> dict:
    default_tor = default_config().get("tor") or {}
    existing_tor = config.get("tor") or {}
    tor = {**default_tor, **existing_tor}
    tor["enabled"] = enabled

    # Keep user custom values, but repair missing/blank values so enabling Tor
    # produces a runnable config even on older config.yaml files.
    if not str(tor.get("socks_proxy") or "").strip():
        tor["socks_proxy"] = default_tor.get("socks_proxy", "127.0.0.1:9050")
    if int(tor.get("startup_timeout_seconds") or 0) < 1:
        tor["startup_timeout_seconds"] = default_tor.get("startup_timeout_seconds", 60)

    config["tor"] = tor
    return config


def _print_tor_config_summary(config_path: Path, config: dict) -> None:
    print()
    print(f"Tor upstream config: {config_path}")
    for line in tor_upstream_lines(config):
        print(line)

    if env_tor_upstream_enabled():
        print("Environment override: WWG_TOR_UPSTREAM=true is active for runtime commands")


def _print_tor_next_steps(config_path: Path, *, enabled: bool) -> None:
    print()
    if enabled:
        print("Next checks:")
        print(f"  wwg tor status --config {config_path}")
        print(f"  wwg test-tor --config {config_path}")
        print(f"  wwg run --config {config_path}")
    else:
        print("Tor upstream is now disabled in config.yaml.")
        print("Manual electrum.socks_proxy settings, if present, were left untouched.")



async def _cmd_test_ntfy_async(config: dict, passphrase: str) -> None:
    notifier = NtfyNotifier(decrypt_ntfy_config(config["ntfy"], passphrase))
    message = (
        "Bitcoin Wallet Watchguard successfully published this test notification via ntfy.\n\n"
        "This is free and open source software made by a fellow bitcoiner.\n"
        "If you appreciate it, my Lightning address is xanny@cake.cash ⚡"
    )
    await notifier.send(
        "⚡ Wallet Watchguard: Test Alert",
        message,
        priority="default",
        tags="white_check_mark,bitcoin",
    )


async def _cmd_test_tor_async(config: dict) -> str:
    tor_upstream = TorUpstreamManager(config)
    if not tor_upstream.enabled:
        raise ValueError("Tor upstream is disabled. Enable tor.enabled in config.yaml, set WWG_TOR_UPSTREAM=true, or pass --tor-upstream.")

    client = _make_electrum_client(config)

    async def ignore_notifications(message: dict) -> None:
        _ = message

    await tor_upstream.start()
    listener_task: asyncio.Task | None = None
    try:
        await client.connect()
        listener_task = asyncio.create_task(client.listen(ignore_notifications))
        result = await client.call("server.version", ["wallet-watchguard", "1.4"])
        return f"ok ({format_server_version(result)})"
    finally:
        if listener_task is not None:
            listener_task.cancel()
            try:
                await listener_task
            except asyncio.CancelledError:
                pass
        await client.close()
        await tor_upstream.stop()


def _wallet_label(wallet: dict, index: int) -> str:
    return f"{index + 1}. {wallet['name']} ({wallet['network']} / {wallet['wallet_type']})"


def _select_wallets_for_addresses(config: dict, args: argparse.Namespace) -> list[dict]:
    wallets = config.get("wallets") or []
    if not wallets:
        raise ValueError("No wallets are configured")

    if getattr(args, "all", False):
        return wallets

    if args.wallet_index is not None:
        index = int(args.wallet_index) - 1
        if index < 0 or index >= len(wallets):
            raise ValueError(f"Wallet index must be between 1 and {len(wallets)}")
        return [wallets[index]]

    if args.wallet:
        requested = args.wallet.strip()
        exact = [w for w in wallets if w["name"] == requested]
        if exact:
            return exact

        requested_lower = requested.lower()
        partial = [w for w in wallets if requested_lower in w["name"].lower()]
        if len(partial) == 1:
            return partial
        if len(partial) > 1:
            print("Multiple wallets matched:")
            for i, wallet in enumerate(wallets):
                if wallet in partial:
                    print(f"  {_wallet_label(wallet, i)}")
            raise ValueError("Please use the exact wallet name or --wallet-index")

        print("Configured wallets:")
        for i, wallet in enumerate(wallets):
            print(f"  {_wallet_label(wallet, i)}")
        raise ValueError(f"No wallet matched {requested!r}")

    if len(wallets) == 1:
        return [wallets[0]]

    print()
    print("Multiple wallets are configured.")
    print("Choose which wallet to view, or type 'all' to show every wallet.")
    print()
    for i, wallet in enumerate(wallets):
        print(f"  {_wallet_label(wallet, i)}")

    while True:
        choice = _prompt("Wallet number or all", "1").strip().lower()
        if choice == "all":
            return wallets
        try:
            index = int(choice) - 1
        except ValueError:
            print("Please enter a wallet number or 'all'.")
            continue
        if 0 <= index < len(wallets):
            return [wallets[index]]
        print(f"Please choose a number between 1 and {len(wallets)}, or 'all'.")


async def _cmd_addresses_async(config: dict, passphrase: str, args: argparse.Namespace) -> None:
    electrum = config["electrum"]
    client = _make_electrum_client(config)

    def debug(message: str) -> None:
        if args.debug:
            print(f"debug: {message}", file=sys.stderr)

    async def ignore_notifications(message: dict) -> None:
        debug(f"Electrum notification ignored by addresses command: {message}")

    debug(
        "connecting to Electrum/Fulcrum "
        f"{electrum['host']}:{electrum['port']} "
        f"tls={bool(electrum.get('tls', True))} "
        f"tls_verify={client.tls_verify} "
        f"socks_proxy={electrum.get('socks_proxy') or 'none'}"
    )

    await client.connect()

    # ElectrumClient.call() is completed by ElectrumClient.listen(), which reads
    # JSON-RPC responses from the socket. The long-running watcher already starts
    # listen(), but this one shot CLI command did not. Without this background
    # reader, balance/history calls can time out after printing only the table
    # header.
    listener_task = asyncio.create_task(client.listen(ignore_notifications))

    try:
        wallets = _select_wallets_for_addresses(config, args)
        debug(f"selected {len(wallets)} wallet(s): {', '.join(w['name'] for w in wallets)}")
        debug(f"derivation helper path: {config['app'].get('derivation_helper_path', './wwg-derive')}")
        debug(f"address limit per branch: {args.limit}")

        for wallet in wallets:
            print()
            print(f"Wallet: {wallet['name']} ({wallet['network']} / {wallet['wallet_type']})")
            print("-" * 104)
            print(f"{'branch':<8} {'path':<18} {'status':<8} {'confirmed':>14} {'unconfirmed':>14}  address")
            print("-" * 104)

            branches = [0, 1] if args.include_change else [0]
            wallet_confirmed = 0
            wallet_unconfirmed = 0
            printed_rows = 0
            derived_rows = 0
            queried_rows = 0

            for branch in branches:
                label = "receive" if branch == 0 else "change"
                path_template = wallet.get("receive_path_template", "0/*") if branch == 0 else wallet.get("change_path_template", "1/*")
                debug(
                    f"deriving wallet={wallet['name']!r} branch={label} "
                    f"template={path_template!r} wallet_type={wallet['wallet_type']} "
                    f"network={wallet['network']} account_path={wallet['account_path']!r}"
                )

                derived = _derive_for_cli(config, wallet, passphrase, branch=branch, limit=args.limit)
                derived_rows += len(derived)
                debug(f"derived {len(derived)} {label} address(es)")

                if args.debug and derived:
                    first = derived[0]
                    debug(
                        f"first {label} address: path={first.path} "
                        f"address={first.address} scripthash={first.scripthash}"
                    )

                for item in derived:
                    debug(f"querying balance/history for {item.path} {item.address}")
                    try:
                        balance = await client.call("blockchain.scripthash.get_balance", [item.scripthash])
                        history = await client.call("blockchain.scripthash.get_history", [item.scripthash])
                    except Exception as exc:
                        print(
                            f"error: failed to query Electrum/Fulcrum for {item.path} {item.address}: {exc}",
                            file=sys.stderr,
                        )
                        raise

                    queried_rows += 1
                    confirmed = int(balance.get("confirmed") or 0)
                    unconfirmed = int(balance.get("unconfirmed") or 0)
                    used = bool(history)
                    status = "used" if used else "unused"
                    debug(
                        f"result {item.path}: confirmed={confirmed} "
                        f"unconfirmed={unconfirmed} history_entries={len(history)}"
                    )

                    wallet_confirmed += confirmed
                    wallet_unconfirmed += unconfirmed

                    if args.only_nonzero and confirmed == 0 and unconfirmed == 0:
                        continue
                    if args.only_used and not used:
                        continue

                    printed_rows += 1
                    print(
                        f"{label:<8} {item.path:<18} {status:<8} "
                        f"{confirmed:>14,} {unconfirmed:>14,}  {item.address}"
                    )

            if printed_rows == 0:
                print("No addresses matched the selected filters.")
                print(f"Derived addresses: {derived_rows}; queried addresses: {queried_rows}.")
                if args.only_nonzero or args.only_used:
                    print("Try running without --only-nonzero/--only-used to show unused receive addresses.")
                if derived_rows == 0:
                    print("No addresses were derived. Check the derivation helper path, wallet type and path templates.")
                print("For verbose troubleshooting, rerun with --debug.")

            print("-" * 104)
            print(f"{'total':<36} {wallet_confirmed:>14,} {wallet_unconfirmed:>14,}")
    finally:
        listener_task.cancel()
        try:
            await listener_task
        except asyncio.CancelledError:
            pass
        await client.close()



async def _cmd_fees_async(config: dict) -> None:
    mempool = MempoolClient(config.get("mempool") or {})
    fees = await mempool.get_recommended_fees()
    print(format_mempool_fee_summary(fees))


async def _cmd_next_async(config: dict, passphrase: str, args: argparse.Namespace, *, count: int) -> None:
    client = _make_electrum_client(config)

    async def ignore_notifications(message: dict) -> None:
        _ = message

    await client.connect()
    # As with the addresses command, ElectrumClient.call() is only completed by a
    # running listen() loop, so start one for this one shot command.
    listener_task = asyncio.create_task(client.listen(ignore_notifications))

    try:
        wallets = _select_wallets_for_addresses(config, args)
        default_lookahead = int(config["app"].get("lookahead", 100))

        for wallet in wallets:
            scan_limit = max(int(wallet.get("lookahead", default_lookahead)), count)
            derived = _derive_for_cli(config, wallet, passphrase, branch=0, limit=scan_limit)

            found = []
            for item in derived:
                history = await client.call("blockchain.scripthash.get_history", [item.scripthash])
                if not history:
                    found.append(item)
                    if len(found) >= count:
                        break

            print()
            print(f"Wallet: {wallet['name']} ({wallet['network']} / {wallet['wallet_type']})")
            if not found:
                print(f"No unused receive address found within lookahead {scan_limit}.")
                print("Increase the wallet lookahead or check the wallet derivation path.")
                continue

            label = "address" if len(found) == 1 else "addresses"
            print(f"Next unused receive {label}:")
            for item in found:
                print(f"  {item.path:<18} {item.address}")
    finally:
        listener_task.cancel()
        try:
            await listener_task
        except asyncio.CancelledError:
            pass
        await client.close()


def cmd_init(args: argparse.Namespace) -> int:
    config_path = Path(args.config)

    if args.reset and args.add:
        raise ValueError("Use either --reset or --add, not both")

    if args.add and args.add not in INIT_SECTIONS:
        raise ValueError(f"--add must be one of: {', '.join(INIT_SECTIONS)}")

    print("Bitcoin Wallet Watchguard setup")
    print()
    print("Privacy recommendation: use your own Bitcoin node + Fulcrum, and your own self-hosted ntfy instance.")
    print("Using public Electrum servers with an xpub-derived watcher can leak wallet activity.")

    config_exists = config_path.exists()

    if args.reset:
        print()
        print("Reset requested. Starting with a new config.")
        config = default_config()
        config = _apply_full_setup(config, args, existing_secret=False)
        save_config(config_path, config)
        _print_save_summary(config_path, config)
        return 0

    if args.add:
        config = load_config_for_edit(config_path)
        if not config_exists:
            print()
            print(f"No existing config found at {config_path}; creating one and configuring: {args.add}")
        config = _apply_section(config, args.add, args)
        save_config(config_path, config)
        _print_save_summary(config_path, config)
        return 0

    if not config_exists:
        print()
        print(f"No config found at {config_path}.")
        print("Starting first-time setup wizard.")
        config = _apply_full_setup(default_config(), args, existing_secret=False)
        save_config(config_path, config)
        _print_save_summary(config_path, config)
        return 0

    config = load_config_for_edit(config_path)
    action = _choose_existing_config_action(config_path)

    if action == "3":
        print("No changes made.")
        return 0

    if action == "2":
        if not _prompt_bool("This will replace the existing config. Continue", False):
            print("No changes made.")
            return 0
        config = _apply_full_setup(default_config(), args, existing_secret=False)
        save_config(config_path, config)
        _print_save_summary(config_path, config)
        return 0

    section_choice = _choose_section_to_add()
    section = _section_from_menu_choice(section_choice)

    if section is None:
        print("No changes made.")
        return 0

    config = _apply_section(config, section, args)
    save_config(config_path, config)
    _print_save_summary(config_path, config)
    return 0


def cmd_encrypt_xpub(args: argparse.Namespace) -> int:
    passphrase = args.passphrase or prompt_new_passphrase()
    encrypted_xpub, metadata = encrypt_xpub_with_passphrase(args.xpub, passphrase)

    snippet = {
        "xpub_encryption": metadata_to_config(metadata),
        "encrypted_xpub": encrypted_xpub,
    }
    print(yaml.safe_dump(snippet, sort_keys=False))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config_path = Path(args.config)

    if not config_path.exists():
        _print_missing_config_help(config_path)
        return 2

    if config_path.is_dir():
        print(file=sys.stderr)
        print(f"Config path is a directory, not a file: {config_path}", file=sys.stderr)
        print(file=sys.stderr)
        print("Point --config at a YAML file, for example:", file=sys.stderr)
        print(f"  wwg run --config {config_path / 'config.yaml'}", file=sys.stderr)
        print(file=sys.stderr)
        return 2

    config = _runtime_config(load_config(config_path), args)
    if args.conversation:
        config.setdefault("conversation", {})["enabled"] = True
    passphrase = args.passphrase or get_passphrase_from_env_or_prompt()
    watcher = Watcher(config, passphrase, config_path=config_path)
    asyncio.run(watcher.run())
    return 0


def cmd_test_ntfy(args: argparse.Namespace) -> int:
    config = load_config_for_edit(args.config)
    passphrase = args.passphrase or get_passphrase_from_env_or_prompt()
    asyncio.run(_cmd_test_ntfy_async(config, passphrase))
    print("ntfy test message sent successfully.")
    return 0


def cmd_addresses(args: argparse.Namespace) -> int:
    config = _runtime_config(load_config(args.config), args)
    passphrase = args.passphrase or get_passphrase_from_env_or_prompt()
    asyncio.run(_cmd_addresses_async(config, passphrase, args))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = _runtime_config(load_config(args.config), args)
    print(build_status_text(config, config_path=args.config))
    return 0


def cmd_wallets(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    wallets = config.get("wallets") or []
    if not wallets:
        print("No wallets are configured.")
        print(f"Add one with: wwg init --config {args.config} --add wallet")
        return 0

    print("Configured wallets:")
    for index, wallet in enumerate(wallets):
        print(f"  {_wallet_label(wallet, index)}")
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    config = _runtime_config(load_config(args.config), args)
    passphrase = args.passphrase or get_passphrase_from_env_or_prompt()
    count = _parse_address_count(args.count)
    asyncio.run(_cmd_next_async(config, passphrase, args, count=count))
    return 0


def cmd_balance(args: argparse.Namespace) -> int:
    config = _runtime_config(load_config(args.config), args)
    passphrase = args.passphrase or get_passphrase_from_env_or_prompt()

    # `wwg balance` is `wwg addresses` scoped to non-zero balances, including the
    # change branch. With no wallet filter it covers every configured wallet,
    # matching the Conversation Mode `balance` command.
    scoped = args.wallet is not None or args.wallet_index is not None
    balance_args = argparse.Namespace(
        config=args.config,
        passphrase=passphrase,
        wallet=args.wallet,
        wallet_index=args.wallet_index,
        all=not scoped,
        limit=int(args.limit),
        include_change=True,
        only_nonzero=True,
        only_used=False,
        debug=getattr(args, "debug", False),
        tor_upstream=getattr(args, "tor_upstream", False),
    )
    asyncio.run(_cmd_addresses_async(config, passphrase, balance_args))
    return 0


def cmd_test_tor(args: argparse.Namespace) -> int:
    config = _runtime_config(load_config(args.config), args)
    result = asyncio.run(_cmd_test_tor_async(config))
    print(f"Tor connectivity test: {result}")
    return 0


def cmd_tor_enable(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    config = _load_config_for_persistent_tor_edit(config_path)
    config = _set_persistent_tor_enabled(config, enabled=True)
    save_config(config_path, config)

    print(f"Internal Tor upstream enabled in {config_path}")
    _print_tor_config_summary(config_path, config)
    _print_tor_next_steps(config_path, enabled=True)
    return 0


def cmd_tor_disable(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    config = _load_config_for_persistent_tor_edit(config_path)
    config = _set_persistent_tor_enabled(config, enabled=False)
    save_config(config_path, config)

    print(f"Internal Tor upstream disabled in {config_path}")
    _print_tor_config_summary(config_path, config)
    _print_tor_next_steps(config_path, enabled=False)
    return 0


def cmd_tor_status(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    config = _load_config_for_persistent_tor_edit(config_path)
    _print_tor_config_summary(config_path, config)
    return 0


def cmd_fees(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    asyncio.run(_cmd_fees_async(config))
    return 0

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wallet-watchguard")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Run interactive setup/update wizard")
    p_init.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    p_init.add_argument("--reset", action="store_true", help="Replace the existing config with a new one")
    p_init.add_argument(
        "--add",
        choices=INIT_SECTIONS,
        default=None,
        help="Jump directly to a setup section: full, electrum, ntfy, wallet, app, mempool, tor, or Conversation Mode",
    )
    p_init.add_argument("--passphrase", default=None, help="Encryption passphrase; otherwise prompt")
    p_init.set_defaults(func=cmd_init)

    p_enc = sub.add_parser("encrypt-xpub", help="Encrypt an xpub for storage in YAML")
    p_enc.add_argument("--xpub", required=True)
    p_enc.add_argument("--passphrase", default=None, help="Encryption passphrase; otherwise prompt")
    p_enc.set_defaults(func=cmd_encrypt_xpub)

    p_run = sub.add_parser("run", help="Run Wallet Watchguard daemon")
    p_run.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    p_run.add_argument("--passphrase", default=None, help="Encryption passphrase; otherwise prompt/env")
    p_run.add_argument(
        "--conversation",
        action="store_true",
        help="Enable ntfy Conversation Mode for this run, subject to topic protection checks",
    )
    _add_tor_upstream_arg(p_run)
    p_run.set_defaults(func=cmd_run)

    p_test = sub.add_parser("test-ntfy", help="Send a test ntfy notification using the configured credentials")
    p_test.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    p_test.add_argument("--passphrase", default=None, help="Encryption passphrase; otherwise prompt/env")
    p_test.set_defaults(func=cmd_test_ntfy)

    p_addr = sub.add_parser(
        "addresses",
        aliases=["list-addresses"],
        help="List derived wallet addresses and Electrum/Fulcrum balances",
    )
    p_addr.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    p_addr.add_argument("--passphrase", default=None, help="Encryption passphrase; otherwise prompt/env")
    p_addr.add_argument("--wallet", default=None, help="Only show one wallet by exact name or unique partial name")
    p_addr.add_argument("--wallet-index", type=int, default=None, help="Only show one wallet by its 1-based index in config")
    p_addr.add_argument("--all", action="store_true", help="Show every configured wallet without prompting")
    p_addr.add_argument("--limit", type=int, default=20, help="Number of receive/change addresses to derive per wallet")
    p_addr.add_argument("--include-change", action="store_true", help="Also list the change branch")
    p_addr.add_argument("--only-nonzero", action="store_true", help="Hide addresses with zero confirmed and unconfirmed balance")
    p_addr.add_argument("--only-used", action="store_true", help="Hide addresses with no Electrum history")
    p_addr.add_argument("--debug", action="store_true", help="Print derivation and Electrum query diagnostics to stderr")
    _add_tor_upstream_arg(p_addr)
    p_addr.set_defaults(func=cmd_addresses)

    p_status = sub.add_parser("status", help="Show Wallet Watchguard startup/status summary")
    p_status.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    _add_tor_upstream_arg(p_status)
    p_status.set_defaults(func=cmd_status)

    p_wallets = sub.add_parser(
        "wallets",
        aliases=["list-wallets"],
        help="List configured wallets",
    )
    p_wallets.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    p_wallets.set_defaults(func=cmd_wallets)

    p_next = sub.add_parser(
        "next",
        aliases=["next-address"],
        help="Show the next unused receive address(es), e.g. 'wwg next address' or 'wwg next 3'",
    )
    p_next.add_argument(
        "count",
        nargs="?",
        default="1",
        help="How many unused receive addresses to show (a number, or the word 'address')",
    )
    p_next.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    p_next.add_argument("--passphrase", default=None, help="Encryption passphrase; otherwise prompt/env")
    p_next.add_argument("--wallet", default=None, help="Only use one wallet by exact name or unique partial name")
    p_next.add_argument("--wallet-index", type=int, default=None, help="Only use one wallet by its 1-based index in config")
    _add_tor_upstream_arg(p_next)
    p_next.set_defaults(func=cmd_next)

    p_balance = sub.add_parser(
        "balance",
        help="Show addresses with a non-zero balance across wallets (receive and change)",
    )
    p_balance.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    p_balance.add_argument("--passphrase", default=None, help="Encryption passphrase; otherwise prompt/env")
    p_balance.add_argument("--wallet", default=None, help="Only show one wallet by exact name or unique partial name")
    p_balance.add_argument("--wallet-index", type=int, default=None, help="Only show one wallet by its 1-based index in config")
    p_balance.add_argument("--limit", type=int, default=100, help="Number of receive/change addresses to scan per wallet")
    p_balance.add_argument("--debug", action="store_true", help="Print derivation and Electrum query diagnostics to stderr")
    _add_tor_upstream_arg(p_balance)
    p_balance.set_defaults(func=cmd_balance)

    p_test_tor = sub.add_parser("test-tor", help="Test the configured Tor upstream by querying Electrum/Fulcrum")
    p_test_tor.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    _add_tor_upstream_arg(p_test_tor)
    p_test_tor.set_defaults(func=cmd_test_tor)

    p_tor = sub.add_parser("tor", help="Manage persistent internal Tor upstream settings")
    tor_sub = p_tor.add_subparsers(dest="tor_command", required=True)

    p_tor_enable = tor_sub.add_parser("enable", help="Persistently enable the internal Tor upstream in config.yaml")
    p_tor_enable.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    p_tor_enable.set_defaults(func=cmd_tor_enable)

    p_tor_disable = tor_sub.add_parser("disable", help="Persistently disable the internal Tor upstream in config.yaml")
    p_tor_disable.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    p_tor_disable.set_defaults(func=cmd_tor_disable)

    p_tor_status = tor_sub.add_parser("status", help="Show persistent internal Tor upstream settings")
    p_tor_status.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    p_tor_status.set_defaults(func=cmd_tor_status)

    p_fees = sub.add_parser(
        "fees",
        aliases=["mempool-fees"],
        help="Show local Mempool low/medium/high Bitcoin fee recommendations",
    )
    p_fees.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    p_fees.set_defaults(func=cmd_fees)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
