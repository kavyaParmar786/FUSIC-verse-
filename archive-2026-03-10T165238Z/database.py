"""
database.py
───────────────────────────────────────────────────────────
Enterprise Ticket Bot — Persistent Storage Layer
Supports MongoDB (primary) with automatic JSON fallback.
All async methods are safe to call from discord.py cogs.
"""

import os
import json
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("ticketbot.db")

# ─── Try importing Motor (async MongoDB driver) ───────────
try:
    import motor.motor_asyncio as motor
    MOTOR_AVAILABLE = True
except ImportError:
    MOTOR_AVAILABLE = False
    log.warning("motor not installed – falling back to JSON storage.")

# ─── JSON file paths ──────────────────────────────────────
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

JSON_FILES = {
    "tickets":       DATA_DIR / "tickets.json",
    "guilds":        DATA_DIR / "guilds.json",
    "users":         DATA_DIR / "users.json",
    "stats":         DATA_DIR / "stats.json",
    "staff_stats":   DATA_DIR / "staff_stats.json",
    "ratings":       DATA_DIR / "ratings.json",
    "notes":         DATA_DIR / "notes.json",
    "tags":          DATA_DIR / "tags.json",
    "queue":         DATA_DIR / "queue.json",
}

# ─── Ensure JSON files exist ──────────────────────────────
for path in JSON_FILES.values():
    if not path.exists():
        path.write_text("{}")


# ══════════════════════════════════════════════════════════
#  JSON BACKEND
# ══════════════════════════════════════════════════════════
class JSONBackend:
    """Thread-safe (via asyncio lock) JSON storage backend."""

    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {k: asyncio.Lock() for k in JSON_FILES}
        self._cache: dict[str, dict] = {}

    # ── Low-level helpers ─────────────────────────────────
    async def _load(self, collection: str) -> dict:
        if collection in self._cache:
            return self._cache[collection]
        path = JSON_FILES[collection]
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, FileNotFoundError):
            data = {}
        self._cache[collection] = data
        return data

    async def _save(self, collection: str, data: dict):
        self._cache[collection] = data
        path = JSON_FILES[collection]
        path.write_text(json.dumps(data, indent=2, default=str))

    # ── Public CRUD ───────────────────────────────────────
    async def find_one(self, collection: str, query: dict) -> Optional[dict]:
        async with self._locks[collection]:
            data = await self._load(collection)
            for doc in data.values():
                if all(doc.get(k) == v for k, v in query.items()):
                    return dict(doc)
            return None

    async def find_many(self, collection: str, query: dict) -> list[dict]:
        async with self._locks[collection]:
            data = await self._load(collection)
            results = []
            for doc in data.values():
                if all(doc.get(k) == v for k, v in query.items()):
                    results.append(dict(doc))
            return results

    async def find_all(self, collection: str) -> list[dict]:
        async with self._locks[collection]:
            data = await self._load(collection)
            return list(data.values())

    async def insert(self, collection: str, document: dict) -> str:
        """Insert a document and return its _id."""
        async with self._locks[collection]:
            data = await self._load(collection)
            doc_id = document.get("_id") or str(datetime.now(timezone.utc).timestamp()).replace(".", "")
            document["_id"] = doc_id
            data[doc_id] = document
            await self._save(collection, data)
            return doc_id

    async def update(self, collection: str, query: dict, update: dict, upsert: bool = False):
        async with self._locks[collection]:
            data = await self._load(collection)
            updated = False
            for key, doc in data.items():
                if all(doc.get(k) == v for k, v in query.items()):
                    data[key].update(update)
                    updated = True
                    break
            if not updated and upsert:
                doc_id = query.get("_id") or str(datetime.now(timezone.utc).timestamp()).replace(".", "")
                merged = {**query, **update, "_id": doc_id}
                data[doc_id] = merged
            await self._save(collection, data)

    async def delete(self, collection: str, query: dict):
        async with self._locks[collection]:
            data = await self._load(collection)
            keys_to_delete = [
                k for k, doc in data.items()
                if all(doc.get(f) == v for f, v in query.items())
            ]
            for k in keys_to_delete:
                del data[k]
            await self._save(collection, data)

    async def count(self, collection: str, query: dict) -> int:
        docs = await self.find_many(collection, query)
        return len(docs)

    async def increment(self, collection: str, query: dict, field: str, amount: int = 1):
        async with self._locks[collection]:
            data = await self._load(collection)
            for key, doc in data.items():
                if all(doc.get(k) == v for k, v in query.items()):
                    data[key][field] = data[key].get(field, 0) + amount
                    await self._save(collection, data)
                    return
            # upsert
            doc_id = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
            data[doc_id] = {**query, field: amount, "_id": doc_id}
            await self._save(collection, data)


