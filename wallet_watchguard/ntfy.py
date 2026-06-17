from __future__ import annotations

import httpx


class NtfyNotifier:
    def __init__(self, config: dict) -> None:
        self.server = config["server"].rstrip("/")
        self.topic = config["topic"]
        self.priority = config.get("priority", "default")
        self.tags = config.get("tags", "bitcoin")

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

        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{self.server}/{self.topic}",
                content=message.encode("utf-8"),
                headers=headers,
                auth=auth,
            )
            response.raise_for_status()
