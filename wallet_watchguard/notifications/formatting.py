from __future__ import annotations

from typing import Any

from ..models import WalletEvent
from .models import NotificationMessage


def format_sats(value: int) -> str:
    return f"{value:,} sats"


def _strip_markdown(text: str) -> str:
    return text.replace("**", "").replace("`", "")


def _plain_lines_from_markdown(lines: list[str]) -> list[str]:
    return [_strip_markdown(line) for line in lines]


def format_tx_io_list(items: list[dict[str, Any]], *, mark_owned_outputs: bool = False) -> str:
    lines: list[str] = []
    for item in items:
        marker = "✅ " if mark_owned_outputs and item.get("owned") else ""
        address = item.get("address") or "unknown address"
        value_sats = int(item.get("value_sats") or 0)
        path = item.get("path")

        line = f"- {marker}`{address}` — `{value_sats:,} sats`"
        if path:
            line += f" ({path})"
        lines.append(line)

    return "\n".join(lines)


def format_wallet_activity_notification(
    event: WalletEvent,
    *,
    debug_latest: bool = False,
) -> NotificationMessage:
    if debug_latest:
        title = f"Wallet Watchguard debug: latest tx for {event.wallet_name}"
    elif event.event_type == "received":
        title = f"Bitcoin received: {event.wallet_name}"
    elif event.event_type == "sent":
        title = f"Bitcoin sent: {event.wallet_name}"
    elif event.event_type == "self_transfer":
        title = f"Bitcoin self-transfer: {event.wallet_name}"
    else:
        title = f"Bitcoin wallet activity: {event.wallet_name}"

    status = "unconfirmed/mempool" if event.status == "unconfirmed" else f"confirmed at height {event.height}"
    lines = []
    if debug_latest:
        lines.append("**Debug:** latest transaction notification test")
    lines.extend(
        [
            f"**Wallet:** {event.wallet_name}",
            f"**Type:** {event.event_type}",
            f"**Status:** {status}",
        ]
    )

    if event.amount_sats:
        sign = "+" if event.amount_sats > 0 else ""
        lines.append(f"**Amount:** `{sign}{event.amount_sats:,} sats`")
    if event.fee_sats is not None:
        lines.append(f"**Fee:** `{event.fee_sats:,} sats`")
    if event.vsize is not None:
        lines.append(f"**vsize:** `{event.vsize} vB`")
    if event.fee_rate_sat_vb is not None:
        lines.append(f"**Fee rate:** `{event.fee_rate_sat_vb:.2f} sat/vB`")

    if not event.tx_inputs and not event.tx_outputs and event.address:
        lines.append(f"**Address:** `{event.address}`")
    if event.tx_inputs:
        lines.append("**Inputs:**\n" + format_tx_io_list(event.tx_inputs))
    if event.tx_outputs:
        lines.append("**Outputs:**\n" + format_tx_io_list(event.tx_outputs, mark_owned_outputs=True))
    if event.path:
        lines.append(f"**Wallet path:** `{event.path}`")

    lines.append(f"**Tx:** `{event.txid}`")
    markdown_body = "\n\n".join(lines)
    plain_body = "\n\n".join(_plain_lines_from_markdown(lines))
    return NotificationMessage(title=title, markdown_body=markdown_body, plain_body=plain_body)


def format_autobalance_notification(
    rows: list[dict[str, int | str]],
    *,
    selection: str,
) -> NotificationMessage:
    total_confirmed = sum(int(row["confirmed_sats"]) for row in rows)
    total_unconfirmed = sum(int(row["unconfirmed_sats"]) for row in rows)
    total = total_confirmed + total_unconfirmed

    lines = [
        "**Autobalance summary**",
        f"**Scope:** {selection}",
    ]

    for row in rows:
        wallet_total = int(row["total_sats"])
        wallet_confirmed = int(row["confirmed_sats"])
        wallet_unconfirmed = int(row["unconfirmed_sats"])
        lines.append(
            f"- **{row['wallet_name']}:** `{format_sats(wallet_total)}` "
            f"confirmed=`{format_sats(wallet_confirmed)}` "
            f"unconfirmed=`{format_sats(wallet_unconfirmed)}`"
        )

    lines.extend(
        [
            f"**Total:** `{format_sats(total)}`",
            f"**Confirmed:** `{format_sats(total_confirmed)}`",
            f"**Unconfirmed:** `{format_sats(total_unconfirmed)}`",
        ]
    )

    return NotificationMessage(
        title="Wallet Watchguard: Autobalance",
        markdown_body="\n\n".join(lines),
        plain_body="\n\n".join(_plain_lines_from_markdown(lines)),
        tags="bitcoin,watch",
    )


def format_test_notification(provider: str = "ntfy") -> NotificationMessage:
    provider_label = provider.strip() or "notification provider"
    message = (
        "Bitcoin Wallet Watchguard successfully published this test notification "
        f"via {provider_label}.\n\n"
        "This is free and open source software made by a fellow bitcoiner.\n"
        "If you appreciate it, my Lightning address is xanny@cake.cash ⚡"
    )
    return NotificationMessage(
        title="⚡ Wallet Watchguard: Test Alert",
        markdown_body=message,
        plain_body=message,
        priority="default",
        tags="white_check_mark,bitcoin",
    )
