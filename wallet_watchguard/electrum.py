from __future__ import annotations

import asyncio
import json
import ssl
from collections.abc import Awaitable, Callable
from typing import Any

try:
    from python_socks.async_.asyncio import Proxy
except Exception:  # pragma: no cover - optional dependency import guard
    Proxy = None  # type: ignore[assignment]

NotificationHandler = Callable[[dict[str, Any]], Awaitable[None]]


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
        socks_proxy: str | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        self.host = host
        self.port = port
        self.use_tls = use_tls
        self.socks_proxy = socks_proxy
        self.timeout_seconds = timeout_seconds
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}

    async def connect(self) -> None:
        ssl_context = ssl.create_default_context() if self.use_tls else None

        if self.host.endswith(".onion") and not self.socks_proxy:
            raise ElectrumError(
                "Onion Electrum hosts need socks_proxy set, or the container/host network must route .onion traffic."
            )

        if self.socks_proxy:
            if Proxy is None:
                raise ElectrumError("python-socks is required for socks_proxy support")
            proxy = Proxy.from_url(f"socks5://{self.socks_proxy}")
            sock = await proxy.connect(dest_host=self.host, dest_port=self.port, timeout=self.timeout_seconds)
            self.reader, self.writer = await asyncio.open_connection(sock=sock, ssl=ssl_context, server_hostname=self.host if self.use_tls else None)
            return

        self.reader, self.writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port, ssl=ssl_context),
            timeout=self.timeout_seconds,
        )

    async def close(self) -> None:
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
                await handler(message)
