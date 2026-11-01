"""Persistencia en SQLite: anuncios, histórico de precios, muestras de alquiler y ejecuciones."""
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id TEXT PRIMARY KEY,            -- '<fuente>:<código>'
    source TEXT NOT NULL,
    zone TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    location TEXT,
    price INTEGER,
    m2 INTEGER,
    rooms INTEGER,
    baths INTEGER,
    floor TEXT,
    description TEXT,
    photo TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    valuation TEXT                  -- JSON con el último cálculo
);
CREATE TABLE IF NOT EXISTS price_history (
    listing_id TEXT NOT NULL,
    day TEXT NOT NULL,
    price INTEGER NOT NULL,
    PRIMARY KEY (listing_id, day)
);
CREATE TABLE IF NOT EXISTS rent_samples (
    id TEXT PRIMARY KEY,
    zone TEXT NOT NULL,
    location TEXT,
    price INTEGER NOT NULL,
    m2 INTEGER NOT NULL,
    seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    day TEXT PRIMARY KEY,
    sent_at TEXT,
    stats TEXT
);
CREATE TABLE IF NOT EXISTS processed_emails (
    message_id TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS api_usage (
    month TEXT PRIMARY KEY,
    requests INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS geocache (
    query TEXT PRIMARY KEY,         -- dirección consultada a Nominatim
    lat REAL,                       -- NULL si no se encontró
    lon REAL
);
"""


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        # Migración: coordenadas que da el propio portal (Fotocasa); las bases antiguas no tienen estas columnas
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(listings)")}
        for col, typ in (("lat", "REAL"), ("lon", "REAL"), ("geo_prec", "TEXT")):
            if col not in cols:
                self.conn.execute(f"ALTER TABLE listings ADD COLUMN {col} {typ}")

    def close(self):
        self.conn.commit()
        self.conn.close()

    # ---- anuncios ---------------------------------------------------------
    def upsert_listing(self, l: dict, today: str) -> str:
        """Inserta o actualiza un anuncio. Devuelve 'new', 'drop' (bajada de precio) o 'seen'."""
        row = self.conn.execute("SELECT price FROM listings WHERE id=?", (l["id"],)).fetchone()
        status = "seen"
        if row is None:
            status = "new"
            self.conn.execute(
                """INSERT INTO listings (id, source, zone, url, title, location, price, m2, rooms, baths,
                   floor, description, photo, first_seen, last_seen, lat, lon, geo_prec)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (l["id"], l["source"], l["zone"], l["url"], l.get("title"), l.get("location"), l["price"],
                 l.get("m2"), l.get("rooms"), l.get("baths"), l.get("floor"), l.get("description"),
                 l.get("photo"), today, today, l.get("lat"), l.get("lon"), l.get("geo_prec")))
        else:
            # Solo es bajada si hay un precio mayor en un día anterior (el histórico guarda un precio por día)
            if row["price"] and l["price"] < row["price"] and self.conn.execute(
                    "SELECT 1 FROM price_history WHERE listing_id=? AND day<? AND price>?",
                    (l["id"], today, l["price"])).fetchone():
                status = "drop"
            # Mantener la descripción más larga (las alertas de email traen menos texto)
            self.conn.execute(
                """UPDATE listings SET price=?, last_seen=?, title=COALESCE(?, title),
                   location=COALESCE(?, location), m2=COALESCE(?, m2), rooms=COALESCE(?, rooms),
                   baths=COALESCE(?, baths), floor=COALESCE(?, floor), photo=COALESCE(?, photo),
                   description=CASE WHEN length(COALESCE(?, '')) > length(COALESCE(description, ''))
                                    THEN ? ELSE description END,
                   lat=COALESCE(?, lat), lon=COALESCE(?, lon), geo_prec=COALESCE(?, geo_prec)
                   WHERE id=?""",
                (l["price"], today, l.get("title"), l.get("location"), l.get("m2"), l.get("rooms"),
                 l.get("baths"), l.get("floor"), l.get("photo"), l.get("description"), l.get("description"),
                 l.get("lat"), l.get("lon"), l.get("geo_prec"), l["id"]))
        self.conn.execute("INSERT OR REPLACE INTO price_history VALUES (?,?,?)", (l["id"], today, l["price"]))
        return status

    def set_valuation(self, listing_id: str, valuation: dict):
        self.conn.execute("UPDATE listings SET valuation=? WHERE id=?",
                          (json.dumps(valuation, ensure_ascii=False), listing_id))

    def active_listings(self, today: str, days: int = 21) -> list[dict]:
        """Anuncios vistos en los últimos `days` días (los antiguos se consideran retirados)."""
        since = (date.fromisoformat(today) - timedelta(days=days)).isoformat()
        rows = self.conn.execute("SELECT * FROM listings WHERE last_seen >= ?", (since,)).fetchall()
        return [dict(r) for r in rows]

    def price_history(self, listing_id: str) -> list[tuple[str, int]]:
        rows = self.conn.execute("SELECT day, price FROM price_history WHERE listing_id=? ORDER BY day",
                                 (listing_id,)).fetchall()
        return [(r["day"], r["price"]) for r in rows]

    # ---- alquileres comparables ------------------------------------------
    def add_rent_sample(self, s: dict, today: str):
        self.conn.execute("INSERT OR REPLACE INTO rent_samples VALUES (?,?,?,?,?,?)",
                          (s["id"], s["zone"], s.get("location"), s["price"], s["m2"], today))

    def rent_samples(self, zone: str, today: str, days: int = 120) -> list[dict]:
        since = (date.fromisoformat(today) - timedelta(days=days)).isoformat()
        rows = self.conn.execute("SELECT * FROM rent_samples WHERE zone=? AND seen >= ?", (zone, since)).fetchall()
        return [dict(r) for r in rows]

    # ---- ejecuciones -------------------------------------------------------
    def already_sent(self, day: str) -> bool:
        row = self.conn.execute("SELECT sent_at FROM runs WHERE day=?", (day,)).fetchone()
        return bool(row and row["sent_at"])

    def mark_sent(self, day: str, sent_at: str, stats: dict):
        self.conn.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?)", (day, sent_at, json.dumps(stats)))

    def is_first_run(self) -> bool:
        return self.conn.execute("SELECT COUNT(*) FROM runs WHERE sent_at IS NOT NULL").fetchone()[0] == 0

    # ---- emails procesados / cuota API ------------------------------------
    def email_processed(self, message_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM processed_emails WHERE message_id=?", (message_id,)).fetchone() is not None

    def mark_email_processed(self, message_id: str):
        self.conn.execute("INSERT OR IGNORE INTO processed_emails VALUES (?)", (message_id,))

    def api_requests(self, month: str) -> int:
        row = self.conn.execute("SELECT requests FROM api_usage WHERE month=?", (month,)).fetchone()
        return row["requests"] if row else 0

    def add_api_request(self, month: str):
        self.conn.execute("INSERT INTO api_usage VALUES (?, 1) ON CONFLICT(month) DO UPDATE SET requests=requests+1",
                          (month,))

    # ---- geocodificación (mapa) --------------------------------------------
    def geocache_get(self, query: str) -> tuple[float | None, float | None] | None:
        """(lat, lon); (None, None) si ya se consultó sin resultado; None si nunca se consultó."""
        row = self.conn.execute("SELECT lat, lon FROM geocache WHERE query=?", (query,)).fetchone()
        return (row["lat"], row["lon"]) if row else None

    def geocache_put(self, query: str, point: tuple[float, float] | None):
        self.conn.execute("INSERT OR REPLACE INTO geocache VALUES (?,?,?)",
                          (query, *(point if point else (None, None))))
