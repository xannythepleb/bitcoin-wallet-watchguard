from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_TRUE_VALUES = {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class TorUpstreamConfig:
    enabled: bool
    socks_proxy: str
    manage_process: bool
    startup_timeout_seconds: int
    test_on_startup: bool
    data_dir: str | None

    @property
    def socks_host_port(self) -> tuple[str, int]:
        return parse_socks_host_port(self.socks_proxy)


def env_tor_upstream_enabled() -> bool:
    """Return True when WWG_TOR_UPSTREAM explicitly enables the Tor upstream."""
    value = os.environ.get("WWG_TOR_UPSTREAM")
    return value is not None and value.strip().lower() in _TRUE_VALUES


def parse_socks_host_port(socks_proxy: str) -> tuple[str, int]:
    """Parse the host:port form used by ElectrumClient/python-socks."""
    value = str(socks_proxy or "").strip()
    if not value:
        raise ValueError("Tor SOCKS proxy must not be blank")

    if value.startswith("socks5://"):
        value = value.removeprefix("socks5://")

    # IPv6 addresses may be written as [::1]:9050. Plain host:port remains the
    # recommended form for Docker Compose and the setup wizard.
    if value.startswith("["):
        host, _, rest = value[1:].partition("]:")
        if not host or not rest:
            raise ValueError(f"Invalid SOCKS proxy address: {socks_proxy!r}")
        return host, int(rest)

    host, sep, port_text = value.rpartition(":")
    if not sep or not host or not port_text:
        raise ValueError(f"Invalid SOCKS proxy address: {socks_proxy!r}; expected host:port")
    return host, int(port_text)


def tor_config_from_app_config(config: dict[str, Any], *, force_enabled: bool = False) -> TorUpstreamConfig:
    tor = config.get("tor") or {}
    return TorUpstreamConfig(
        enabled=bool(tor.get("enabled", False)) or force_enabled,
        socks_proxy=str(tor.get("socks_proxy") or "127.0.0.1:9050"),
        manage_process=bool(tor.get("manage_process", True)),
        startup_timeout_seconds=int(tor.get("startup_timeout_seconds", 60)),
        test_on_startup=bool(tor.get("test_on_startup", True)),
        data_dir=str(tor.get("data_dir") or "").strip() or None,
    )


def apply_tor_upstream(config: dict[str, Any], *, force_enabled: bool = False) -> dict[str, Any]:
    """
    Return a runtime config copy with Tor upstream applied to Electrum.

    The existing electrum.socks_proxy option remains supported for users who run
    their own external SOCKS proxy. The new tor.enabled switch is stronger: when
    it is on, Wallet Watchguard uses tor.socks_proxy for Electrum/Fulcrum even if
    electrum.socks_proxy was blank in the YAML.
    """
    runtime_config = deepcopy(config)
    tor_cfg = tor_config_from_app_config(runtime_config, force_enabled=force_enabled)

    if tor_cfg.enabled:
        runtime_config.setdefault("tor", {})["enabled"] = True
        runtime_config.setdefault("tor", {})["socks_proxy"] = tor_cfg.socks_proxy
        runtime_config.setdefault("electrum", {})["socks_proxy"] = tor_cfg.socks_proxy

    return runtime_config


class TorUpstreamManager:
    """Starts and stops Wallet Watchguard's optional internal Tor SOCKS proxy."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = tor_config_from_app_config(config)
        self.process: asyncio.subprocess.Process | None = None
        self._temp_data_dir: tempfile.TemporaryDirectory[str] | None = None
        self._data_dir_path: Path | None = None

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    async def start(self) -> None:
        if not self.config.enabled:
            return

        if not self.config.manage_process:
            await wait_for_socks_proxy(self.config.socks_proxy, timeout_seconds=self.config.startup_timeout_seconds)
            return

        tor_binary = shutil.which("tor")
        if tor_binary is None:
            raise RuntimeError(
                "Tor upstream is enabled, but the 'tor' binary is not installed. "
                "Use the Docker image/Compose build, install tor on the host, or disable tor.enabled."
            )

        host, port = self.config.socks_host_port
        data_dir = self._prepare_data_dir()
        socks_port = f"{host}:{port}"

        self.process = await asyncio.create_subprocess_exec(
            tor_binary,
            "--SocksPort",
            socks_port,
            "--DataDirectory",
            str(data_dir),
            "--ClientOnly",
            "1",
            "--Log",
            "notice stdout",
        )

        try:
            await wait_for_socks_proxy(self.config.socks_proxy, timeout_seconds=self.config.startup_timeout_seconds)
        except Exception:
            await self.stop()
            raise

    async def stop(self) -> None:
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=10)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()

        self.process = None

        if self._temp_data_dir is not None:
            self._temp_data_dir.cleanup()
            self._temp_data_dir = None
            self._data_dir_path = None

    def _prepare_data_dir(self) -> Path:
        if self.config.data_dir:
            data_dir = Path(self.config.data_dir)
            data_dir.mkdir(parents=True, exist_ok=True)
        else:
            self._temp_data_dir = tempfile.TemporaryDirectory(prefix="wwg-tor-")
            data_dir = Path(self._temp_data_dir.name)

        # Tor is deliberately picky about private key directory permissions.
        try:
            data_dir.chmod(0o700)
        except PermissionError:
            pass

        self._data_dir_path = data_dir
        return data_dir


async def wait_for_socks_proxy(socks_proxy: str, *, timeout_seconds: int) -> None:
    host, port = parse_socks_host_port(socks_proxy)
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last_error: Exception | None = None

    while True:
        if asyncio.get_running_loop().time() >= deadline:
            detail = f": {last_error}" if last_error else ""
            raise TimeoutError(f"Timed out waiting for Tor SOCKS proxy at {socks_proxy}{detail}")

        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=2)
            writer.close()
            await writer.wait_closed()
            # Keep a reference to reader so linters do not treat it as unused;
            # opening the connection is the actual readiness check.
            _ = reader
            return
        except Exception as exc:  # pragma: no cover - timing dependent
            last_error = exc
            await asyncio.sleep(0.25)
