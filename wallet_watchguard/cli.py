from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
from pathlib import Path

import yaml

from .config import default_config, load_config, save_config
from .crypto import encrypt_xpub_with_passphrase, prompt_new_passphrase
from .watcher import Watcher, get_passphrase_from_env_or_prompt


def _prompt(label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or (default or "")


def _prompt_bool(label: str, default: bool = True) -> bool:
    default_text = "Y/n" if default else "y/N"
    value = input(f"{label} [{default_text}]: ").strip().lower()
    if not value:
        return default
    return value in ["y", "yes", "true", "1"]


def _encryption_metadata_to_config(metadata) -> dict[str, object]:
    return {
        "scheme": metadata.scheme,
        "kdf": metadata.kdf,
        "opslimit": metadata.opslimit,
        "memlimit": metadata.memlimit,
        "salt": metadata.salt_b64,
    }


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


def cmd_init(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    config = default_config()

    print("Bitcoin Wallet Watchguard setup")
    print()
    print("Privacy recommendation: use your own Bitcoin node + Fulcrum, and your own self-hosted ntfy instance.")
    print("Using public Electrum servers with an xpub-derived watcher can leak wallet activity.")
    print()

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

    print()
    print("ntfy configuration")
    print("Recommendation: use a self-hosted ntfy instance with a long random private topic name.")
    ntfy_server = _prompt("ntfy server URL", "https://ntfy.example.com")
    random_topic = f"wallet-watchguard-{secrets.token_hex(12)}"
    ntfy_topic = _prompt("Private ntfy topic", random_topic)
    ntfy_token = _prompt("ntfy bearer token, blank for none", "")

    config["ntfy"]["server"] = ntfy_server
    config["ntfy"]["topic"] = ntfy_topic
    config["ntfy"]["token"] = ntfy_token or None

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

    print()
    print("Set the passphrase used to encrypt the xpub at rest.")
    print("You will need this passphrase whenever Wallet Watchguard starts.")
    passphrase = prompt_new_passphrase()

    encrypted_xpub, metadata = encrypt_xpub_with_passphrase(xpub, passphrase)

    config["wallets"].append(
        {
            "name": wallet_name,
            "network": network,
            "wallet_type": wallet_type,
            "account_path": account_path,
            "xpub_encryption": _encryption_metadata_to_config(metadata),
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
        "xpub_encryption": _encryption_metadata_to_config(metadata),
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