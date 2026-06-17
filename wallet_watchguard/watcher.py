from __future__ import annotations

import asyncio
import os
from typing import Any

from .crypto import (
    XpubEncryptionMetadata,
    decrypt_xpub_with_passphrase,
    prompt_existing_passphrase,
)
from .db import Database
from .derivation import derive_addresses
from .electrum import ElectrumClient
from .models import WalletEvent
from .ntfy import NtfyNotifier


class Watcher:
    def __init__(self, config: dict[str, Any], passphrase: str) -> None:
        if not passphrase:
            raise ValueError("Wallet Watchguard passphrase must not be blank")

        self.config = config
        self.passphrase = passphrase
        self.db = Database(config["app"]["database_path"])
        electrum = config["electrum"]
        self.client = ElectrumClient(
            electrum["host"],
            int(electrum["port"]),
            use_tls=bool(electrum.get("tls", False)),
            socks_proxy=electrum.get("socks_proxy"),
            timeout_seconds=int(electrum.get("timeout_seconds", 30)),
        )
        self.notifier = NtfyNotifier(config["ntfy"])
        self._subscriptions_ready = asyncio.Event()

    async def run(self) -> None:
        await self.db.connect()
        await self.client.connect()

        listener = asyncio.create_task(self.client.listen(self._handle_notification))
        try:
            await self._derive_store_and_subscribe()
            self._subscriptions_ready.set()
            await asyncio.Event().wait()
        finally:
            listener.cancel()
            await self.client.close()
            await self.db.close()

    async def _derive_store_and_subscribe(self) -> None:
        helper_path = self.config["app"].get("derivation_helper_path", "./wwg-derive")
        default_lookahead = int(self.config["app"].get("lookahead", 100))

        for wallet in self.config["wallets"]:
            encryption = wallet["xpub_encryption"]
            metadata = XpubEncryptionMetadata(
                scheme=encryption["scheme"],
                kdf=encryption["kdf"],
                opslimit=int(encryption["opslimit"]),
                memlimit=int(encryption["memlimit"]),
                salt_b64=encryption["salt"],
            )
            xpub = decrypt_xpub_with_passphrase(
                encrypted_xpub_b64=wallet["encrypted_xpub"],
                passphrase=self.passphrase,
                metadata=metadata,
            )
            lookahead = int(wallet.get("lookahead", default_lookahead))

            receive = derive_addresses(
                helper_path=helper_path,
                wallet_name=wallet["name"],
                xpub=xpub,
                network=wallet["network"],
                wallet_type=wallet["wallet_type"],
                account_path=wallet["account_path"],
                path_template=wallet.get("receive_path_template", "0/*"),
                branch=0,
                start=0,
                end=lookahead - 1,
            )
            change = derive_addresses(
                helper_path=helper_path,
                wallet_name=wallet["name"],
                xpub=xpub,
                network=wallet["network"],
                wallet_type=wallet["wallet_type"],
                account_path=wallet["account_path"],
                path_template=wallet.get("change_path_template", "1/*"),
                branch=1,
                start=0,
                end=lookahead - 1,
            )

            scripts = receive + change
            await self.db.upsert_watched_scripts(scripts)

            for script in scripts:
                await self.client.call("blockchain.scripthash.subscribe", [script.scripthash])

    async def _handle_notification(self, message: dict[str, Any]) -> None:
        if message.get("method") != "blockchain.scripthash.subscribe":
            return

        scripthash, status = message.get("params", [None, None])
        if not scripthash:
            return

        changed = await self.db.set_scripthash_status(scripthash, status)
        if not changed:
            return

        watched = await self.db.get_watched_script(scripthash)
        if not watched:
            return

        history = await self.client.call("blockchain.scripthash.get_history", [scripthash])
        new_items = await self.db.remember_history(scripthash, history)

        for item in new_items:
            txid = item["tx_hash"]
            height = int(item.get("height", 0))
            event = WalletEvent(
                wallet_name=watched["wallet_name"],
                txid=txid,
                event_type="activity",
                amount_sats=0,
                status="unconfirmed" if height <= 0 else "confirmed",
                height=height,
                address=watched["address"],
                path=watched["path"],
            )

            if await self.db.save_event(event):
                await self._notify_activity(event)

    async def _notify_activity(self, event: WalletEvent) -> None:
        title = f"Bitcoin wallet activity: {event.wallet_name}"
        status = "unconfirmed/mempool" if event.status == "unconfirmed" else f"confirmed at height {event.height}"
        message = (
            f"**Wallet:** {event.wallet_name}\n\n"
            f"**Status:** {status}\n\n"
            f"**Address:** `{event.address}`\n\n"
            f"**Path:** `{event.path}`\n\n"
            f"**Tx:** `{event.txid}`"
        )
        await self.notifier.send(title, message)


def get_passphrase_from_env_or_prompt() -> str:
    passphrase = os.environ.get("WWG_PASSPHRASE")

    if passphrase is not None:
        if not passphrase:
            raise ValueError("WWG_PASSPHRASE must not be blank")
        return passphrase

    return prompt_existing_passphrase()
