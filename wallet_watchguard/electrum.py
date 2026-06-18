from __future__ import annotations

import asyncio
import json
import ipaddress
import ssl
from collections.abc import Awaitable, Callable
from typing import Any

try:
    from python_socks.async_.asyncio import Proxy
except Exception:  # pragma: no cover - optional dependency import guard
    Proxy = None  # type: ignore[assignment]

NotificationHandler = Callable[[dict[str, Any]], Awaitable[None]]


def default_tls_verify_for_host(host: str) -> bool:
    """Default to relaxed verification for local/self-host appliance names only."""
    lowered = host.strip().lower()
    if lowered in {"localhost", "127.0.0.1", "::1"}:
        return False
    if lowered.endswith((".local", ".localdomain", ".lan", ".onion")):
        return False
    try:
        ip = ipaddress.ip_address(lowered)
        return not (ip.is_private or ip.is_loopback or ip.is_link_local)
    except ValueError:
        return True


class ElectrumError(RuntimeError):
    pass


class ElectrumClient:
    """Small newline-delimited JSON-RPC Electrum client.

    It intentionally implements only the primitives Wallet Watchguard needs.
    """

    def __init__(
        self,
        host: str,
        port: int,
        *,
        use_tls: bool,
        tls_verify: bool | None = None,
        socks_proxy: str | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        self.host = host
        self.port = port
        self.use_tls = use_tls
        self.tls_verify = default_tls_verify_for_host(host) if tls_verify is None else tls_verify
        self.socks_proxy = socks_proxy
        self.timeout_seconds = timeout_seconds
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._notification_tasks: set[asyncio.Task[None]] = set()

    def _ssl_context(self) -> ssl.SSLContext | None:
        if not self.use_tls:
            return None

        if self.tls_verify:
            return ssl.create_default_context()

        # Start9/StartOS and similar local node setups commonly use private/self-signed certs.
        # This keeps TLS encryption enabled but disables public CA verification.
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    async def connect(self) -> None:
        ssl_context = self._ssl_context()

        if self.host.endswith(".onion") and not self.socks_proxy:
            raise ElectrumError(
                "Onion Electrum hosts need socks_proxy set, or the container/host network must route .onion traffic."
            )

        if self.socks_proxy:
            if Proxy is None:
                raise ElectrumError("python-socks is required for socks_proxy support")
            proxy = Proxy.from_url(f"socks5://{self.socks_proxy}")
            sock = await proxy.connect(dest_host=self.host, dest_port=self.port, timeout=self.timeout_seconds)
            self.reader, self.writer = await asyncio.open_connection(
                sock=sock,
                ssl=ssl_context,
                server_hostname=self.host if self.use_tls and self.tls_verify else None,
            )
            return

        self.reader, self.writer = await asyncio.wait_for(
            asyncio.open_connection(
                self.host,
                self.port,
                ssl=ssl_context,
                server_hostname=self.host if self.use_tls and self.tls_verify else None,
            ),
            timeout=self.timeout_seconds,
        )

    async def close(self) -> None:
        # Notification handlers are deliberately dispatched as background tasks
        # (see listen()). Cancel them before closing the socket so a handler that
        # is waiting on call() cannot be left pending during shutdown.
        for task in tuple(self._notification_tasks):
            task.cancel()
        if self._notification_tasks:
            await asyncio.gather(*self._notification_tasks, return_exceptions=True)
            self._notification_tasks.clear()

        if self.writer is not None:
            self.writer.close()
            await self.writer.wait_closed()

    async def call(self, method: str, params: list[Any] | None = None) -> Any:
        if self.writer is None:
            raise ElectrumError("Electrum client is not connected")

        self._next_id += 1
        request_id = self._next_id
        payload = {"id": request_id, "method": method, "params": params or []}

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        self._pending[request_id] = fut

        self.writer.write(json.dumps(payload).encode("utf-8") + b"\n")
        await self.writer.drain()

        return await asyncio.wait_for(fut, timeout=self.timeout_seconds)

    def _track_notification_task(self, task: asyncio.Task[None]) -> None:
        self._notification_tasks.add(task)

        def done(completed: asyncio.Task[None]) -> None:
            self._notification_tasks.discard(completed)
            if completed.cancelled():
                return
            exc = completed.exception()
            if exc is not None:
                loop = asyncio.get_running_loop()
                loop.call_exception_handler(
                    {
                        "message": "Unhandled Electrum notification handler exception",
                        "exception": exc,
                        "task": completed,
                    }
                )

        task.add_done_callback(done)

    async def listen(self, handler: NotificationHandler) -> None:
        if self.reader is None:
            raise ElectrumError("Electrum client is not connected")

        while True:
            line = await self.reader.readline()
            if not line:
                raise ElectrumError("Electrum server disconnected")

            message = json.loads(line.decode("utf-8"))

            if "id" in message and message["id"] in self._pending:
                fut = self._pending.pop(message["id"])
                if message.get("error"):
                    fut.set_exception(ElectrumError(str(message["error"])))
                else:
                    fut.set_result(message.get("result"))
                continue

            if "method" in message:
                # Do not await the notification handler in the socket reader loop.
                # Wallet Watchguard's handler needs to issue follow-up Electrum
                # calls such as blockchain.scripthash.get_history. Those call()
                # futures are completed by this same listen() loop when it reads
                # the server response, so awaiting the handler here deadlocks live
                # subscription processing. Dispatching the handler lets the reader
                # keep draining JSON-RPC responses and push notifications.
                self._track_notification_task(asyncio.create_task(handler(message)))
