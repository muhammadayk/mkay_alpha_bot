from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Protocol

import requests

from mkay_radar.models import Opportunity


class Store(Protocol):
    def upsert(self, opportunity: Opportunity) -> str: ...
    def review_queue(self, limit: int, unsent_only: bool = False) -> list[dict]: ...
    def mark_review_sent(self, opportunity_id: str) -> None: ...
    def set_status(self, opportunity_id: str, status: str) -> None: ...
    def get_opportunity(self, opportunity_id: str) -> dict | None: ...
    def register_broadcast_chat(self, chat_id: int, title: str | None, chat_type: str) -> None: ...
    def broadcast_chats(self) -> list[dict]: ...
    def mark_posted(self, opportunity_id: str) -> None: ...


class SQLiteStore:
    def __init__(self, database_url: str) -> None:
        path = Path(database_url.removeprefix("sqlite:///"))
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row

    def initialize(self) -> None:
        self.connection.executescript("""
        create table if not exists opportunities (
          id integer primary key, title text not null, project text not null, category text not null,
          description text, requirements text, chain text, reward text, url text not null,
          canonical_url text not null unique, source text not null, source_status text, risk_level text not null,
          score real not null, status text not null, content_hash text not null,
          discovered_at text not null, updated_at text not null, review_sent_at text, posted_at text
        );
        create table if not exists telegram_chats (
          chat_id integer primary key, title text, chat_type text not null,
          is_active integer not null default 1, registered_at text not null default current_timestamp
        );
        """)
        columns = {row[1] for row in self.connection.execute("pragma table_info(opportunities)")}
        if "review_sent_at" not in columns:
            self.connection.execute("alter table opportunities add column review_sent_at text")
        self.connection.commit()

    def upsert(self, opportunity: Opportunity) -> str:
        record = opportunity.record()
        record["updated_at"] = record["discovered_at"]
        existing = self.connection.execute("select id, content_hash from opportunities where canonical_url = ?", (record["canonical_url"],)).fetchone()
        if not existing:
            near = self.connection.execute("select id, title, content_hash from opportunities where source = ?", (record["source"],)).fetchall()
            existing = next((row for row in near if SequenceMatcher(None, row["title"].lower(), record["title"].lower()).ratio() >= .92), None)
        if existing and existing["content_hash"] == record["content_hash"]:
            return "unchanged"
        columns = [key for key in record if key != "status"]
        if existing:
            assignments = ", ".join(f"{column} = ?" for column in columns if column not in {"canonical_url", "discovered_at"})
            values = [record[column] for column in columns if column not in {"canonical_url", "discovered_at"}] + [existing["id"]]
            self.connection.execute(f"update opportunities set {assignments}, updated_at = datetime('now') where id = ?", values)
            action = "updated"
        else:
            columns = list(record)
            self.connection.execute(f"insert into opportunities ({', '.join(columns)}) values ({', '.join('?' for _ in columns)})", [record[column] for column in columns])
            action = "created"
        self.connection.commit()
        return action

    def review_queue(self, limit: int, unsent_only: bool = False) -> list[dict]:
        query = "select * from opportunities where status = 'REVIEW'"
        if unsent_only:
            query += " and review_sent_at is null"
        query += " order by discovered_at desc limit ?"
        return [dict(row) for row in self.connection.execute(query, (limit,))]

    def mark_review_sent(self, opportunity_id: str) -> None:
        self.connection.execute("update opportunities set review_sent_at = datetime('now') where id = ?", (opportunity_id,))
        self.connection.commit()

    def set_status(self, opportunity_id: str, status: str) -> None:
        self.connection.execute("update opportunities set status = ?, updated_at = datetime('now') where id = ?", (status, opportunity_id))
        self.connection.commit()

    def get_opportunity(self, opportunity_id: str) -> dict | None:
        row = self.connection.execute("select * from opportunities where id = ?", (opportunity_id,)).fetchone()
        return dict(row) if row else None

    def register_broadcast_chat(self, chat_id: int, title: str | None, chat_type: str) -> None:
        self.connection.execute(
            "insert into telegram_chats (chat_id, title, chat_type, is_active) values (?, ?, ?, 1) "
            "on conflict(chat_id) do update set title = excluded.title, chat_type = excluded.chat_type, is_active = 1",
            (chat_id, title, chat_type),
        )
        self.connection.commit()

    def broadcast_chats(self) -> list[dict]:
        return [dict(row) for row in self.connection.execute("select * from telegram_chats where is_active = 1")]

    def mark_posted(self, opportunity_id: str) -> None:
        self.connection.execute("update opportunities set status = 'POSTED', posted_at = datetime('now') where id = ?", (opportunity_id,))
        self.connection.commit()


