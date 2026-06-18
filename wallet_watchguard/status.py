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


def _format_bool(value: bool) -> str:
    return "true" if value else "false"


def _split_connectivity_result(result: str) -> tuple[str, str | None]:
    """
    Split a compact connectivity result into a status and optional detail.
    """
    stripped = result.strip()

    for status in ("ok", "failed"):
        bracket_prefix = f"{status} ("
        if stripped.startswith(bracket_prefix) and stripped.endswith(")"):
            return status, stripped[len(bracket_prefix) : -1]

        plain_prefix = f"{status} "
        if stripped.startswith(plain_prefix):
            return status, stripped[len(plain_prefix) :].strip() or None

        if stripped == status:
            return status, None

    return stripped, None


def _effective_socks_proxy(config: dict[str, Any]) -> str:
    """
    Return the SOCKS proxy that the Electrum/Fulcrum client will use.
    Internal or external SOCKS proxy?
    """
    tor_cfg = tor_config_from_app_config(config)
    if tor_cfg.enabled:
        return tor_cfg.socks_proxy

    electrum = config.get("electrum") or {}
    return str(electrum.get("socks_proxy") or "").strip()


def electrum_upstream_lines(
    config: dict[str, Any],
    *,
    electrum_connectivity_result: str | None = None,
) -> list[str]:
    electrum = config["electrum"]
    socks_proxy = _effective_socks_proxy(config)

    lines = [
        "Electrum/Fulcrum:",
        f"  Server: {electrum['host']}:{electrum['port']}",
        f"  TLS: {_format_bool(bool(electrum.get('tls', True)))}",
        f"  TLS verify: {_format_bool(bool(electrum.get('tls_verify', True)))}",
        f"  SOCKS proxy: {socks_proxy or 'none'}",
    ]

    if electrum_connectivity_result:
        status, detail = _split_connectivity_result(electrum_connectivity_result)
        lines.append(f"  Connectivity test: {status}")
        if detail:
            label = "Server version" if status == "ok" else "Failure detail"
            lines.append(f"  {label}: {detail}")

    return lines


def tor_upstream_lines(config: dict[str, Any]) -> list[str]:
    tor_cfg = tor_config_from_app_config(config)

    if tor_cfg.enabled:
        return [
            "Tor Upstream: enabled",
            f"  Proxy: {tor_cfg.socks_proxy}",
            f"  Managed process: {_format_bool(tor_cfg.manage_process)}",
            f"  Test on startup: {_format_bool(tor_cfg.test_on_startup)}",
        ]

    return ["Tor Upstream: disabled"]


def tor_upstream_line(config: dict[str, Any]) -> str:
    return "\n".join(tor_upstream_lines(config))


def build_status_text(
    config: dict[str, Any],
    *,
    config_path: str | Path,
    ntfy_config: dict[str, Any] | None = None,
    conversation_ntfy_config: dict[str, Any] | None = None,
    subscription_count: int | None = None,
    conversation_started: bool | None = None,
    electrum_connectivity_result: str | None = None,
    tor_connectivity_result: str | None = None,
    include_useful_commands: bool = True,
) -> str:
    """
    Build the startup/status text shown in the terminal and Conversation Mode.

    tor_connectivity_result is kept as a backwards compatible alias for earlier
    call sites. New code should pass electrum_connectivity_result because the
    server.version result belongs to the Electrum/Fulcrum connection, not Tor.
    """
    if electrum_connectivity_result is None and tor_connectivity_result is not None:
        electrum_connectivity_result = tor_connectivity_result

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
    ]

    lines.extend(
        electrum_upstream_lines(
            config,
            electrum_connectivity_result=electrum_connectivity_result,
        )
    )
    lines.extend(tor_upstream_lines(config))

    lines.append(
        f"ntfy: {str(ntfy['server']).rstrip('/')}/{ntfy['topic']} "
        f"auth={(ntfy.get('auth') or {}).get('type', 'none')} "
        f"tls_verify={_format_bool(bool(ntfy.get('tls_verify', True)))}"
    )

    mempool_enabled = bool(mempool_config.get("enabled", False))
    mempool_base_url = str(mempool_config.get("base_url", "") or "")
    mempool_tls_verify = bool(mempool_config.get("tls_verify", True))
    mempool_enrich = bool(mempool_config.get("enrich_notifications", True))
    if mempool_enabled:
        lines.append(
            "Mempool API: enabled"
            + (f" ({mempool_base_url})" if mempool_base_url else "")
            + f" tls_verify={_format_bool(mempool_tls_verify)}"
            + f" enrich_notifications={_format_bool(mempool_enrich)}"
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
                "  wwg tor status",
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
