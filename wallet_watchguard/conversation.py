from __future__ import annotations

import argparse
import asyncio
import re
import shlex
import time
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any

from .crypto import decrypt_xpub_with_passphrase, metadata_from_config
from .derivation import derive_addresses
from .electrum import ElectrumClient
from .mempool import MempoolClient, format_mempool_fee_summary
from .models import DerivedAddress
from .ntfy import NtfyNotifier


class ConversationModeError(RuntimeError):
    pass


@dataclass(frozen=True)
class AddressRow:
    branch_label: str
    path: str
    status: str
    confirmed: int
    unconfirmed: int
    address: str


class ConversationBridge:
    def __init__(
        self,
        *,
        config: dict[str, Any],
        passphrase: str,
        electrum_client: ElectrumClient,
        notifier: NtfyNotifier,
        status_provider: Callable[[], str] | None = None,
    ) -> None:
        self.config = config
        self.passphrase = passphrase
        self.electrum = electrum_client
        self.notifier = notifier
        self.status_provider = status_provider
        self.settings = config.get("conversation") or {}
        self.command_prefix = str(self.settings.get("command_prefix", "wwg")).strip()
        self.max_addresses = int(self.settings.get("max_addresses_per_response", 10))
        self.max_response_chars = int(self.settings.get("max_response_chars", 3900))
        self.probe_anonymous_write = bool(self.settings.get("probe_anonymous_write", True))
        self._command_lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("enabled", False))

    async def validate_or_raise(self) -> None:
        check = await self.notifier.check_conversation_security(probe_anonymous_write=self.probe_anonymous_write)
        if check.protected_for_conversation:
            return

        details = "\n".join(f"- {line}" for line in check.details)
        guidance = self._conversation_security_guidance(check)
        raise ConversationModeError(
            f"{guidance}\n"
            "Required for Conversation Mode: authenticated read, authenticated write, anonymous read denied, anonymous write denied.\n"
            f"Check results:\n{details}"
        )

    @staticmethod
    def _conversation_security_guidance(check) -> str:
        """Return a precise, user-facing reason for disabling Conversation Mode."""
        anonymous_problem = not check.anonymous_read_blocked or not check.anonymous_write_blocked

        if anonymous_problem:
            return (
                "Conversation Mode refused: the ntfy topic allows anonymous access, so it is not safe for "
                "remote wallet queries. Block anonymous read and anonymous write access, then try again."
            )

        if not check.authenticated_read_ok and check.authenticated_write_ok:
            return (
                "Conversation Mode cannot start: Wallet Watchguard can publish to ntfy, but the configured "
                "credentials do not have read/subscribe permission. Normal notifications can still work, but "
                "Conversation Mode needs read access so it can receive commands. For Start9/StartOS, "
                "Provision Publisher tokens are write-only; create a regular ntfy user, grant that user "
                "read-write access to the topic, create an access token in the ntfy web UI, then update "
                "Wallet Watchguard's ntfy config with that token."
            )

        if check.authenticated_read_ok and not check.authenticated_write_ok:
            return (
                "Conversation Mode cannot start: Wallet Watchguard can read the ntfy topic, but the configured "
                "credentials cannot publish replies. Grant write permission to the same topic, then try again."
            )

        if not check.authenticated_read_ok and not check.authenticated_write_ok:
            return (
                "Conversation Mode cannot start: Wallet Watchguard's configured ntfy credentials cannot read "
                "or publish to the topic. Check the token/username/password and the topic permissions."
            )

        return "Conversation Mode cannot start: the ntfy topic did not pass the required protection checks."

    async def run(self) -> None:
        since = int(time.time())
        print(
            "Conversation Mode is enabled. Listening for ntfy commands "
            f"on topic {self.notifier.topic!r}.",
            flush=True,
        )
        print(
            "Conversation commands: 'wwg help', 'wwg status', 'wwg wallets', "
            "'wwg next address', 'wwg next 3', 'wwg addresses --wallet-index 1 --limit 5', 'wwg fees'.",
            flush=True,
        )
        async for event in self.notifier.subscribe_json(since=since):
            if event.get("event") != "message":
                continue
            if self._is_own_message(event):
                continue
            command = self._normalise_command(str(event.get("message") or ""))
            if command is None:
                continue
            async with self._command_lock:
                try:
                    response = await self.handle_command(command)
                    await self.notifier.send(
                        "Wallet Watchguard response",
                        response,
                        priority="default",
                        tags="robot,bitcoin",
                        cache=False,
                    )
                except Exception as exc:
                    await self.notifier.send(
                        "Wallet Watchguard error",
                        f"Command failed: `{exc}`",
                        priority="high",
                        tags="warning,bitcoin",
                        cache=False,
                    )

    def _is_own_message(self, event: dict[str, Any]) -> bool:
        title = str(event.get("title") or "")
        if title.startswith("Wallet Watchguard"):
            return True
        tags = event.get("tags") or []
        return isinstance(tags, list) and "robot" in tags

    def _normalise_command(self, message: str) -> str | None:
        text = message.strip()
        if not text:
            return None
        lowered = text.lower()
        if self.command_prefix:
            prefix = self.command_prefix.lower()
            if lowered == prefix:
                return "help"
            if lowered.startswith(prefix + " "):
                return text[len(self.command_prefix) :].strip()
        natural_starts = ("help", "status", "wallets", "next", "receive", "address", "addresses", "balance", "latest", "unused", "fees", "fee", "mempool fees")
        if lowered.startswith(natural_starts):
            return text
        return None

    async def handle_command(self, command: str) -> str:
        command = command.strip()
        lowered = command.lower()
        if lowered in {"help", "?", "commands"}:
            return self._help_text()
        if lowered in {"status", "show status", "system status"}:
            return self._status_text()
        if lowered in {"wallets", "list wallets", "wallet list"}:
            return self._wallets_text()
        if lowered.startswith("addresses"):
            return await self._handle_addresses_command(command)
        if lowered.startswith("balance"):
            return await self._handle_addresses_command("addresses --all --include-change --only-nonzero --limit 100")
        if self._looks_like_fee_command(lowered):
            return await self._fees_text()
        if self._looks_like_next_receive_command(lowered):
            count = self._extract_count(lowered, default=1)
            return await self._next_unused_receive_addresses(count=count, command=command)
        return (
            "Unknown Wallet Watchguard command.\n\n"
            "Try:\n"
            "`wwg help`\n"
            "`wwg status`\n"
            "`wwg wallets`\n"
            "`wwg next address`\n"
            "`wwg next 3`\n"
            "`wwg addresses --wallet-index 1 --limit 5`\n"
            "`wwg fees`"
        )

    def _help_text(self) -> str:
        prefix = f"{self.command_prefix} " if self.command_prefix else ""
        return (
            "**Wallet Watchguard conversation commands**\n\n"
            f"`{prefix}status` - show the same runtime summary printed at startup\n"
            f"`{prefix}wallets` - list configured wallets\n"
            f"`{prefix}next address` - return the next unused receive address\n"
            f"`{prefix}next 3` - return the next three unused receive addresses\n"
            f"`{prefix}addresses --wallet-index 1 --limit 5` - list receive addresses\n"
            f"`{prefix}addresses --wallet \"Wallet name\" --include-change --only-used` - list matching addresses\n"
            f"`{prefix}balance` - show non-zero receive/change addresses across wallets\n"
            f"`{prefix}fees` - show local Mempool low/medium/high fee recommendations"
        )

    def _status_text(self) -> str:
        if self.status_provider is None:
            return "Status is not available in this Conversation Mode context."
        return self._truncate(self.status_provider().strip())

    def _wallets_text(self) -> str:
        wallets = self.config.get("wallets") or []
        if not wallets:
            return "No wallets are configured."
        lines = ["**Configured wallets**"]
        for index, wallet in enumerate(wallets, start=1):
            lines.append(f"{index}. {wallet['name']} ({wallet['network']} / {wallet['wallet_type']})")
        return "\n".join(lines)

    @staticmethod
    def _looks_like_fee_command(lowered: str) -> bool:
        return lowered in {"fees", "fee", "mempool fees", "mempool fee", "fee estimates", "recommended fees"}

    async def _fees_text(self) -> str:
        mempool = MempoolClient(self.config.get("mempool") or {})
        fees = await mempool.get_recommended_fees()
        summary = format_mempool_fee_summary(fees)
        first_line, _, rest = summary.partition("\n")
        if rest:
            return f"**{first_line}**\n{rest}"
        return f"**{summary}**"

    @staticmethod
    def _looks_like_next_receive_command(lowered: str) -> bool:
        if lowered.startswith(("next", "receive", "latest", "unused", "address")):
            return "address" in lowered or "receive" in lowered or lowered.startswith("next")
        return False

    @staticmethod
    def _extract_count(text: str, *, default: int) -> int:
        match = re.search(r"\b(\d{1,3})\b", text)
        if match:
            return max(1, int(match.group(1)))
        words = {
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "ten": 10,
        }
        for word, value in words.items():
            if re.search(rf"\b{word}\b", text):
                return value
        return default

    def _wallet_xpub(self, wallet: dict[str, Any]) -> str:
        return decrypt_xpub_with_passphrase(
            encrypted_xpub_b64=wallet["encrypted_xpub"],
            passphrase=self.passphrase,
            metadata=metadata_from_config(wallet["xpub_encryption"]),
        )

    def _derive(self, wallet: dict[str, Any], *, branch: int, limit: int) -> list[DerivedAddress]:
        helper_path = self.config["app"].get("derivation_helper_path", "./wwg-derive")
        path_template = wallet.get("receive_path_template", "0/*") if branch == 0 else wallet.get("change_path_template", "1/*")
        return derive_addresses(
            helper_path=helper_path,
            wallet_name=wallet["name"],
            xpub=self._wallet_xpub(wallet),
            network=wallet["network"],
            wallet_type=wallet["wallet_type"],
            account_path=wallet["account_path"],
            path_template=path_template,
            branch=branch,
            start=0,
            end=limit - 1,
        )

    def _select_wallets(self, args: argparse.Namespace, *, allow_all_default: bool = False) -> list[dict[str, Any]]:
        wallets = self.config.get("wallets") or []
        if not wallets:
            raise ValueError("No wallets are configured")
        if getattr(args, "all", False):
            return wallets
        wallet_index = getattr(args, "wallet_index", None)
        if wallet_index is not None:
            index = int(wallet_index) - 1
            if index < 0 or index >= len(wallets):
                raise ValueError(f"Wallet index must be between 1 and {len(wallets)}")
            return [wallets[index]]
        wallet_name = getattr(args, "wallet", None)
        if wallet_name:
            requested = str(wallet_name).strip()
            exact = [w for w in wallets if w["name"] == requested]
            if exact:
                return exact
            requested_lower = requested.lower()
            partial = [w for w in wallets if requested_lower in w["name"].lower()]
            if len(partial) == 1:
                return partial
            raise ValueError("Wallet name did not uniquely match. Use `wwg wallets` and then `--wallet-index N`.")
        if len(wallets) == 1:
            return [wallets[0]]
        if allow_all_default:
            return wallets
        raise ValueError("Multiple wallets are configured. Use `wwg wallets`, then add `--wallet-index N` or `--all`.")

    async def _address_rows(
        self,
        wallet: dict[str, Any],
        *,
        limit: int,
        include_change: bool,
        only_nonzero: bool,
        only_used: bool,
    ) -> list[AddressRow]:
        branches = [0, 1] if include_change else [0]
        rows: list[AddressRow] = []
        for branch in branches:
            label = "receive" if branch == 0 else "change"
            for item in self._derive(wallet, branch=branch, limit=limit):
                balance = await self.electrum.call("blockchain.scripthash.get_balance", [item.scripthash])
                history = await self.electrum.call("blockchain.scripthash.get_history", [item.scripthash])
                confirmed = int(balance.get("confirmed") or 0)
                unconfirmed = int(balance.get("unconfirmed") or 0)
                used = bool(history)
                if only_nonzero and confirmed == 0 and unconfirmed == 0:
                    continue
                if only_used and not used:
                    continue
                rows.append(AddressRow(label, item.path, "used" if used else "unused", confirmed, unconfirmed, item.address))
        return rows

    async def _next_unused_receive_addresses(self, *, count: int, command: str) -> str:
        count = min(max(1, count), self.max_addresses)
        pseudo_args = argparse.Namespace(wallet=None, wallet_index=None, all=False)
        tokens = shlex.split(command)
        if "--wallet-index" in tokens:
            pos = tokens.index("--wallet-index")
            if pos + 1 >= len(tokens):
                raise ValueError("--wallet-index needs a value")
            pseudo_args.wallet_index = int(tokens[pos + 1])
        if "--wallet" in tokens:
            pos = tokens.index("--wallet")
            if pos + 1 >= len(tokens):
                raise ValueError("--wallet needs a value")
            pseudo_args.wallet = tokens[pos + 1]
        wallet = self._select_wallets(pseudo_args)[0]
        scan_limit = max(int(wallet.get("lookahead", self.config["app"].get("lookahead", 100))), count)
        found: list[DerivedAddress] = []
        for item in self._derive(wallet, branch=0, limit=scan_limit):
            history = await self.electrum.call("blockchain.scripthash.get_history", [item.scripthash])
            if not history:
                found.append(item)
                if len(found) >= count:
                    break
        if not found:
            return (
                f"No unused receive address found for **{wallet['name']}** within lookahead {scan_limit}.\n"
                "Increase the wallet lookahead or check the wallet derivation path."
            )
        lines = [f"**Next unused receive address{'es' if len(found) != 1 else ''}: {wallet['name']}**"]
        for item in found:
            lines.append(f"`{item.path}` `{item.address}`")
        return self._truncate("\n".join(lines))

    @staticmethod
    def _address_arg_parser() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="addresses", add_help=False)
        parser.add_argument("command", nargs="?")
        parser.add_argument("--wallet", default=None)
        parser.add_argument("--wallet-index", type=int, default=None)
        parser.add_argument("--all", action="store_true")
        parser.add_argument("--limit", type=int, default=10)
        parser.add_argument("--include-change", action="store_true")
        parser.add_argument("--only-nonzero", action="store_true")
        parser.add_argument("--only-used", action="store_true")
        return parser

    async def _handle_addresses_command(self, command: str) -> str:
        parser = self._address_arg_parser()
        try:
            args = parser.parse_args(shlex.split(command))
        except SystemExit as exc:
            raise ValueError(f"Could not parse addresses command: {exc}") from exc
        limit = min(max(1, int(args.limit)), self.max_addresses)
        wallets = self._select_wallets(args, allow_all_default=False)
        lines: list[str] = []
        for wallet in wallets:
            rows = await self._address_rows(
                wallet,
                limit=limit,
                include_change=bool(args.include_change),
                only_nonzero=bool(args.only_nonzero),
                only_used=bool(args.only_used),
            )
            lines.append(f"**{wallet['name']}** ({wallet['network']} / {wallet['wallet_type']})")
            if not rows:
                lines.append("No addresses matched the selected filters.")
                continue
            for row in rows:
                lines.append(
                    f"`{row.branch_label}` `{row.path}` `{row.status}` "
                    f"conf `{row.confirmed:,}` unconf `{row.unconfirmed:,}`\n`{row.address}`"
                )
        return self._truncate("\n\n".join(lines))

    def _truncate(self, text: str) -> str:
        if len(text) <= self.max_response_chars:
            return text
        return text[: self.max_response_chars - 80].rstrip() + "\n\n_Output truncated. Use a smaller --limit._"
