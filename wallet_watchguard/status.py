from __future__ import annotations

import tomllib
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any

from .config import DEFAULT_CONFIG_PATH, DEFAULT_DATABASE_PATH, notification_providers_config
from .nostr import nostr_helper_availability
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


def _label(text: str, emoji: str, *, use_emoji: bool) -> str:
    # Use two spaces after emoji labels, looks bad otherwise
    return f"{emoji}  {text}" if use_emoji else text


def _status_use_emoji(config: dict[str, Any], use_emoji: bool | None) -> bool:
    if use_emoji is not None:
        return bool(use_emoji)
    app_config = config.get("app") or {}
    return bool(app_config.get("_display_emoji", True))


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


def _autobalance_selection_text(config: dict[str, Any]) -> str:
    autobalance = config.get("autobalance") or {}
    if bool(autobalance.get("all_wallets", True)):
        return "all wallets combined"

    wallet_names = [str(name) for name in autobalance.get("wallets") or [] if str(name).strip()]
    if not wallet_names:
        return "no wallets selected"

    return ", ".join(wallet_names)


def autobalance_status_line(config: dict[str, Any], *, use_emoji: bool = True) -> str:
    autobalance = config.get("autobalance") or {}
    enabled = bool(autobalance.get("enabled", False))
    interval_hours = int(autobalance.get("interval_hours", 12))
    selection = _autobalance_selection_text(config)

    if enabled:
        return _label("Autobalance: enabled", "🚨", use_emoji=use_emoji) + f" every {interval_hours}h; wallets={selection}"

    return _label("Autobalance: disabled", "🚨", use_emoji=use_emoji) + f"; wallets={selection}; interval={interval_hours}h"


def electrum_upstream_lines(
    config: dict[str, Any],
    *,
    electrum_connectivity_result: str | None = None,
    use_emoji: bool = True,
) -> list[str]:
    electrum = config["electrum"]
    socks_proxy = _effective_socks_proxy(config)

    lines = [
        _label("Electrum/Fulcrum:", "📡", use_emoji=use_emoji),
        f"   - Server: {electrum['host']}:{electrum['port']}",
        f"   - TLS: {_format_bool(bool(electrum.get('tls', True)))}",
        f"   - TLS verify: {_format_bool(bool(electrum.get('tls_verify', True)))}",
        f"   - SOCKS proxy: {socks_proxy or 'none'}",
    ]

    if electrum_connectivity_result:
        status, detail = _split_connectivity_result(electrum_connectivity_result)
        lines.append(f"  Connectivity test: {status}")
        if detail:
            label = "Server version" if status == "ok" else "Failure detail"
            lines.append(f"  {label}: {detail}")

    return lines


def tor_upstream_lines(config: dict[str, Any], *, use_emoji: bool = True) -> list[str]:
    tor_cfg = tor_config_from_app_config(config)

    if tor_cfg.enabled:
        return [
            _label("Tor Upstream: enabled", "🌐", use_emoji=use_emoji),
            f"   - Proxy: {tor_cfg.socks_proxy}",
            f"   - Managed process: {_format_bool(tor_cfg.manage_process)}",
            f"   - Test on startup: {_format_bool(tor_cfg.test_on_startup)}",
        ]

    return [_label("Tor Upstream: disabled", "🌐", use_emoji=use_emoji)]


def tor_upstream_line(config: dict[str, Any], *, use_emoji: bool = True) -> str:
    return "\n".join(tor_upstream_lines(config, use_emoji=use_emoji))


def _count_nostr_recipients(recipients: Any) -> int:
    return len(recipients) if isinstance(recipients, list) else 0




def _nostr_sender_npub(nostr_config: dict[str, Any]) -> str:
    sender = nostr_config.get("sender") or {}
    if not isinstance(sender, dict):
        return "not configured"

    npub = str(sender.get("npub") or "").strip()
    return npub or "not configured"


def _count_nostr_relays(nostr_config: dict[str, Any]) -> int:
    relay_urls: set[str] = set()

    relays = nostr_config.get("relays") or []
    if isinstance(relays, list):
        relay_urls.update(str(relay).strip() for relay in relays if str(relay).strip())

    recipients = nostr_config.get("recipients") or []
    if isinstance(recipients, list):
        for recipient in recipients:
            if isinstance(recipient, dict):
                recipient_relays = recipient.get("relays") or []
                if isinstance(recipient_relays, list):
                    relay_urls.update(str(relay).strip() for relay in recipient_relays if str(relay).strip())

    return len(relay_urls)


def notification_status_lines(
    config: dict[str, Any],
    *,
    ntfy_config: dict[str, Any] | None = None,
    use_emoji: bool = True,
) -> list[str]:
    providers = notification_providers_config(config)
    ntfy = ntfy_config or config["ntfy"]
    ntfy_provider = providers.get("ntfy") or {}
    nostr_provider = providers.get("nostr") or {}

    lines = [_label("Notifications:", "🔔", use_emoji=use_emoji)]

    if bool(ntfy_provider.get("enabled", True)):
        lines.append(
            f"   - ntfy: enabled {str(ntfy['server']).rstrip('/')}/{ntfy['topic']} "
            f"auth={(ntfy.get('auth') or {}).get('type', 'none')} "
            f"tls_verify={_format_bool(bool(ntfy.get('tls_verify', True)))}"
        )
    else:
        lines.append("   - ntfy: disabled")

    nostr_enabled = bool(nostr_provider.get("enabled", False))
    nostr_status = "enabled" if nostr_enabled else "disabled"
    nostr_recipients = _count_nostr_recipients(nostr_provider.get("recipients") or [])
    nostr_relays = _count_nostr_relays(nostr_provider)
    nostr_sender_npub = _nostr_sender_npub(nostr_provider)
    nostr_helper = nostr_helper_availability(nostr_provider)
    lines.append(
        f"   - Nostr: {nostr_status} recipients={nostr_recipients} "
        f"relays={nostr_relays} WWG npub={nostr_sender_npub} "
        f"helper={nostr_helper.status_text}"
    )

    return lines


