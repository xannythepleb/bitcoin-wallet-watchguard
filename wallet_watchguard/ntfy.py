from __future__ import annotations

from typing import Any

import httpx

from .crypto import decrypt_string_with_passphrase, metadata_from_config


class NtfyNotifier:
    def __init__(self, config: dict[str, Any]) -> None:
        self.server = str(config["server"]).rstrip("/")
        self.topic = str(config["topic"])
        self.priority = str(config.get("priority", "default"))
        self.tags = str(config.get("tags", "bitcoin"))
        self.tls_verify = bool(config.get("tls_verify", True))
        self.timeout_seconds = int(config.get("timeout_seconds", 15))

        auth = config.get("auth") or {"type": "none"}
        self.auth_type = auth.get("type", "none")
        self.token = auth.get("token")
        self.username = auth.get("username")
        self.password = auth.get("password")

    async def send(self, title: str, message: str, *, priority: str | None = None, tags: str | None = None) -> None:
        headers = {
            "Title": title,
            "Priority": priority or self.priority,
            "Tags": tags or self.tags,
            "Markdown": "yes",
        }

        auth = None

        if self.auth_type == "token":
            if not self.token:
                raise ValueError("ntfy auth type is token, but no token was configured")
            headers["Authorization"] = f"Bearer {self.token}"

        elif self.auth_type == "basic":
            if not self.username or not self.password:
                raise ValueError("ntfy auth type is basic, but username/password were not configured")
            auth = (self.username, self.password)

        elif self.auth_type == "none":
            pass

        else:
            raise ValueError(f"Unsupported ntfy auth type: {self.auth_type}")

        async with httpx.AsyncClient(timeout=self.timeout_seconds, verify=self.tls_verify) as client:
            response = await client.post(
                f"{self.server}/{self.topic}",
                content=message.encode("utf-8"),
                headers=headers,
                auth=auth,
            )
            response.raise_for_status()


def decrypt_ntfy_config(ntfy_config: dict[str, Any], passphrase: str) -> dict[str, Any]:
    auth = dict(ntfy_config.get("auth") or {"type": "none"})
    auth_type = auth.get("type", "none")

    decrypted_auth: dict[str, Any] = {"type": auth_type}

    if auth_type == "token":
        decrypted_auth["token"] = decrypt_string_with_passphrase(
            encrypted_value_b64=auth["encrypted_token"],
            passphrase=passphrase,
            metadata=metadata_from_config(auth["token_encryption"]),
            secret_name="ntfy token",
        )
    elif auth_type == "basic":
        decrypted_auth["username"] = decrypt_string_with_passphrase(
            encrypted_value_b64=auth["encrypted_username"],
            passphrase=passphrase,
            metadata=metadata_from_config(auth["username_encryption"]),
            secret_name="ntfy username",
        )
        decrypted_auth["password"] = decrypt_string_with_passphrase(
            encrypted_value_b64=auth["encrypted_password"],
            passphrase=passphrase,
            metadata=metadata_from_config(auth["password_encryption"]),
            secret_name="ntfy password",
        )
    elif auth_type == "none":
        pass
    else:
        raise ValueError(f"Unsupported ntfy auth type: {auth_type}")

    return {
        "server": ntfy_config["server"],
        "topic": ntfy_config["topic"],
        "auth": decrypted_auth,
        "priority": ntfy_config.get("priority", "default"),
        "tags": ntfy_config.get("tags", "bitcoin"),
        "tls_verify": bool(ntfy_config.get("tls_verify", True)),
        "timeout_seconds": int(ntfy_config.get("timeout_seconds", 15)),
    }
