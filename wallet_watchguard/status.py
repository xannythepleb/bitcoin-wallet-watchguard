from __future__ import annotations

import tomllib
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any

from .tor import tor_config_from_app_config


def get_app_version() -> str:
    try:
        return package_version("wallet-watchguard")
    except PackageNotFoundError:
        pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        try:
            with pyproject_path.open("rb") as handle:
                data = tomllib.load(handle)
            return str(data.get("project", {}).get("version", "unknown"))
        except Exception:
            return "unknown"


def format_server_version(result: Any) -> str:
    if isinstance(result, list):
        return ", ".join(str(item) for item in result)
    return str(result)


def tor_upstream_line(config: dict[str, Any], *, tor_connectivity_result: str | None = None) -> str:
    tor_cfg = tor_config_from_app_config(config)
    electrum = config.get("electrum") or {}
    manual_socks_proxy = str(electrum.get("socks_proxy") or "").strip()

    if tor_cfg.enabled:
        parts = ["enabled", f"proxy={tor_cfg.socks_proxy}"]
        parts.append("managed_process=true" if tor_cfg.manage_process else "managed_process=false")
        if tor_connectivity_result:
            parts.append(f"test={tor_connectivity_result}")
        elif tor_cfg.test_on_startup:
            parts.append("test=pending")
        else:
            parts.append("test=disabled")
        return "Tor Upstream: " + " ".join(parts)

    if manual_socks_proxy:
        return f"Tor Upstream: disabled (manual SOCKS proxy configured: {manual_socks_proxy})"

    return "Tor Upstream: disabled"


def build_status_text(
    config: dict[str, Any],
    *,
    config_path: str | Path,
    ntfy_config: dict[str, Any] | None = None,
    conversation_ntfy_config: dict[str, Any] | None = None,
    subscription_count: int | None = None,
    conversation_started: bool | None = None,
    tor_connectivity_result: str | None = None,
    include_useful_commands: bool = True,
) -> str:
    electrum = config["electrum"]
    ntfy = ntfy_config or config["ntfy"]
    mempool_config = config.get("mempool") or {}
    conversation = config.get("conversation") or {}

    version = get_app_version()
    version_label = f"v{version}" if version != "unknown" and not version.startswith("v") else version

    lines: list[str] = [
        "",
        f"Bitcoin Wallet Watchguard {version_label} by xannythepleb is running",
        "-------------------------------------",
        f"Version: {version_label}",
        f"Config: {config_path}",
        f"Database: {config['app']['database_path']}",
        "Electrum/Fulcrum: "
        f"{electrum['host']}:{electrum['port']} "
        f"tls={bool(electrum.get('tls', True))} "
        f"tls_verify={bool(electrum.get('tls_verify', True))}",
        tor_upstream_line(config, tor_connectivity_result=tor_connectivity_result),
        f"ntfy: {str(ntfy['server']).rstrip('/')}/{ntfy['topic']} "
        f"auth={(ntfy.get('auth') or {}).get('type', 'none')} "
        f"tls_verify={bool(ntfy.get('tls_verify', True))}",
    ]

    mempool_enabled = bool(mempool_config.get("enabled", False))
    mempool_base_url = str(mempool_config.get("base_url", "") or "")
    mempool_tls_verify = bool(mempool_config.get("tls_verify", True))
    mempool_enrich = bool(mempool_config.get("enrich_notifications", True))
    if mempool_enabled:
        lines.append(
            "Mempool API: enabled"
            + (f" ({mempool_base_url})" if mempool_base_url else "")
            + f" tls_verify={mempool_tls_verify} enrich_notifications={mempool_enrich}"
        )
    else:
        lines.append("Mempool API: disabled")

    requested = bool(conversation.get("enabled", False))
    conversation_topic = (conversation_ntfy_config or {}).get("topic") or ntfy.get("topic")
    conversation_server = (conversation_ntfy_config or {}).get("server") or ntfy.get("server")
    if conversation_started is True:
        conversation_status = "enabled"
        conversation_extra = f" on {str(conversation_server).rstrip('/')}/{conversation_topic}"
    elif requested and conversation_started is False:
        conversation_status = "disabled"
        conversation_extra = " (requested, but not running; check permission/protection messages above)"
    elif requested:
        conversation_status = "configured"
        conversation_extra = " (runtime status unknown in CLI status command)"
    else:
        conversation_status = "disabled"
        conversation_extra = ""
    lines.append(f"Conversation Mode: {conversation_status}{conversation_extra}")

    if subscription_count is None:
        lines.append("Subscribed scripts: not connected in this status command")
    else:
        lines.append(f"Subscribed scripts: {subscription_count}")

    lines.extend(["", "Wallets:"])
    for wallet in config.get("wallets", []):
        lines.append(
            "  - "
            f"{wallet['name']} | {wallet['network']} | {wallet['wallet_type']} | "
            f"lookahead={wallet.get('lookahead', config['app'].get('lookahead', 100))}"
        )

    if include_useful_commands:
        lines.extend(
            [
                "",
                "Useful commands:",
                "  wwg status",
                "  wwg test-ntfy",
                "  wwg test-tor",
                "  wwg addresses --limit 20",
                "  wwg addresses --wallet \"<wallet name>\" --limit 20",
                "  wwg addresses --all --include-change --limit 20",
                "  wwg balance",
            ]
        )
        if mempool_enabled:
            lines.append("  wwg fees")
        if bool(conversation.get("enabled", False)):
            lines.append("  ntfy conversation: send 'wwg help' to the protected topic")

    lines.append("")
    return "\n".join(lines)
