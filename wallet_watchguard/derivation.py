from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from .models import DerivedAddress


def electrum_scripthash(script_pubkey_hex: str) -> str:
    return hashlib.sha256(bytes.fromhex(script_pubkey_hex)).digest()[::-1].hex()


def derive_addresses(
    *,
    helper_path: str | Path,
    wallet_name: str,
    xpub: str,
    network: str,
    wallet_type: str,
    account_path: str,
    path_template: str,
    branch: int,
    start: int,
    end: int,
) -> list[DerivedAddress]:
    cmd = [
        str(helper_path),
        "derive",
        "--xpub",
        xpub,
        "--network",
        network,
        "--wallet-type",
        wallet_type,
        "--account-path",
        account_path,
        "--path-template",
        path_template,
        "--start",
        str(start),
        "--end",
        str(end),
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"Derivation helper failed: {proc.stderr.strip() or proc.stdout.strip()}")

    rows = json.loads(proc.stdout)
    out: list[DerivedAddress] = []

    for row in rows:
        out.append(
            DerivedAddress(
                wallet_name=wallet_name,
                network=network,
                wallet_type=wallet_type,
                branch=branch,
                index=int(row["index"]),
                path=row["path"],
                address=row["address"],
                script_pubkey=row["script_pubkey"],
                scripthash=electrum_scripthash(row["script_pubkey"]),
            )
        )

    return out
