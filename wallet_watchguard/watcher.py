from __future__ import annotations

import asyncio
import os
import tomllib
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any

from .conversation import ConversationBridge, ConversationModeError
from .crypto import decrypt_xpub_with_passphrase, metadata_from_config, prompt_existing_passphrase
from .db import Database
from .derivation import derive_addresses
from .electrum import ElectrumClient
from .mempool import MempoolClient, mempool_fee_rate, mempool_tx_status
from .models import WalletEvent
from .ntfy import NtfyNotifier, decrypt_conversation_ntfy_config, decrypt_ntfy_config


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


class Watcher:
    def __init__(self, config: dict[str, Any], passphrase: str, *, config_path: str | Path | None = None) -> None:
        self.config = config
        self.passphrase = passphrase
        self.config_path = str(config_path) if config_path is not None else "config.yaml"
        self.db = Database(config["app"]["database_path"])
        electrum = config["electrum"]
        self.client = ElectrumClient(
            electrum["host"],
            int(electrum["port"]),
            use_tls=bool(electrum.get("tls", True)),
            tls_verify=electrum.get("tls_verify"),
            socks_proxy=electrum.get("socks_proxy"),
            timeout_seconds=int(electrum.get("timeout_seconds", 30)),
        )
        self.decrypted_ntfy_config = decrypt_ntfy_config(config["ntfy"], passphrase)
        self.notifier = NtfyNotifier(self.decrypted_ntfy_config)
        self.decrypted_conversation_ntfy_config = decrypt_conversation_ntfy_config(config, passphrase)
        self.conversation_notifier = NtfyNotifier(self.decrypted_conversation_ntfy_config)
        self.mempool = MempoolClient(config.get("mempool") or {})
        self._subscriptions_ready = asyncio.Event()
        self._subscription_count = 0
        self._conversation_started = False

    async def run(self) -> None:
        await self.db.connect()
        await self.client.connect()

        listener = asyncio.create_task(self.client.listen(self._handle_notification))
        conversation_task: asyncio.Task | None = None
        try:
            await self._derive_store_and_subscribe()
            self._subscriptions_ready.set()

            if bool((self.config.get("conversation") or {}).get("enabled", False)):
                bridge = ConversationBridge(
                    config=self.config,
                    passphrase=self.passphrase,
                    electrum_client=self.client,
                    notifier=self.conversation_notifier,
                )
                try:
                    await bridge.validate_or_raise()
                    conversation_task = asyncio.create_task(bridge.run())
                    self._conversation_started = True
                except ConversationModeError as exc:
                    self._conversation_started = False
                    print("", flush=True)
                    print(str(exc), flush=True)
                    print("Wallet Watchguard will continue monitoring wallets, but Conversation Mode is disabled.", flush=True)

            self._print_startup_summary()
            await asyncio.Event().wait()
        finally:
            if conversation_task is not None:
                conversation_task.cancel()
            listener.cancel()
            if conversation_task is not None:
                try:
                    await conversation_task
                except asyncio.CancelledError:
                    pass
            await self.client.close()
            await self.db.close()

    async def _derive_store_and_subscribe(self) -> None:
        helper_path = self.config["app"].get("derivation_helper_path", "./wwg-derive")
        default_lookahead = int(self.config["app"].get("lookahead", 100))

        for wallet in self.config["wallets"]:
            metadata = metadata_from_config(wallet["xpub_encryption"])
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
                self._subscription_count += 1

    def _print_startup_summary(self) -> None:
        electrum = self.config["electrum"]
        ntfy = self.config["ntfy"]
        mempool = self.config.get("mempool") or {}

        print("", flush=True)
        version = get_app_version()
        version_label = f"v{version}" if version != "unknown" and not version.startswith("v") else version

        print(f"Bitcoin Wallet Watchguard {version_label} by xannythepleb is running", flush=True)
        print("-------------------------------------", flush=True)
        print(f"Version: {version_label}", flush=True)
        print(f"Config: {self.config_path}", flush=True)
        print(f"Database: {self.config['app']['database_path']}", flush=True)
        print(
            "Electrum/Fulcrum: "
            f"{electrum['host']}:{electrum['port']} "
            f"tls={bool(electrum.get('tls', True))} "
            f"tls_verify={bool(electrum.get('tls_verify', True))}",
            flush=True,
        )
        print(
            f"ntfy: {ntfy['server'].rstrip('/')}/{ntfy['topic']} "
            f"auth={(ntfy.get('auth') or {}).get('type', 'none')} "
            f"tls_verify={bool(ntfy.get('tls_verify', True))}",
            flush=True,
        )
        print(
            f"Mempool API: {'enabled' if mempool.get('enabled') else 'disabled'}"
            + (f" ({mempool.get('base_url')})" if mempool.get("enabled") else ""),
            flush=True,
        )
        conversation = self.config.get("conversation") or {}
        requested = bool(conversation.get("enabled", False))
        conversation_topic = self.decrypted_conversation_ntfy_config.get("topic", ntfy["topic"])
        if self._conversation_started:
            conversation_status = "enabled"
            conversation_extra = f" on {self.decrypted_ntfy_config['server'].rstrip('/')}/{conversation_topic}"
        elif requested:
            conversation_status = "disabled"
            conversation_extra = " (requested, but not running; check permission/protection messages above)"
        else:
            conversation_status = "disabled"
            conversation_extra = ""
        print(f"Conversation Mode: {conversation_status}{conversation_extra}", flush=True)
        print(f"Subscribed scripts: {self._subscription_count}", flush=True)
        print("", flush=True)
        print("Wallets:", flush=True)
        for wallet in self.config.get("wallets", []):
            print(
                "  - "
                f"{wallet['name']} | {wallet['network']} | {wallet['wallet_type']} | "
                f"lookahead={wallet.get('lookahead', self.config['app'].get('lookahead', 100))}",
                flush=True,
            )
        print("", flush=True)
        print("Useful commands:", flush=True)
        print(f"  wwg test-ntfy --config {self.config_path}", flush=True)
        print(f"  wwg addresses --config {self.config_path} --limit 20", flush=True)
        print(f'  wwg addresses --config {self.config_path} --wallet "<wallet name>" --limit 20', flush=True)
        print(f"  wwg addresses --config {self.config_path} --all --include-change --limit 20", flush=True)
        if bool(mempool.get("enabled", False)):
            print(f"  wwg fees --config {self.config_path}", flush=True)
        if bool((self.config.get("conversation") or {}).get("enabled", False)):
            print("  ntfy conversation: send 'wwg help' to the protected topic", flush=True)
        print("", flush=True)

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
            event = await self._build_event(watched, txid, height)

            if await self.db.save_event(event):
                await self._notify_activity(event)

    async def _build_event(self, watched: Any, txid: str, electrum_height: int) -> WalletEvent:
        if not self.mempool.enabled:
            return WalletEvent(
                wallet_name=watched["wallet_name"],
                txid=txid,
                event_type="activity",
                amount_sats=0,
                status="unconfirmed" if electrum_height <= 0 else "confirmed",
                height=electrum_height,
                address=watched["address"],
                path=watched["path"],
            )

        try:
            tx = await self.mempool.get_tx(txid)
        except Exception:
            return WalletEvent(
                wallet_name=watched["wallet_name"],
                txid=txid,
                event_type="activity",
                amount_sats=0,
                status="unconfirmed" if electrum_height <= 0 else "confirmed",
                height=electrum_height,
                address=watched["address"],
                path=watched["path"],
            )

        wallet_scripts = await self.db.get_watched_scripts_for_wallet(watched["wallet_name"])
        owned_by_script = {row["script_pubkey"]: row for row in wallet_scripts}

        owned_input_sats = 0
        owned_output_sats = 0
        touched_address = watched["address"]
        touched_path = watched["path"]

        for vin in tx.get("vin") or []:
            prevout = vin.get("prevout") or {}
            script = prevout.get("scriptpubkey")
            if script in owned_by_script:
                owned_input_sats += int(prevout.get("value") or 0)
                touched_address = owned_by_script[script]["address"]
                touched_path = owned_by_script[script]["path"]

        for vout in tx.get("vout") or []:
            script = vout.get("scriptpubkey")
            if script in owned_by_script:
                owned_output_sats += int(vout.get("value") or 0)
                touched_address = owned_by_script[script]["address"]
                touched_path = owned_by_script[script]["path"]

        net_sats = owned_output_sats - owned_input_sats
        if net_sats > 0:
            event_type = "received"
        elif net_sats < 0:
            event_type = "sent"
        elif owned_input_sats or owned_output_sats:
            event_type = "self_transfer"
        else:
            event_type = "activity"

        status, height = mempool_tx_status(tx, electrum_height)
        return WalletEvent(
            wallet_name=watched["wallet_name"],
            txid=txid,
            event_type=event_type,
            amount_sats=net_sats,
            status=status,
            height=height,
            address=touched_address,
            path=touched_path,
            fee_sats=int(tx["fee"]) if tx.get("fee") is not None else None,
            vsize=int(tx["vsize"]) if tx.get("vsize") is not None else None,
            fee_rate_sat_vb=mempool_fee_rate(tx),
        )

    async def _notify_activity(self, event: WalletEvent) -> None:
        if event.event_type == "received":
            title = f"Bitcoin received: {event.wallet_name}"
        elif event.event_type == "sent":
            title = f"Bitcoin sent: {event.wallet_name}"
        elif event.event_type == "self_transfer":
            title = f"Bitcoin self-transfer: {event.wallet_name}"
        else:
            title = f"Bitcoin wallet activity: {event.wallet_name}"

        status = "unconfirmed/mempool" if event.status == "unconfirmed" else f"confirmed at height {event.height}"
        lines = [
            f"**Wallet:** {event.wallet_name}",
            f"**Type:** {event.event_type}",
            f"**Status:** {status}",
        ]

        if event.amount_sats:
            sign = "+" if event.amount_sats > 0 else ""
            lines.append(f"**Amount:** `{sign}{event.amount_sats:,} sats`")
        if event.fee_sats is not None:
            lines.append(f"**Fee:** `{event.fee_sats:,} sats`")
        if event.vsize is not None:
            lines.append(f"**vsize:** `{event.vsize} vB`")
        if event.fee_rate_sat_vb is not None:
            lines.append(f"**Fee rate:** `{event.fee_rate_sat_vb:.2f} sat/vB`")

        if event.address:
            lines.append(f"**Address:** `{event.address}`")
        if event.path:
            lines.append(f"**Path:** `{event.path}`")

        lines.append(f"**Tx:** `{event.txid}`")
        message = "\n\n".join(lines)
        await self.notifier.send(title, message)


def get_passphrase_from_env_or_prompt() -> str:
    value = os.environ.get("WWG_PASSPHRASE")
    if value is not None:
        value = value.strip()
        if not value:
            raise ValueError("WWG_PASSPHRASE must not be blank")
        return value

    return prompt_existing_passphrase()