# ══════════════════════════════════════════════════════════
#  MONGODB BACKEND
# ══════════════════════════════════════════════════════════
class MongoBackend:
    """Async MongoDB backend via Motor."""

    def __init__(self, uri: str, db_name: str):
        self.client = motor.AsyncIOMotorClient(uri)
        self.db = self.client[db_name]

    def _col(self, name: str):
        return self.db[name]

    async def find_one(self, collection: str, query: dict) -> Optional[dict]:
        doc = await self._col(collection).find_one(query)
        return doc

    async def find_many(self, collection: str, query: dict) -> list[dict]:
        cursor = self._col(collection).find(query)
        return await cursor.to_list(length=None)

    async def find_all(self, collection: str) -> list[dict]:
        cursor = self._col(collection).find({})
        return await cursor.to_list(length=None)

    async def insert(self, collection: str, document: dict) -> str:
        result = await self._col(collection).insert_one(document)
        return str(result.inserted_id)

    async def update(self, collection: str, query: dict, update: dict, upsert: bool = False):
        await self._col(collection).update_one(query, {"$set": update}, upsert=upsert)

    async def delete(self, collection: str, query: dict):
        await self._col(collection).delete_many(query)

    async def count(self, collection: str, query: dict) -> int:
        return await self._col(collection).count_documents(query)

    async def increment(self, collection: str, query: dict, field: str, amount: int = 1):
        await self._col(collection).update_one(
            query, {"$inc": {field: amount}}, upsert=True
        )


# ══════════════════════════════════════════════════════════
#  DATABASE FACADE — auto-selects backend
# ══════════════════════════════════════════════════════════
class Database:
    """
    Public API used by all cogs.
    Automatically uses MongoDB if available, else JSON fallback.
    """

    def __init__(self):
        self._backend: Optional[MongoBackend | JSONBackend] = None
        self.using_mongo = False

    async def connect(self):
        uri = os.getenv("MONGODB_URI", "")
        db_name = os.getenv("MONGODB_DB", "ticketbot")

        if MOTOR_AVAILABLE and uri:
            try:
                backend = MongoBackend(uri, db_name)
                # Ping to verify connection
                await backend.client.admin.command("ping")
                self._backend = backend
                self.using_mongo = True
                log.info("✅  Connected to MongoDB at %s", uri)
                return
            except Exception as e:
                log.warning("MongoDB connection failed (%s) – using JSON fallback.", e)

        self._backend = JSONBackend()
        log.info("📂  Using JSON file storage (data/ directory).")

    # ── Proxy all calls to active backend ─────────────────
    async def find_one(self, collection: str, query: dict):
        return await self._backend.find_one(collection, query)

    async def find_many(self, collection: str, query: dict):
        return await self._backend.find_many(collection, query)

    async def find_all(self, collection: str):
        return await self._backend.find_all(collection)

    async def insert(self, collection: str, document: dict):
        return await self._backend.insert(collection, document)

    async def update(self, collection: str, query: dict, update: dict, upsert: bool = False):
        return await self._backend.update(collection, query, update, upsert=upsert)

    async def delete(self, collection: str, query: dict):
        return await self._backend.delete(collection, query)

    async def count(self, collection: str, query: dict):
        return await self._backend.count(collection, query)

    async def increment(self, collection: str, query: dict, field: str, amount: int = 1):
        return await self._backend.increment(collection, query, field, amount)

    # ── High-level ticket helpers ─────────────────────────
    async def get_ticket(self, channel_id: int) -> Optional[dict]:
        return await self.find_one("tickets", {"channel_id": channel_id})

    async def get_open_ticket(self, guild_id: int, user_id: int, category: str) -> Optional[dict]:
        return await self.find_one("tickets", {
            "guild_id": guild_id,
            "user_id": user_id,
            "category": category,
            "status": "open"
        })

    async def get_guild_config(self, guild_id: int) -> dict:
        cfg = await self.find_one("guilds", {"guild_id": guild_id})
        return cfg or {}

    async def set_guild_config(self, guild_id: int, config: dict):
        await self.update("guilds", {"guild_id": guild_id}, config, upsert=True)

    async def get_staff_stats(self, guild_id: int, staff_id: int) -> dict:
        doc = await self.find_one("staff_stats", {"guild_id": guild_id, "staff_id": staff_id})
        return doc or {"guild_id": guild_id, "staff_id": staff_id, "claimed": 0, "closed": 0, "total_response_ms": 0, "response_count": 0}

    async def record_staff_claim(self, guild_id: int, staff_id: int, response_ms: int):
        await self.increment("staff_stats", {"guild_id": guild_id, "staff_id": staff_id}, "claimed", 1)
        await self.increment("staff_stats", {"guild_id": guild_id, "staff_id": staff_id}, "total_response_ms", response_ms)
        await self.increment("staff_stats", {"guild_id": guild_id, "staff_id": staff_id}, "response_count", 1)

    async def record_staff_close(self, guild_id: int, staff_id: int):
        await self.increment("staff_stats", {"guild_id": guild_id, "staff_id": staff_id}, "closed", 1)

    async def save_rating(self, ticket_id: str, user_id: int, guild_id: int, rating: int):
        await self.update(
            "ratings",
            {"ticket_id": ticket_id},
            {"ticket_id": ticket_id, "user_id": user_id, "guild_id": guild_id, "rating": rating, "created_at": datetime.now(timezone.utc).isoformat()},
            upsert=True
        )

    async def get_guild_avg_rating(self, guild_id: int) -> float:
        docs = await self.find_many("ratings", {"guild_id": guild_id})
        if not docs:
            return 0.0
        return round(sum(d["rating"] for d in docs) / len(docs), 2)


# ── Singleton ─────────────────────────────────────────────
db = Database()
