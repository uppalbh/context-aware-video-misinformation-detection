import json
import sqlite3
import time

import httpx

from app.config import Settings


class Store:
    """Server-only persistence. Call from worker threads, never the ASGI event loop."""

    def __init__(self, cfg: Settings):
        self.cfg = cfg
        if cfg.store == "sqlite":
            self.path = cfg.data_dir / "analyses.sqlite3"
            with sqlite3.connect(self.path) as con:
                con.execute("CREATE TABLE IF NOT EXISTS analyses (id TEXT PRIMARY KEY, body TEXT)")
        elif cfg.store == "supabase":
            if not cfg.supabase_url.startswith("https://") or not cfg.supabase_key:
                raise ValueError(
                    "Configure SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY or STORE=sqlite"
                )
        else:
            raise ValueError("STORE must be supabase or sqlite")

    def request(self, method, params=None, body=None):
        with httpx.Client(timeout=15) as client:
            response = client.request(
                method,
                self.cfg.supabase_url.rstrip("/") + "/rest/v1/analyses",
                params=params,
                json=body,
                headers={
                    "apikey": self.cfg.supabase_key,
                    "Authorization": "Bearer " + self.cfg.supabase_key,
                    "Prefer": "return=representation",
                },
            )
        response.raise_for_status()
        return response.json()

    def save(self, row, new=False):
        row["updated_at"] = time.time()
        if self.cfg.store == "sqlite":
            with sqlite3.connect(self.path) as con:
                con.execute(
                    "INSERT INTO analyses VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                    (row["id"], json.dumps(row)),
                )
        elif new:
            self.request("POST", body=row)
        else:
            self.request("PATCH", {"id": "eq." + row["id"]}, row)

    def all(self):
        if self.cfg.store == "sqlite":
            with sqlite3.connect(self.path) as con:
                return [json.loads(r[0]) for r in con.execute("SELECT body FROM analyses")]
        return self.request("GET", {"order": "created_at.asc", "limit": "1000"})

    def get(self, analysis_id):
        if self.cfg.store == "sqlite":
            with sqlite3.connect(self.path) as con:
                row = con.execute("SELECT body FROM analyses WHERE id=?", (analysis_id,)).fetchone()
            return json.loads(row[0]) if row else None
        rows = self.request("GET", {"id": "eq." + analysis_id})
        return rows[0] if rows else None

    def delete(self, analysis_id):
        if self.cfg.store == "sqlite":
            with sqlite3.connect(self.path) as con:
                con.execute("DELETE FROM analyses WHERE id=?", (analysis_id,))
        else:
            self.request("DELETE", {"id": "eq." + analysis_id})