def build_status_text(
    config: dict[str, Any],
    *,
    config_path: str | Path | None = None,
    ntfy_config: dict[str, Any] | None = None,
    conversation_ntfy_config: dict[str, Any] | None = None,
    subscription_count: int | None = None,
    conversation_started: bool | None = None,
    electrum_connectivity_result: str | None = None,
    tor_connectivity_result: str | None = None,
    include_useful_commands: bool = True,
    use_emoji: bool | None = None,
) -> str:
    """
    Build the startup/status text shown in the terminal and Conversation Mode.

    tor_connectivity_result is kept as a backwards compatible alias for earlier
    call sites. New code should pass electrum_connectivity_result because the
    server.version result belongs to the Electrum/Fulcrum connection, not Tor.
    """
    if electrum_connectivity_result is None and tor_connectivity_result is not None:
        electrum_connectivity_result = tor_connectivity_result

    use_emoji = _status_use_emoji(config, use_emoji)

    app_config = config.get("app") or {}
    ntfy = ntfy_config or config["ntfy"]
    mempool_config = config.get("mempool") or {}
    conversation = config.get("conversation") or {}

    version = get_app_version()
    version_label = f"v{version}" if version != "unknown" and not version.startswith("v") else version

    lightning_contact = "⚡ xanny@cake.cash" if use_emoji else "xanny@cake.cash"

    display_config_path = str(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
    display_database_path = app_config.get("database_path") or DEFAULT_DATABASE_PATH

    lines: list[str] = [
        "",
        f"Bitcoin Wallet Watchguard {version_label} by xannythepleb ({lightning_contact}) is running",
        "-------------------------------------",
        f"{_label('Version:', '📦', use_emoji=use_emoji)} {version_label}",
        f"{_label('Config:', '⚙️ ', use_emoji=use_emoji)} {display_config_path}",
        f"{_label('Database:', '💾', use_emoji=use_emoji)} {display_database_path}",
    ]

    lines.extend(
        electrum_upstream_lines(
            config,
            electrum_connectivity_result=electrum_connectivity_result,
            use_emoji=use_emoji,
        )
    )
    lines.extend(tor_upstream_lines(config, use_emoji=use_emoji))

    lines.extend(notification_status_lines(config, ntfy_config=ntfy, use_emoji=use_emoji))

    mempool_enabled = bool(mempool_config.get("enabled", False))
    mempool_base_url = str(mempool_config.get("base_url", "") or "")
    mempool_tls_verify = bool(mempool_config.get("tls_verify", True))
    mempool_enrich = bool(mempool_config.get("enrich_notifications", True))
    if mempool_enabled:
        lines.append(
            _label("Mempool API: enabled", "🌊", use_emoji=use_emoji)
            + (f" ({mempool_base_url})" if mempool_base_url else "")
            + f" tls_verify={_format_bool(mempool_tls_verify)}"
            + f" enrich_notifications={_format_bool(mempool_enrich)}"
        )
    else:
        lines.append(_label("Mempool API: disabled", "🌊", use_emoji=use_emoji))

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
    lines.append(f"{_label('Conversation Mode:', '🗣️ ', use_emoji=use_emoji)} {conversation_status}{conversation_extra}")
    lines.append(autobalance_status_line(config, use_emoji=use_emoji))

    if subscription_count is None:
        lines.append(_label("Subscribed scripts: not connected in this status command", "📜", use_emoji=use_emoji))
    else:
        lines.append(f"{_label('Subscribed scripts:', '📜', use_emoji=use_emoji)} {subscription_count}")

    lines.extend(["", _label("Wallets:", "💵", use_emoji=use_emoji)])
    for wallet in config.get("wallets", []):
        lines.append(
            "  - "
            f"{wallet['name']} | {wallet['network']} | {wallet['wallet_type']} | "
            f"lookahead={wallet.get('lookahead', app_config.get('lookahead', 100))}"
        )

    if include_useful_commands:
        lines.extend(
            [
                "",
                _label("Useful commands:", "🚀", use_emoji=use_emoji),
                "  wwg status",
                "  wwg wallets",
                "  wwg balance",
                "  wwg next address",
                "  wwg next 3",
                "  wwg addresses --limit 20",
                "  wwg addresses --wallet \"<wallet name>\" --limit 20",
                "  wwg addresses --all --include-change --limit 20",
                "  wwg test-ntfy",
                "  wwg test-tor",
                "  wwg nostr status",
                "  wwg tor status",
                "  wwg init --add <section>",
            ]
        )
        if mempool_enabled:
            lines.append("  wwg fees")
        if bool(conversation.get("enabled", False)):
            lines.append("  ntfy conversation: send 'wwg help' to the protected topic")

    lines.append("")
    return "\n".join(lines)