class SupabaseStore:
    """Uses Supabase's PostgREST API; no paid database driver is needed."""
    def __init__(self, url: str, service_role_key: str) -> None:
        rest_url = url.rstrip("/") + "/rest/v1/"
        self.endpoint = rest_url + "opportunities"
        self.chats_endpoint = rest_url + "telegram_chats"
        self.headers = {"apikey": service_role_key, "Authorization": f"Bearer {service_role_key}", "Content-Type": "application/json"}

    def upsert(self, opportunity: Opportunity) -> str:
        record = opportunity.record()
        response = requests.post(
            self.endpoint,
            headers={**self.headers, "Prefer": "resolution=merge-duplicates,return=representation"},
            params={"on_conflict": "canonical_url"},
            json=record,
            timeout=30,
        )
        response.raise_for_status()
        return "created_or_updated"

    def review_queue(self, limit: int, unsent_only: bool = False) -> list[dict]:
        params = {"status": "eq.REVIEW", "order": "discovered_at.desc", "limit": limit, "select": "*"}
        if unsent_only:
            params["review_sent_at"] = "is.null"
        response = requests.get(self.endpoint, headers=self.headers, params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def mark_review_sent(self, opportunity_id: str) -> None:
        response = requests.patch(
            self.endpoint,
            headers=self.headers,
            params={"id": f"eq.{opportunity_id}"},
            json={"review_sent_at": datetime.now(timezone.utc).isoformat()},
            timeout=30,
        )
        response.raise_for_status()

    def set_status(self, opportunity_id: str, status: str) -> None:
        response = requests.patch(self.endpoint, headers=self.headers, params={"id": f"eq.{opportunity_id}"}, json={"status": status}, timeout=30)
        response.raise_for_status()

    def get_opportunity(self, opportunity_id: str) -> dict | None:
        response = requests.get(self.endpoint, headers=self.headers, params={"id": f"eq.{opportunity_id}", "select": "*"}, timeout=30)
        response.raise_for_status()
        rows = response.json()
        return rows[0] if rows else None

    def register_broadcast_chat(self, chat_id: int, title: str | None, chat_type: str) -> None:
        response = requests.post(
            self.chats_endpoint,
            headers={**self.headers, "Prefer": "resolution=merge-duplicates"},
            params={"on_conflict": "chat_id"},
            json={"chat_id": chat_id, "title": title, "chat_type": chat_type, "is_active": True},
            timeout=30,
        )
        response.raise_for_status()

    def broadcast_chats(self) -> list[dict]:
        response = requests.get(self.chats_endpoint, headers=self.headers, params={"is_active": "is.true", "select": "*"}, timeout=30)
        response.raise_for_status()
        return response.json()

    def mark_posted(self, opportunity_id: str) -> None:
        response = requests.patch(self.endpoint, headers=self.headers, params={"id": f"eq.{opportunity_id}"}, json={"status": "POSTED"}, timeout=30)
        response.raise_for_status()


def open_store(database_url: str, supabase_url: str | None, service_role_key: str | None) -> Store:
    if supabase_url and service_role_key:
        return SupabaseStore(supabase_url, service_role_key)
    store = SQLiteStore(database_url)
    store.initialize()
    return store
