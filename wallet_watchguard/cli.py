from __future__ import annotations

import argparse
import asyncio
import getpass
import secrets
import sys
from pathlib import Path

import yaml

from .config import default_config, load_config, save_config
from .crypto import (
    encrypt_string_with_passphrase,
    encrypt_xpub_with_passphrase,
    metadata_to_config,
    prompt_new_passphrase,
)
from .watcher import Watcher, get_passphrase_from_env_or_prompt


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


def _prompt_ntfy_config(passphrase: str) -> dict[str, object]:
    print()
    print("ntfy configuration")
    print("Recommendation: use a self-hosted ntfy instance with a dedicated Wallet Watchguard topic.")
    print("For locked-down instances such as Start9, create or choose a topic that Wallet Watchguard can publish to.")
    print("A good setup is: Wallet Watchguard user/token = write access; phone/user account = read access.")
    print()

    ntfy_server = _prompt("ntfy server URL", "https://ntfy.example.com")
    random_topic = f"wallet-watchguard-{secrets.token_hex(12)}"
    ntfy_topic = _prompt("Private ntfy topic", random_topic)

    print()
    print("Choose ntfy authentication mode:")
    print("  none  - no credentials; only sensible for LAN-only testing or a very locked-down private network")
    print("  token - ntfy access token / bearer token; recommended where available")
    print("  basic - username and password")
    auth_type = _prompt("Auth mode: none/token/basic", "token").lower()

    if auth_type not in ["none", "token", "basic"]:
        raise ValueError("ntfy auth mode must be one of: none, token, basic")

    auth: dict[str, object] = {"type": auth_type}

    if auth_type == "token":
        print()
        print("Enter the ntfy access token for Wallet Watchguard.")
        print("This will be encrypted in config.yaml using the same passphrase as your xpub.")
        token = _prompt_secret("ntfy access token")
        if not token:
            raise ValueError("ntfy token must not be blank when auth mode is token")
        encrypted_token, token_encryption = _encrypted_config_value(token, passphrase)
        auth.update(
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
        auth.update(
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
        "auth": auth,
        "priority": "high",
        "tags": "bitcoin,watch",
    }


def cmd_init(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    config = default_config()

    print("Bitcoin Wallet Watchguard setup")
    print()
    print("Privacy recommendation: use your own Bitcoin node + Fulcrum, and your own self-hosted ntfy instance.")
    print("Using public Electrum servers with an xpub-derived watcher can leak wallet activity.")
    print()

    print("First, set the passphrase used to encrypt sensitive values in config.yaml.")
    print("This passphrase encrypts your xpub and ntfy credentials at rest.")
    print("You will need it whenever Wallet Watchguard starts.")
    passphrase = prompt_new_passphrase()

    electrum_host = _prompt("Electrum/Fulcrum host", "127.0.0.1")
    use_tls = _prompt_bool("Use TLS", True)
    default_port = "50002" if use_tls else "50001"
    electrum_port = int(_prompt("Electrum/Fulcrum port", default_port))
    socks_proxy = _prompt("SOCKS5 proxy for onion host, blank for none", "")

    config["electrum"] = {
        "host": electrum_host,
        "port": electrum_port,
        "tls": use_tls,
        "socks_proxy": socks_proxy or None,
        "timeout_seconds": 30,
    }

    config["ntfy"] = _prompt_ntfy_config(passphrase)

    print()
    print("Wallet configuration")
    wallet_name = _prompt("Wallet name", "Main Taproot wallet")
    wallet_type = _prompt("Wallet type: taproot/native_segwit/nested_segwit/legacy", "taproot")
    network = _prompt("Network: bitcoin/testnet/signet/regtest", "bitcoin")
    account_path_default = {
        "taproot": "m/86'/0'/0'",
        "native_segwit": "m/84'/0'/0'",
        "nested_segwit": "m/49'/0'/0'",
        "legacy": "m/44'/0'/0'",
    }.get(wallet_type, "m/86'/0'/0'")
    account_path = _prompt("Account derivation path metadata", account_path_default)
    xpub = _prompt("Account xpub")

    encrypted_xpub, metadata = encrypt_xpub_with_passphrase(xpub, passphrase)

    config["wallets"].append(
        {
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
    )

    save_config(config_path, config)
    print()
    print(f"Wrote config to {config_path}")
    print("Run locally with:")
    print(f"  wwg run --config {config_path}")
    print()
    print("Run via Docker Compose with:")
    print("  WWG_PASSPHRASE='your passphrase here' docker compose up -d")
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

    p_init = sub.add_parser("init", help="Run interactive setup wizard")
    p_init.add_argument("--config", default="config.yaml")
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
