from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from .config import DEFAULT_CONFIG_PATH, DEFAULT_DATABASE_PATH
from .conversation import ConversationBridge, ConversationModeError
from .crypto import decrypt_xpub_with_passphrase, metadata_from_config, prompt_existing_passphrase
from .db import Database
from .derivation import derive_addresses
from .electrum import ElectrumClient
from .mempool import MempoolClient, mempool_fee_rate, mempool_tx_status
from .models import DerivedAddress, WalletEvent
from .ntfy import NtfyNotifier, decrypt_conversation_ntfy_config, decrypt_ntfy_config
from .status import build_status_text, format_server_version
from .tor import TorUpstreamManager


class Watcher:
    def __init__(self, config: dict[str, Any], passphrase: str, *, config_path: str | Path | None = None) -> None:
        self.config = config
        self.passphrase = passphrase
        self.config_path = str(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
        self.tor_upstream = TorUpstreamManager(config)

        app_config = config.get("app") or {}
        database_path = app_config.get("database_path") or DEFAULT_DATABASE_PATH
        self.db = Database(database_path)
        
        self.client = self._make_electrum_client()
        self.decrypted_ntfy_config = decrypt_ntfy_config(config["ntfy"], passphrase)
        self.notifier = NtfyNotifier(self.decrypted_ntfy_config)
        # Conversation Mode credentials are decrypted lazily, only when
        # Conversation Mode actually starts (see run()). A broken or partial
        # separate conversation credential must not stop the wallet watcher
        # daemon from booting and monitoring wallets.
        self.decrypted_conversation_ntfy_config: dict[str, Any] | None = None
        self.conversation_notifier: NtfyNotifier | None = None
        self.mempool = MempoolClient(config.get("mempool") or {})
        self._subscriptions_ready = asyncio.Event()
        self._subscription_count = 0
        self._conversation_started = False
        self._tor_connectivity_result: str | None = None

    def _make_electrum_client(self) -> ElectrumClient:
        electrum = self.config["electrum"]
        return ElectrumClient(
            electrum["host"],
            int(electrum["port"]),
            use_tls=bool(electrum.get("tls", True)),
            tls_verify=electrum.get("tls_verify"),
            socks_proxy=electrum.get("socks_proxy"),
            timeout_seconds=int(electrum.get("timeout_seconds", 30)),
        )

    async def run(self) -> None:
        listener: asyncio.Task | None = None
        conversation_task: asyncio.Task | None = None
        autobalance_task: asyncio.Task | None = None

        await self.db.connect()
        try:
            await self.tor_upstream.start()
            await self.client.connect()
            listener = asyncio.create_task(self.client.listen(self._handle_notification))

            if self.tor_upstream.enabled and self.tor_upstream.config.test_on_startup:
                await self._test_tor_connectivity()

            await self._derive_store_and_subscribe()
            self._subscriptions_ready.set()

            if bool((self.config.get("conversation") or {}).get("enabled", False)):
                try:
                    self.decrypted_conversation_ntfy_config = decrypt_conversation_ntfy_config(
                        self.config, self.passphrase
                    )
                    self.conversation_notifier = NtfyNotifier(self.decrypted_conversation_ntfy_config)
                    bridge = ConversationBridge(
                        config=self.config,
                        passphrase=self.passphrase,
                        electrum_client=self.client,
                        notifier=self.conversation_notifier,
                        status_provider=self.status_text,
                    )
                    await bridge.validate_or_raise()
                    conversation_task = asyncio.create_task(bridge.run())
                    self._conversation_started = True
                except ConversationModeError as exc:
                    self._conversation_started = False
                    print("", flush=True)
                    print(str(exc), flush=True)
                    print("Wallet Watchguard will continue monitoring wallets, but Conversation Mode is disabled.", flush=True)
                except Exception as exc:
                    # A bad/partial conversation credential, missing config key, or
                    # similar should disable Conversation Mode, not take the whole
                    # daemon down. Wallet monitoring is the priority.
                    self._conversation_started = False
                    self.decrypted_conversation_ntfy_config = None
                    self.conversation_notifier = None
                    print("", flush=True)
                    print(f"Conversation Mode could not start: {exc}", flush=True)
                    print("Wallet Watchguard will continue monitoring wallets, but Conversation Mode is disabled.", flush=True)

            if bool((self.config.get("autobalance") or {}).get("enabled", False)):
                autobalance_task = asyncio.create_task(self._run_autobalance_loop())

            self._print_startup_summary()
            await asyncio.Event().wait()
        finally:
            if autobalance_task is not None:
                autobalance_task.cancel()
            if conversation_task is not None:
                conversation_task.cancel()
            if listener is not None:
                listener.cancel()
            if autobalance_task is not None:
                try:
                    await autobalance_task
                except asyncio.CancelledError:
                    pass
            if conversation_task is not None:
                try:
                    await conversation_task
                except asyncio.CancelledError:
                    pass
            await self.client.close()
            await self.db.close()
            await self.tor_upstream.stop()

    def _derive_wallet_scripts(self, wallet: dict[str, Any], *, lookahead: int | None = None) -> list[DerivedAddress]:
        helper_path = self.config["app"].get("derivation_helper_path", "./wwg-derive")
        default_lookahead = int(self.config["app"].get("lookahead", 100))
        lookahead = int(lookahead or wallet.get("lookahead", default_lookahead))

        metadata = metadata_from_config(wallet["xpub_encryption"])
        xpub = decrypt_xpub_with_passphrase(
            encrypted_xpub_b64=wallet["encrypted_xpub"],
            passphrase=self.passphrase,
            metadata=metadata,
        )

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

        return receive + change

    async def _derive_store_and_subscribe(self) -> None:
        for wallet in self.config["wallets"]:
            scripts = self._derive_wallet_scripts(wallet)
            await self.db.upsert_watched_scripts(scripts)

            for script in scripts:
                state_before = await self.db.get_scripthash_state(script.scripthash)
                initial_status = await self.client.call("blockchain.scripthash.subscribe", [script.scripthash])
                self._subscription_count += 1

                # Electrum returns the current status as the subscription response.
                # The old watcher ignored it and only reacted to later push
                # notifications, so transactions that happened while the daemon was
                # offline were silently missed. Process the initial status too, but
                # baseline first-run/imported history without spamming old alerts.
                has_existing_baseline = bool(state_before["last_status"] is not None or state_before["history_count"])
                await self._process_scripthash_update(
                    script.scripthash,
                    initial_status,
                    notify_new_items=has_existing_baseline,
                )

    async def _test_tor_connectivity(self) -> None:
        try:
            result = await self.client.call("server.version", ["wallet-watchguard", "1.4"])
            self._tor_connectivity_result = f"ok ({format_server_version(result)})"
        except Exception as exc:
            self._tor_connectivity_result = f"failed ({exc})"

    def status_text(self) -> str:
        return build_status_text(
            self.config,
            config_path=self.config_path,
            ntfy_config=self.decrypted_ntfy_config,
            conversation_ntfy_config=self.decrypted_conversation_ntfy_config,
            subscription_count=self._subscription_count,
            conversation_started=self._conversation_started,
            tor_connectivity_result=self._tor_connectivity_result,
        )

    def _print_startup_summary(self) -> None:
        print(self.status_text(), flush=True)

    async def _handle_notification(self, message: dict[str, Any]) -> None:
        if message.get("method") != "blockchain.scripthash.subscribe":
            return

        scripthash, status = message.get("params", [None, None])
        if not scripthash:
            return

        await self._process_scripthash_update(scripthash, status, notify_new_items=True)

    async def _process_scripthash_update(
        self,
        scripthash: str,
        status: str | None,
        *,
        notify_new_items: bool,
    ) -> int:
        """Synchronise one scripthash and optionally notify for new tx history.

        `blockchain.scripthash.subscribe` has two paths that matter here:

        * the immediate subscription response containing the current status;
        * later push notifications containing changed statuses.

        Both need to update the local status/history database. Whether the new
        history entries should notify is decided by the caller so startup can
        baseline a newly imported wallet quietly, while normal live changes and
        missed changes from an already-baselined wallet still alert.
        """
        changed = await self.db.set_scripthash_status(scripthash, status)
        if not changed and not notify_new_items:
            return 0

        watched = await self.db.get_watched_script(scripthash)
        if not watched:
            return 0

        if status is None:
            return 0

        history = await self.client.call("blockchain.scripthash.get_history", [scripthash])
        new_items = await self.db.remember_history(scripthash, history)

        if not notify_new_items:
            return len(new_items)

        for item in new_items:
            txid = item["tx_hash"]
            height = int(item.get("height", 0))
            event = await self._build_event(watched, txid, height)
            await self._record_and_notify_event(event)

        return len(new_items)

    def _should_notify_event(self, event: WalletEvent) -> bool:
        app_config = self.config.get("app") or {}
        if event.status == "unconfirmed" and not bool(app_config.get("notify_on_mempool", True)):
            return False
        if event.status == "confirmed" and not bool(app_config.get("notify_on_confirmed", True)):
            return False
        return True

    async def _record_and_notify_event(self, event: WalletEvent) -> bool:
        if not await self.db.save_event(event):
            return False
        if self._should_notify_event(event):
            await self._notify_activity(event)
        return True

    @staticmethod
    def _fallback_event(watched: Any, txid: str, electrum_height: int) -> WalletEvent:
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

    async def _build_event(self, watched: Any, txid: str, electrum_height: int) -> WalletEvent:
        if not self.mempool.enabled:
            return self._fallback_event(watched, txid, electrum_height)

        try:
            tx = await self.mempool.get_tx(txid)
            return await self._build_enriched_event(watched, txid, electrum_height, tx)
        except Exception as exc:
            print(f"Mempool enrichment failed for {txid}; sending generic wallet activity notification: {exc}", flush=True)
            return self._fallback_event(watched, txid, electrum_height)

    async def _build_enriched_event(
        self,
        watched: Any,
        txid: str,
        electrum_height: int,
        tx: dict[str, Any],
    ) -> WalletEvent:
        wallet_scripts = await self.db.get_watched_scripts_for_wallet(watched["wallet_name"])
        owned_by_script = {row["script_pubkey"]: row for row in wallet_scripts}

        owned_input_sats = 0
        owned_output_sats = 0
        touched_address = watched["address"]
        touched_path = watched["path"]
        tx_inputs: list[dict[str, Any]] = []
        tx_outputs: list[dict[str, Any]] = []

        for position, vin in enumerate(tx.get("vin") or []):
            prevout = vin.get("prevout") or {}
            script = prevout.get("scriptpubkey")
            owned = script in owned_by_script
            if owned:
                owned_input_sats += int(prevout.get("value") or 0)
                touched_address = owned_by_script[script]["address"]
                touched_path = owned_by_script[script]["path"]

            tx_inputs.append(
                {
                    "index": position,
                    "address": prevout.get("scriptpubkey_address"),
                    "value_sats": int(prevout.get("value") or 0),
                    "owned": owned,
                    "path": owned_by_script[script]["path"] if owned else None,
                    "txid": vin.get("txid"),
                    "vout": vin.get("vout"),
                }
            )

        for position, vout in enumerate(tx.get("vout") or []):
            script = vout.get("scriptpubkey")
            owned = script in owned_by_script
            if owned:
                owned_output_sats += int(vout.get("value") or 0)
                touched_address = owned_by_script[script]["address"]
                touched_path = owned_by_script[script]["path"]

            tx_outputs.append(
                {
                    "index": position,
                    "address": vout.get("scriptpubkey_address"),
                    "value_sats": int(vout.get("value") or 0),
                    "owned": owned,
                    "path": owned_by_script[script]["path"] if owned else None,
                }
            )

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
            tx_inputs=tx_inputs,
            tx_outputs=tx_outputs,
        )

    @staticmethod
    def _history_sort_key(item: dict[str, Any]) -> tuple[int, int]:
        height = int(item.get("height", 0) or 0)
        # Unconfirmed transactions are the newest useful debug signal. Otherwise
        # the highest confirmed block height is the best Electrum history gives us
        # without requiring an external block-time lookup for every transaction.
        if height <= 0:
            return (1, 0)
        return (0, height)

    @staticmethod
    def _format_tx_io_list(items: list[dict[str, Any]], *, mark_owned_outputs: bool = False) -> str:
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

    async def notify_latest_transaction_for_wallet(
        self,
        wallet: dict[str, Any],
        *,
        scan_limit: int | None = None,
        debug_logger: Any | None = None,
    ) -> WalletEvent:
        """Send a real ntfy notification for the latest tx seen in one wallet.

        This deliberately uses the same derivation, Electrum/Fulcrum history,
        optional Mempool enrichment, and ntfy formatting as the daemon path. It is
        a one shot diagnostic path and does not save a wallet_event row, so it
        cannot mask a later live notification.
        """

        async def ignore_notifications(message: dict[str, Any]) -> None:
            if debug_logger is not None:
                debug_logger(f"ignored Electrum notification during latest-tx debug: {message}")

        def debug(message: str) -> None:
            if debug_logger is not None:
                debug_logger(message)

        listener_task: asyncio.Task | None = None
        await self.db.connect()
        try:
            await self.tor_upstream.start()
            await self.client.connect()
            listener_task = asyncio.create_task(self.client.listen(ignore_notifications))

            lookahead = int(scan_limit or wallet.get("lookahead") or self.config["app"].get("lookahead", 100))
            scripts = self._derive_wallet_scripts(wallet, lookahead=lookahead)
            await self.db.upsert_watched_scripts(scripts)
            debug(f"derived {len(scripts)} receive/change script(s) for wallet {wallet['name']!r}")

            latest: tuple[tuple[int, int], dict[str, Any], Any] | None = None
            for script in scripts:
                history = await self.client.call("blockchain.scripthash.get_history", [script.scripthash])
                debug(f"{script.path} {script.address}: {len(history)} history entr{'y' if len(history) == 1 else 'ies'}")
                if not history:
                    continue

                watched = await self.db.get_watched_script(script.scripthash)
                if watched is None:
                    continue

                for item in history:
                    candidate = (self._history_sort_key(item), item, watched)
                    if latest is None or candidate[0] > latest[0]:
                        latest = candidate

            if latest is None:
                raise ValueError(
                    f"No transaction history found for wallet {wallet['name']!r} within lookahead {lookahead}."
                )

            _, latest_item, watched = latest
            txid = latest_item["tx_hash"]
            height = int(latest_item.get("height", 0) or 0)
            debug(f"latest transaction selected: txid={txid} height={height}")

            event = await self._build_event(watched, txid, height)
            await self._notify_activity(event, debug_latest=True)
            return event
        finally:
            if listener_task is not None:
                listener_task.cancel()
                try:
                    await listener_task
                except asyncio.CancelledError:
                    pass
            await self.client.close()
            await self.db.close()
            await self.tor_upstream.stop()

    def _autobalance_wallets(self) -> list[dict[str, Any]]:
        autobalance = self.config.get("autobalance") or {}
        wallets = list(self.config.get("wallets") or [])

        if bool(autobalance.get("all_wallets", True)):
            return wallets

        selected_names = {str(name) for name in autobalance.get("wallets") or []}
        return [wallet for wallet in wallets if str(wallet.get("name") or "") in selected_names]

    async def _wallet_balance(self, wallet: dict[str, Any], client: ElectrumClient) -> dict[str, int | str]:
        confirmed = 0
        unconfirmed = 0

        for script in self._derive_wallet_scripts(wallet):
            balance = await client.call("blockchain.scripthash.get_balance", [script.scripthash])
            confirmed += int(balance.get("confirmed") or 0)
            unconfirmed += int(balance.get("unconfirmed") or 0)

        return {
            "wallet_name": str(wallet["name"]),
            "confirmed_sats": confirmed,
            "unconfirmed_sats": unconfirmed,
            "total_sats": confirmed + unconfirmed,
        }

    @staticmethod
    def _format_sats(value: int) -> str:
        return f"{value:,} sats"

    async def _send_autobalance_notification(self) -> None:
        wallets = self._autobalance_wallets()
        if not wallets:
            raise ValueError("Autobalance is enabled but no configured wallets matched the Autobalance wallet selection")

        async def ignore_notifications(message: dict[str, Any]) -> None:
            _ = message

        client = self._make_electrum_client()
        listener_task: asyncio.Task | None = None
        try:
            await client.connect()
            listener_task = asyncio.create_task(client.listen(ignore_notifications))
            rows = [await self._wallet_balance(wallet, client) for wallet in wallets]
        finally:
            if listener_task is not None:
                listener_task.cancel()
                try:
                    await listener_task
                except asyncio.CancelledError:
                    pass
            await client.close()

        total_confirmed = sum(int(row["confirmed_sats"]) for row in rows)
        total_unconfirmed = sum(int(row["unconfirmed_sats"]) for row in rows)
        total = total_confirmed + total_unconfirmed

        autobalance = self.config.get("autobalance") or {}
        selection = "all wallets combined" if bool(autobalance.get("all_wallets", True)) else "selected wallets"

        lines = [
            "**Autobalance summary**",
            f"**Scope:** {selection}",
        ]

        for row in rows:
            wallet_total = int(row["total_sats"])
            wallet_confirmed = int(row["confirmed_sats"])
            wallet_unconfirmed = int(row["unconfirmed_sats"])
            lines.append(
                f"- **{row['wallet_name']}:** `{self._format_sats(wallet_total)}` "
                f"confirmed=`{self._format_sats(wallet_confirmed)}` "
                f"unconfirmed=`{self._format_sats(wallet_unconfirmed)}`"
            )

        lines.extend(
            [
                f"**Total:** `{self._format_sats(total)}`",
                f"**Confirmed:** `{self._format_sats(total_confirmed)}`",
                f"**Unconfirmed:** `{self._format_sats(total_unconfirmed)}`",
            ]
        )

        await self.notifier.send(
            "Wallet Watchguard: Autobalance",
            "\n\n".join(lines),
            tags="bitcoin,watch",
        )

    async def _run_autobalance_loop(self) -> None:
        autobalance = self.config.get("autobalance") or {}
        interval_hours = max(1, int(autobalance.get("interval_hours", 12)))
        interval_seconds = interval_hours * 60 * 60

        while True:
            try:
                await self._send_autobalance_notification()
            except Exception as exc:
                print(f"Autobalance notification failed: {exc}", flush=True)
            await asyncio.sleep(interval_seconds)

    async def _notify_activity(self, event: WalletEvent, *, debug_latest: bool = False) -> None:
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
            lines.append("**Inputs:**\n" + self._format_tx_io_list(event.tx_inputs))
        if event.tx_outputs:
            lines.append("**Outputs:**\n" + self._format_tx_io_list(event.tx_outputs, mark_owned_outputs=True))
        if event.path:
            lines.append(f"**Wallet path:** `{event.path}`")

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