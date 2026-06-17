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
