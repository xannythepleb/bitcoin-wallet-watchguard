from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from .crypto import decrypt_string_with_passphrase, metadata_from_config


@dataclass(frozen=True)
class NtfyProtectionCheck:
    authenticated_read_ok: bool
    authenticated_write_ok: bool
    anonymous_read_blocked: bool
    anonymous_write_blocked: bool
    details: list[str]

    @property
    def protected_for_conversation(self) -> bool:
        return (
            self.authenticated_read_ok
            and self.authenticated_write_ok
            and self.anonymous_read_blocked
            and self.anonymous_write_blocked
        )


class NtfyNotifier:
    def __init__(self, config: dict[str, Any]) -> None:
        self.server = str(config["server"]).rstrip("/")
        self.topic = str(config["topic"]).strip("/")
        self.priority = str(config.get("priority", "default"))
        self.tags = str(config.get("tags", "bitcoin"))
        self.tls_verify = bool(config.get("tls_verify", True))
        self.timeout_seconds = int(config.get("timeout_seconds", 15))

        auth = config.get("auth") or {"type": "none"}
        self.auth_type = str(auth.get("type", "none"))
        self.token = auth.get("token")
        self.username = auth.get("username")
        self.password = auth.get("password")

    @property
    def topic_url(self) -> str:
        return f"{self.server}/{self.topic}"

    def _auth_headers(self) -> dict[str, str]:
        if self.auth_type == "token":
            if not self.token:
                raise ValueError("ntfy auth type is token, but no token was configured")
            return {"Authorization": f"Bearer {self.token}"}
        if self.auth_type == "basic":
            return {}
        if self.auth_type == "none":
            return {}
        raise ValueError(f"Unsupported ntfy auth type: {self.auth_type}")

    def _auth_tuple(self) -> tuple[str, str] | None:
        if self.auth_type != "basic":
            return None
        if not self.username or not self.password:
            raise ValueError("ntfy auth type is basic, but username/password were not configured")
        return (str(self.username), str(self.password))

    def _timeout(self, *, streaming: bool = False) -> httpx.Timeout:
        if streaming:
            return httpx.Timeout(
                connect=self.timeout_seconds,
                read=None,
                write=self.timeout_seconds,
                pool=self.timeout_seconds,
            )
        return httpx.Timeout(self.timeout_seconds)

    async def send(
        self,
        title: str,
        message: str,
        *,
        priority: str | None = None,
        tags: str | None = None,
        cache: bool = True,
        firebase: bool = True,
    ) -> None:
        headers = {
            "Title": title,
            "Priority": priority or self.priority,
            "Tags": tags or self.tags,
            "Markdown": "yes",
        }
        headers.update(self._auth_headers())
        if not cache:
            headers["Cache"] = "no"
        if not firebase:
            headers["Firebase"] = "no"

        async with httpx.AsyncClient(timeout=self._timeout(), verify=self.tls_verify) as client:
            response = await client.post(
                self.topic_url,
                content=message.encode("utf-8"),
                headers=headers,
                auth=self._auth_tuple(),
            )
            response.raise_for_status()

    async def subscribe_json(self, *, since: str | int = "latest") -> AsyncIterator[dict[str, Any]]:
        headers = self._auth_headers()
        async with httpx.AsyncClient(timeout=self._timeout(streaming=True), verify=self.tls_verify) as client:
            async with client.stream(
                "GET",
                f"{self.topic_url}/json",
                params={"since": str(since)},
                headers=headers,
                auth=self._auth_tuple(),
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue

    async def check_conversation_security(self, *, probe_anonymous_write: bool = True) -> NtfyProtectionCheck:
        details: list[str] = []
        if self.auth_type not in {"token", "basic"}:
            return NtfyProtectionCheck(
                authenticated_read_ok=False,
                authenticated_write_ok=False,
                anonymous_read_blocked=False,
                anonymous_write_blocked=False,
                details=["ntfy auth type must be token or basic for Conversation Mode"],
            )

        auth_headers = self._auth_headers()
        auth_tuple = self._auth_tuple()
        authenticated_read_ok = False
        authenticated_write_ok = False
        anonymous_read_blocked = False
        anonymous_write_blocked = False

        async with httpx.AsyncClient(timeout=self._timeout(), verify=self.tls_verify) as client:
            try:
                response = await client.get(
                    f"{self.topic_url}/json",
                    params={"poll": "1", "since": "latest"},
                    headers=auth_headers,
                    auth=auth_tuple,
                )
                authenticated_read_ok = response.status_code < 400
                details.append("authenticated read: ok" if authenticated_read_ok else f"authenticated read: failed HTTP {response.status_code}")
            except Exception as exc:
                details.append(f"authenticated read: failed {exc}")

            try:
                response = await client.post(
                    self.topic_url,
                    content=b"Wallet Watchguard Conversation Mode authenticated write probe.",
                    headers={
                        **auth_headers,
                        "Title": "Wallet Watchguard security probe",
                        "Tags": "shield,bitcoin",
                        "Priority": "min",
                        "Cache": "no",
                        "Firebase": "no",
                    },
                    auth=auth_tuple,
                )
                authenticated_write_ok = response.status_code < 400
                details.append("authenticated write: ok" if authenticated_write_ok else f"authenticated write: failed HTTP {response.status_code}")
            except Exception as exc:
                details.append(f"authenticated write: failed {exc}")

            try:
                response = await client.get(
                    f"{self.topic_url}/json",
                    params={"poll": "1", "since": "latest"},
                )
                if response.status_code in {401, 403}:
                    anonymous_read_blocked = True
                    details.append(f"anonymous read: blocked HTTP {response.status_code}")
                else:
                    details.append(f"anonymous read: allowed or inconclusive HTTP {response.status_code}")
            except Exception as exc:
                details.append(f"anonymous read: failed to check {exc}")

            if not probe_anonymous_write:
                details.append("anonymous write: not probed; refusing Conversation Mode")
            else:
                try:
                    response = await client.post(
                        self.topic_url,
                        content=(
                            b"Wallet Watchguard anonymous write protection probe. "
                            b"If this publishes, the topic is not private enough for Conversation Mode."
                        ),
                        headers={
                            "Title": "Wallet Watchguard anonymous write probe",
                            "Tags": "warning,bitcoin",
                            "Priority": "min",
                            "Cache": "no",
                            "Firebase": "no",
                        },
                    )
                    if response.status_code in {401, 403}:
                        anonymous_write_blocked = True
                        details.append(f"anonymous write: blocked HTTP {response.status_code}")
                    else:
                        details.append(f"anonymous write: allowed or inconclusive HTTP {response.status_code}")
                except Exception as exc:
                    details.append(f"anonymous write: failed to check {exc}")

        return NtfyProtectionCheck(
            authenticated_read_ok=authenticated_read_ok,
            authenticated_write_ok=authenticated_write_ok,
            anonymous_read_blocked=anonymous_read_blocked,
            anonymous_write_blocked=anonymous_write_blocked,
            details=details,
        )


def _decrypt_auth(auth: dict[str, Any], passphrase: str, *, label_prefix: str) -> dict[str, Any]:
    auth = dict(auth or {"type": "none"})
    auth_type = auth.get("type", "none")
    decrypted_auth: dict[str, Any] = {"type": auth_type}

    if auth_type == "token":
        decrypted_auth["token"] = decrypt_string_with_passphrase(
            encrypted_value_b64=auth["encrypted_token"],
            passphrase=passphrase,
            metadata=metadata_from_config(auth["token_encryption"]),
            secret_name=f"{label_prefix} token",
        )
    elif auth_type == "basic":
        decrypted_auth["username"] = decrypt_string_with_passphrase(
            encrypted_value_b64=auth["encrypted_username"],
            passphrase=passphrase,
            metadata=metadata_from_config(auth["username_encryption"]),
            secret_name=f"{label_prefix} username",
        )
        decrypted_auth["password"] = decrypt_string_with_passphrase(
            encrypted_value_b64=auth["encrypted_password"],
            passphrase=passphrase,
            metadata=metadata_from_config(auth["password_encryption"]),
            secret_name=f"{label_prefix} password",
        )
    elif auth_type == "none":
        pass
    else:
        raise ValueError(f"Unsupported {label_prefix} auth type: {auth_type}")

    return decrypted_auth


def decrypt_ntfy_config(ntfy_config: dict[str, Any], passphrase: str) -> dict[str, Any]:
    return {
        "server": ntfy_config["server"],
        "topic": ntfy_config["topic"],
        "auth": _decrypt_auth(ntfy_config.get("auth") or {"type": "none"}, passphrase, label_prefix="ntfy"),
        "priority": ntfy_config.get("priority", "default"),
        "tags": ntfy_config.get("tags", "bitcoin"),
        "tls_verify": bool(ntfy_config.get("tls_verify", True)),
        "timeout_seconds": int(ntfy_config.get("timeout_seconds", 15)),
    }


def decrypt_conversation_ntfy_config(config: dict[str, Any], passphrase: str) -> dict[str, Any]:
    """
    Build the ntfy config used by Conversation Mode.

    By default Conversation Mode reuses the main ntfy topic and credentials. Users
    can optionally configure a separate topic and/or separate token/basic auth so
    normal alert traffic and remote wallet queries stay tidy.
    """
    ntfy_config = config["ntfy"]
    conversation = config.get("conversation") or {}
    conversation_topic = str(conversation.get("topic") or "").strip("/")
    topic = conversation_topic or ntfy_config["topic"]

    conversation_auth = conversation.get("auth") or {"type": "same_as_ntfy"}
    conversation_auth_type = conversation_auth.get("type", "same_as_ntfy")

    if conversation_auth_type == "same_as_ntfy":
        decrypted_auth = _decrypt_auth(ntfy_config.get("auth") or {"type": "none"}, passphrase, label_prefix="ntfy")
    else:
        decrypted_auth = _decrypt_auth(conversation_auth, passphrase, label_prefix="Conversation Mode ntfy")

    return {
        "server": ntfy_config["server"],
        "topic": topic,
        "auth": decrypted_auth,
        "priority": conversation.get("priority", ntfy_config.get("priority", "default")),
        "tags": conversation.get("tags", ntfy_config.get("tags", "bitcoin")),
        "tls_verify": bool(conversation.get("tls_verify", ntfy_config.get("tls_verify", True))),
        "timeout_seconds": int(conversation.get("timeout_seconds", ntfy_config.get("timeout_seconds", 15))),
    }
