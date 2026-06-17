from __future__ import annotations

from typing import Any

import httpx


class MempoolClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.enabled = bool(config.get("enabled", False))
        self.base_url = str(config.get("base_url", "")).rstrip("/")
        self.tls_verify = bool(config.get("tls_verify", True))
        self.timeout_seconds = int(config.get("timeout_seconds", 15))

    async def get_tx(self, txid: str) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("Mempool integration is disabled")
        if not self.base_url:
            raise RuntimeError("Mempool base_url is not configured")

        async with httpx.AsyncClient(timeout=self.timeout_seconds, verify=self.tls_verify) as client:
            response = await client.get(f"{self.base_url}/tx/{txid}")
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise RuntimeError("Unexpected Mempool transaction response")
            return data

    async def get_recommended_fees(self) -> dict[str, Any]:
        """
        Fetch fee recommendations from a local/self-hosted Mempool instance.

        For a base_url like https://mempool.local/api, this calls:
          /v1/fees/recommended

        Typical response keys are:
          fastestFee, halfHourFee, hourFee, economyFee, minimumFee
        """
        if not self.enabled:
            raise RuntimeError("Mempool integration is disabled. Run `wwg init --add mempool` and enable it first.")
        if not self.base_url:
            raise RuntimeError("Mempool base_url is not configured. Run `wwg init --add mempool` first.")

        async with httpx.AsyncClient(timeout=self.timeout_seconds, verify=self.tls_verify) as client:
            response = await client.get(f"{self.base_url}/v1/fees/recommended")
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise RuntimeError("Unexpected Mempool fee recommendation response")
            return data


def mempool_fee_value(fees: dict[str, Any], key: str) -> int | float | None:
    value = fees.get(key)
    try:
        if value is None:
            return None
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric.is_integer():
        return int(numeric)
    return numeric


def mempool_priority_fees(fees: dict[str, Any]) -> dict[str, int | float | None]:
    """Return the three user-facing priority buckets used by Wallet Watchguard.

    Mempool's recommended-fees endpoint exposes several horizons. We map them
    to the simple priority names users expect:
      low    -> hourFee
      medium -> halfHourFee
      high   -> fastestFee
    """
    return {
        "low": mempool_fee_value(fees, "hourFee"),
        "medium": mempool_fee_value(fees, "halfHourFee"),
        "high": mempool_fee_value(fees, "fastestFee"),
    }


def format_fee_value(value: int | float | None) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, int):
        return f"{value} sat/vB"
    return f"{value:.2f} sat/vB"


def format_mempool_fee_summary(fees: dict[str, Any]) -> str:
    priorities = mempool_priority_fees(fees)
    lines = [
        "Bitcoin fee recommendations from local Mempool",
        "",
        f"Low priority:    {format_fee_value(priorities['low'])}",
        f"Medium priority: {format_fee_value(priorities['medium'])}",
        f"High priority:   {format_fee_value(priorities['high'])}",
    ]

    economy = mempool_fee_value(fees, "economyFee")
    minimum = mempool_fee_value(fees, "minimumFee")
    if economy is not None or minimum is not None:
        lines.extend(
            [
                "",
                f"Economy:         {format_fee_value(economy)}",
                f"Minimum:         {format_fee_value(minimum)}",
            ]
        )

    return "\n".join(lines)


def mempool_tx_status(tx: dict[str, Any], electrum_height: int) -> tuple[str, int]:
    status = tx.get("status") or {}
    confirmed = bool(status.get("confirmed", False))
    height = int(status.get("block_height") or electrum_height or 0)
    return ("confirmed" if confirmed or height > 0 else "unconfirmed", height)


def mempool_fee_rate(tx: dict[str, Any]) -> float | None:
    fee = tx.get("fee")
    vsize = tx.get("vsize")
    try:
        if fee is None or vsize in (None, 0):
            return None
        return float(fee) / float(vsize)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
