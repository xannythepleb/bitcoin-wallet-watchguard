from __future__ import annotations

from pathlib import Path
from typing import Iterable

import aiosqlite

from .models import DerivedAddress, WalletEvent

SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS watched_scripts (
    scripthash TEXT PRIMARY KEY,
    wallet_name TEXT NOT NULL,
    network TEXT NOT NULL,
    wallet_type TEXT NOT NULL,
    branch INTEGER NOT NULL,
    address_index INTEGER NOT NULL,
    path TEXT NOT NULL,
    address TEXT NOT NULL,
    script_pubkey TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    last_status TEXT
);

CREATE INDEX IF NOT EXISTS idx_watched_scripts_wallet ON watched_scripts(wallet_name);
CREATE INDEX IF NOT EXISTS idx_watched_scripts_script_pubkey ON watched_scripts(script_pubkey);
CREATE INDEX IF NOT EXISTS idx_watched_scripts_branch_index ON watched_scripts(wallet_name, branch, address_index);

CREATE TABLE IF NOT EXISTS tx_history (
    scripthash TEXT NOT NULL,
    txid TEXT NOT NULL,
    height INTEGER NOT NULL,
    PRIMARY KEY (scripthash, txid)
);

CREATE TABLE IF NOT EXISTS utxos (
    txid TEXT NOT NULL,
    vout INTEGER NOT NULL,
    wallet_name TEXT NOT NULL,
    address TEXT NOT NULL,
    path TEXT NOT NULL,
    value_sats INTEGER NOT NULL,
    spent_by_txid TEXT,
    PRIMARY KEY (txid, vout)
);

