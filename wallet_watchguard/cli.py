from __future__ import annotations

import argparse
import asyncio
import getpass
import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import yaml

from .config import default_config, load_config, load_config_for_edit, save_config
from .crypto import (
    encrypt_string_with_passphrase,
    encrypt_xpub_with_passphrase,
    metadata_to_config,
    prompt_existing_passphrase,
    prompt_new_passphrase,
)
from .watcher import Watcher, get_passphrase_from_env_or_prompt


INIT_SECTIONS = ["full", "electrum", "ntfy", "wallet", "app"]


def _prompt(label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or (default or "")


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
    print(f"  wwg init --config {config_path}", file=sys.stderr)
    print(file=sys.stderr)
    print("If you are using Docker Compose, run:", file=sys.stderr)
    print(f"  docker compose run --rm wallet-watchguard wwg init --config {config_path}", file=sys.stderr)
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
    print()

    current_tls = bool(existing_electrum.get("tls", True))
    electrum_host = _prompt("Electrum/Fulcrum host", str(existing_electrum.get("host", "127.0.0.1")))
    use_tls = _prompt_bool("Use TLS", current_tls)
    default_port = "50002" if use_tls else "50001"
    current_port = existing_electrum.get("port") or default_port
    electrum_port = int(_prompt("Electrum/Fulcrum port", str(current_port)))
    socks_proxy = _prompt(
        "SOCKS5 proxy for onion host, blank for none",
        str(existing_electrum.get("socks_proxy") or ""),
    )

    return {
        "host": electrum_host,
        "port": electrum_port,
        "tls": use_tls,
        "socks_proxy": socks_proxy or None,
        "timeout_seconds": int(_prompt("Connection timeout seconds", str(existing_electrum.get("timeout_seconds", 30)))),
    }


def _prompt_ntfy_start9_config(passphrase: str, existing_ntfy: dict | None = None) -> dict[str, object]:
    existing_ntfy = existing_ntfy or {}
    existing_topic = existing_ntfy.get("topic")
    suggested_topic = existing_topic or f"wallet-watchguard-{secrets.token_hex(12)}"

    print()
    print("Start9 / StartOS ntfy publisher provisioning")
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
    }


def _prompt_ntfy_config(passphrase: str, existing_ntfy: dict | None = None) -> dict[str, object]:
    existing_ntfy = existing_ntfy or {}

    print()
    print("ntfy configuration")
    print("Recommendation: use a self-hosted ntfy instance with a dedicated Wallet Watchguard topic.")
    print("A good setup is: Wallet Watchguard publisher/token = write access; phone/user account = read access.")
    print("Avoid broad anonymous publish permissions unless you deliberately want that.")

    if existing_ntfy.get("topic"):
        print()
        print(f"Existing topic stored in config: {existing_ntfy['topic']}")
        print("If you are provisioning a new Start9 publisher, use this exact topic name unless you want to change it.")

    if _prompt_bool("Are you using Start9/StartOS 'Provision Publisher' details", False):
        return _prompt_ntfy_start9_config(passphrase, existing_ntfy)

    ntfy_server = _prompt("ntfy server URL", str(existing_ntfy.get("server", "https://ntfy.example.com")))
    random_topic = f"wallet-watchguard-{secrets.token_hex(12)}"
    ntfy_topic = _prompt("Private ntfy topic", str(existing_ntfy.get("topic", random_topic)))

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
        }

    print()
    print("Choose ntfy authentication mode:")
    print("  none  - no credentials; only sensible for LAN-only testing or a very locked-down private network")
    print("  token - ntfy access token / bearer token; recommended where available")
    print("  basic - username and password")

    existing_auth_type = auth.get("type", "token")
    if existing_auth_type not in ["none", "token", "basic"]:
        existing_auth_type = "token"

    auth_type = _prompt("Auth mode: none/token/basic", existing_auth_type).lower()

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
            "5": "Full setup wizard",
            "6": "Cancel",
        },
        default="1",
    )


def _section_from_menu_choice(choice: str) -> str | None:
    return {
        "1": "ntfy",
        "2": "electrum",
        "3": "wallet",
        "4": "app",
        "5": "full",
        "6": None,
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

    config = load_config(config_path)
    passphrase = args.passphrase or get_passphrase_from_env_or_prompt()
    watcher = Watcher(config, passphrase)
    asyncio.run(watcher.run())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wallet-watchguard")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Run interactive setup/update wizard")
    p_init.add_argument("--config", default="config.yaml")
    p_init.add_argument("--reset", action="store_true", help="Replace the existing config with a new one")
    p_init.add_argument(
        "--add",
        choices=INIT_SECTIONS,
        default=None,
        help="Jump directly to a setup section: full, electrum, ntfy, wallet, or app",
    )
    p_init.add_argument("--passphrase", default=None, help="Encryption passphrase; otherwise prompt")
    p_init.set_defaults(func=cmd_init)

    p_enc = sub.add_parser("encrypt-xpub", help="Encrypt an xpub for storage in YAML")
    p_enc.add_argument("--xpub", required=True)
    p_enc.add_argument("--passphrase", default=None, help="Encryption passphrase; otherwise prompt")
    p_enc.set_defaults(func=cmd_encrypt_xpub)

    p_run = sub.add_parser("run", help="Run Wallet Watchguard daemon")
    p_run.add_argument("--config", default="config.yaml")
    p_run.add_argument("--passphrase", default=None, help="Encryption passphrase; otherwise prompt/env")
    p_run.set_defaults(func=cmd_run)

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
