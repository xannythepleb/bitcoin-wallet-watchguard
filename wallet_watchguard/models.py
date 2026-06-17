from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

WalletType = Literal["taproot", "native_segwit", "nested_segwit", "legacy"]
Network = Literal["bitcoin", "testnet", "signet", "regtest"]


@dataclass(frozen=True)
class DerivedAddress:
    wallet_name: str
    network: str
    wallet_type: str
    branch: int
    index: int
    path: str
    address: str
    script_pubkey: str
    scripthash: str


@dataclass(frozen=True)
class WalletEvent:
    wallet_name: str
    txid: str
    event_type: str
    amount_sats: int
    status: str
    height: int
    address: str | None = None
    path: str | None = None
    fee_sats: int | None = None