CREATE TABLE IF NOT EXISTS wallet_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet_name TEXT NOT NULL,
    txid TEXT NOT NULL,
    event_type TEXT NOT NULL,
    amount_sats INTEGER NOT NULL,
    status TEXT NOT NULL,
    height INTEGER NOT NULL,
    address TEXT,
    path TEXT,
    fee_sats INTEGER,
    notified INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(wallet_name, txid, event_type, status)
);
"""


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    def _conn(self) -> aiosqlite.Connection:
        if self.conn is None:
            raise RuntimeError("Database is not connected")
        return self.conn

    async def upsert_watched_scripts(self, scripts: Iterable[DerivedAddress]) -> None:
        conn = self._conn()
        await conn.executemany(
            """
            INSERT INTO watched_scripts (
                scripthash, wallet_name, network, wallet_type, branch, address_index,
                path, address, script_pubkey
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scripthash) DO UPDATE SET
                wallet_name = excluded.wallet_name,
                network = excluded.network,
                wallet_type = excluded.wallet_type,
                branch = excluded.branch,
                address_index = excluded.address_index,
                path = excluded.path,
                address = excluded.address,
                script_pubkey = excluded.script_pubkey
            """,
            [
                (
                    s.scripthash,
                    s.wallet_name,
                    s.network,
                    s.wallet_type,
                    s.branch,
                    s.index,
                    s.path,
                    s.address,
                    s.script_pubkey,
                )
                for s in scripts
            ],
        )
        await conn.commit()

    async def set_scripthash_status(self, scripthash: str, status: str | None) -> bool:
        """Return True if the status changed."""
        conn = self._conn()
        cur = await conn.execute("SELECT last_status FROM watched_scripts WHERE scripthash = ?", (scripthash,))
        row = await cur.fetchone()
        previous = None if row is None else row["last_status"]
        if previous == status:
            return False
        await conn.execute("UPDATE watched_scripts SET last_status = ?, used = 1 WHERE scripthash = ?", (status, scripthash))
        await conn.commit()
        return True

    async def get_watched_script(self, scripthash: str) -> aiosqlite.Row | None:
        cur = await self._conn().execute("SELECT * FROM watched_scripts WHERE scripthash = ?", (scripthash,))
        return await cur.fetchone()

    async def get_scripthash_state(self, scripthash: str) -> dict[str, int | str | None]:
        """Return the stored monitoring state for one watched script.

        This is used during startup subscription baselining. If a script has no
        stored status and no stored history, we treat the initial Electrum
        subscription response as existing history rather than alerting the user
        about every old transaction in a newly imported wallet.
        """
        conn = self._conn()
        cur = await conn.execute(
            """
            SELECT
                ws.last_status AS last_status,
                COUNT(th.txid) AS history_count
            FROM watched_scripts ws
            LEFT JOIN tx_history th ON th.scripthash = ws.scripthash
            WHERE ws.scripthash = ?
            GROUP BY ws.scripthash, ws.last_status
            """,
            (scripthash,),
        )
        row = await cur.fetchone()
        if row is None:
            return {"last_status": None, "history_count": 0}
        return {"last_status": row["last_status"], "history_count": int(row["history_count"] or 0)}

    async def get_watched_scripts_for_wallet(self, wallet_name: str) -> list[aiosqlite.Row]:
        cur = await self._conn().execute(
            "SELECT * FROM watched_scripts WHERE wallet_name = ? ORDER BY branch, address_index",
            (wallet_name,),
        )
        return list(await cur.fetchall())

    async def get_wallet_stats(self) -> list[dict[str, int | str | None]]:
        conn = self._conn()
        cur = await conn.execute(
            """
            WITH wallet_names AS (
                SELECT wallet_name FROM watched_scripts
                UNION
                SELECT wallet_name FROM utxos
                UNION
                SELECT wallet_name FROM wallet_events
            ),
            script_stats AS (
                SELECT
                    wallet_name,
                    COUNT(*) AS watched_scripts,
                    SUM(CASE WHEN used != 0 THEN 1 ELSE 0 END) AS used_scripts
                FROM watched_scripts
                GROUP BY wallet_name
            ),
            tx_stats AS (
                SELECT
                    ws.wallet_name,
                    COUNT(DISTINCT th.txid) AS transactions,
                    COUNT(th.txid) AS history_entries
                FROM watched_scripts ws
                JOIN tx_history th ON th.scripthash = ws.scripthash
                GROUP BY ws.wallet_name
            ),
            utxo_stats AS (
                SELECT
                    wallet_name,
                    COUNT(*) AS utxos,
                    SUM(CASE WHEN spent_by_txid IS NULL THEN 1 ELSE 0 END) AS unspent_utxos,
                    SUM(CASE WHEN spent_by_txid IS NULL THEN value_sats ELSE 0 END) AS unspent_sats
                FROM utxos
                GROUP BY wallet_name
            ),
            event_stats AS (
                SELECT
                    wallet_name,
                    COUNT(*) AS wallet_events,
                    MAX(created_at) AS latest_event_at
                FROM wallet_events
                GROUP BY wallet_name
            )
            SELECT
                wn.wallet_name,
                COALESCE(ss.watched_scripts, 0) AS watched_scripts,
                COALESCE(ss.used_scripts, 0) AS used_scripts,
                COALESCE(ts.transactions, 0) AS transactions,
                COALESCE(ts.history_entries, 0) AS history_entries,
                COALESCE(us.utxos, 0) AS utxos,
                COALESCE(us.unspent_utxos, 0) AS unspent_utxos,
                COALESCE(us.unspent_sats, 0) AS unspent_sats,
                COALESCE(es.wallet_events, 0) AS wallet_events,
                es.latest_event_at AS latest_event_at
            FROM wallet_names wn
            LEFT JOIN script_stats ss ON ss.wallet_name = wn.wallet_name
            LEFT JOIN tx_stats ts ON ts.wallet_name = wn.wallet_name
            LEFT JOIN utxo_stats us ON us.wallet_name = wn.wallet_name
            LEFT JOIN event_stats es ON es.wallet_name = wn.wallet_name
            ORDER BY LOWER(wn.wallet_name)
            """
        )
        rows = await cur.fetchall()
        return [
            {
                "wallet_name": row["wallet_name"],
                "watched_scripts": int(row["watched_scripts"] or 0),
                "used_scripts": int(row["used_scripts"] or 0),
                "transactions": int(row["transactions"] or 0),
                "history_entries": int(row["history_entries"] or 0),
                "utxos": int(row["utxos"] or 0),
                "unspent_utxos": int(row["unspent_utxos"] or 0),
                "unspent_sats": int(row["unspent_sats"] or 0),
                "wallet_events": int(row["wallet_events"] or 0),
                "latest_event_at": row["latest_event_at"],
            }
            for row in rows
        ]

    async def delete_wallet(self, wallet_name: str) -> dict[str, int]:
        conn = self._conn()
        deleted_rows = {
            "watched_scripts": 0,
            "tx_history": 0,
            "utxos": 0,
            "wallet_events": 0,
        }

        cur = await conn.execute(
            "SELECT scripthash FROM watched_scripts WHERE wallet_name = ?",
            (wallet_name,),
        )
        scripthashes = [row["scripthash"] for row in await cur.fetchall()]

        if scripthashes:
            placeholders = ", ".join("?" for _ in scripthashes)
            cur = await conn.execute(
                f"DELETE FROM tx_history WHERE scripthash IN ({placeholders})",
                scripthashes,
            )
            deleted_rows["tx_history"] = max(int(cur.rowcount or 0), 0)

        for table_name in ["utxos", "wallet_events", "watched_scripts"]:
            cur = await conn.execute(
                f"DELETE FROM {table_name} WHERE wallet_name = ?",
                (wallet_name,),
            )
            deleted_rows[table_name] = max(int(cur.rowcount or 0), 0)

        await conn.commit()
        return deleted_rows

    async def rename_wallet(self, old_name: str, new_name: str) -> dict[str, int]:
        conn = self._conn()
        renamed_rows = {
            "watched_scripts": 0,
            "utxos": 0,
            "wallet_events": 0,
        }

        for table_name in ["watched_scripts", "utxos", "wallet_events"]:
            cur = await conn.execute(
                f"UPDATE {table_name} SET wallet_name = ? WHERE wallet_name = ?",
                (new_name, old_name),
            )
            renamed_rows[table_name] = max(int(cur.rowcount or 0), 0)

        await conn.commit()
        return renamed_rows

    async def remember_history(self, scripthash: str, history: list[dict]) -> list[dict]:
        """Store history rows and return only newly seen tx entries."""
        conn = self._conn()
        new_items: list[dict] = []

        for item in history:
            txid = item["tx_hash"]
            height = int(item.get("height", 0))
            cur = await conn.execute(
                "SELECT height FROM tx_history WHERE scripthash = ? AND txid = ?",
                (scripthash, txid),
            )
            existing = await cur.fetchone()
            if existing:
                previous_height = int(existing["height"])
                if previous_height != height:
                    await conn.execute(
                        "UPDATE tx_history SET height = ? WHERE scripthash = ? AND txid = ?",
                        (height, scripthash, txid),
                    )
                    new_items.append(item)
                continue
            await conn.execute(
                "INSERT INTO tx_history (scripthash, txid, height) VALUES (?, ?, ?)",
                (scripthash, txid, height),
            )
            new_items.append(item)

        await conn.commit()
        return new_items

    async def save_event(self, event: WalletEvent) -> bool:
        conn = self._conn()
        cur = await conn.execute(
            """
            INSERT OR IGNORE INTO wallet_events (
                wallet_name, txid, event_type, amount_sats, status, height, address, path, fee_sats
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.wallet_name,
                event.txid,
                event.event_type,
                event.amount_sats,
                event.status,
                event.height,
                event.address,
                event.path,
                event.fee_sats,
            ),
        )
        await conn.commit()
        return cur.rowcount > 0
