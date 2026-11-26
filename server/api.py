#!/usr/bin/env python3
"""API-Server für die EMS-Benchmark-Community.

Empfängt pro Haushalt und Tag EIN Aggregat (niemals Rohdaten). stdlib-only,
SQLite. Läuft auf dem KI-Server. Liefert zusätzlich die Community-Website
(index.html) auf Pfad /.

Regeln (mit Kai festgelegt):
- devices.ems_product MUSS ein HEMS aus manufacturers.json sein
  (Groß-/Kleinschreibung egal, normalisiert auf den Listennamen).
- Max. 5 Pushes pro household_id pro Kalendertag (Europe/Berlin).
- Stromtarife und HEMS-Softwareversionen werden ignoriert; übermittelt wird
  als Version nur die ems-benchmark-App (benchmark_version).

Endpoints:
  POST /api/v1/daily       -> ein Tagesergebnis speichern
  GET  /api/v1/leaderboard -> Ø-Index pro HEMS-Hersteller (Website)
  GET  /api/v1/stats       -> (alt) Gruppierung nach Produkt+Version
  GET  /api/v1/healthz
  GET  /                   -> Community-Website
"""
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("BENCH_API_DB", os.path.join(HERE, "results.db"))
SITE = os.path.join(HERE, "index.html")
PORT = int(os.environ.get("PORT", "8090"))
MAX_PUSHES_PER_DAY = 5

with open(os.path.join(HERE, "manufacturers.json"), encoding="utf-8") as f:
    MANUFACTURERS = json.load(f)
HEMS_LOOKUP = {m.lower(): m for m in MANUFACTURERS["HEMS"]}

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  received_at REAL,
  benchmark_version TEXT NOT NULL,
  household_id TEXT NOT NULL,
  date TEXT NOT NULL,
  resolution_min INTEGER,
  quotient REAL,
  oracle_cost REAL,
  real_cost REAL,
  battery_throughput_kwh REAL,
  relaxed INTEGER,
  quality TEXT,
  devices TEXT,
  UNIQUE(benchmark_version, household_id, date)
);
CREATE INDEX IF NOT EXISTS idx_prod ON daily_results(benchmark_version);
CREATE TABLE IF NOT EXISTS push_log (
  household_id TEXT NOT NULL,
  day TEXT NOT NULL,
  n INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (household_id, day)
);
"""


def con():
    c = sqlite3.connect(DB)
    c.executescript(SCHEMA)
    return c


def berlin_day():
    # feste UTC+1-Aggregation: reicht für ein Tageslimit (Edge CEST ±1 h okay)
    return (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%d")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path in ("/", "/index.html"):
            try:
                with open(SITE, "rb") as f:
                    body = f.read()
            except OSError:
                return self._send(503, {"error": "website nicht gefunden"})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        if path.startswith("/api/v1/healthz"):
            return self._send(200, {"ok": True, "ts": time.time()})
        if path.startswith("/api/v1/leaderboard"):
            with con() as c:
                rows = c.execute(
                    """SELECT json_extract(devices,'$.ems_product') AS hems,
                              COUNT(*) days, COUNT(DISTINCT household_id) homes,
                              AVG(quotient) mean_q, MIN(quotient) min_q,
                              MAX(quotient) max_q, MAX(date) last_date
                       FROM daily_results
                       GROUP BY hems ORDER BY mean_q DESC""").fetchall()
            lb = [{"hems": r[0], "days": r[1], "homes": r[2],
                   "mean_index": r[3], "min_index": r[4], "max_index": r[5],
                   "last_date": r[6]} for r in rows]
            return self._send(200, {"leaderboard": lb})
        if path.startswith("/api/v1/stats"):
            with con() as c:
                rows = c.execute(
                    """SELECT json_extract(devices,'$.ems_product') AS product,
                              json_extract(devices,'$.ems_version') AS ver,
                              COUNT(*) days, COUNT(DISTINCT household_id) homes,
                              AVG(quotient) mean_q, MIN(quotient) min_q,
                              MAX(quotient) max_q
                       FROM daily_results
                       GROUP BY product, ver ORDER BY mean_q DESC""").fetchall()
            stats = [dict(zip(
                ["product", "version", "days", "homes", "mean_quotient",
                 "min_quotient", "max_quotient"], r)) for r in rows]
            return self._send(200, {"stats": stats})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.startswith("/api/v1/daily"):
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            p = json.loads(self.rfile.read(n))
        except Exception as e:
            return self._send(400, {"error": f"bad json: {e}"})
        required = ("benchmark_version", "household_id", "date", "quotient")
        missing = [k for k in required if k not in p]
        if missing:
            return self._send(400, {"error": f"missing fields: {missing}"})
        # Hersteller-Pflicht: nur HEMS von der Liste, normalisiert
        dev = p.get("devices") or {}
        hems = HEMS_LOOKUP.get(str(dev.get("ems_product", "")).strip().lower())
        if not hems:
            return self._send(400, {"error": "ems_product nicht auf der "
                                             "HEMS-Liste (manufacturers.json)"})
        # Geräte-Metadaten abspecken: Tarife & HEMS-Softwareversion sind egal,
        # relevant sind Hersteller + ems-benchmark-App-Version (benchmark_version).
        slim = {k: dev[k] for k in
                ("ems_product", "battery_brand", "pv_brand", "hp_brand",
                 "wallbox_brand") if dev.get(k)}
        slim["ems_product"] = hems
        try:
            with con() as c:
                # Rate-Limit: max. MAX_PUSHES_PER_DAY Pushes pro Haushalt/Tag
                day = berlin_day()
                cur = c.execute("SELECT n FROM push_log WHERE household_id=? AND day=?",
                                (p["household_id"], day)).fetchone()
                if cur and cur[0] >= MAX_PUSHES_PER_DAY:
                    return self._send(429, {"error": f"max. {MAX_PUSHES_PER_DAY} "
                                                     "Pushes pro Tag erreicht"})
                c.execute("INSERT INTO push_log(household_id, day, n) VALUES(?,?,1) "
                          "ON CONFLICT DO UPDATE SET n=n+1",
                          (p["household_id"], day))
                c.execute(
                    """INSERT OR REPLACE INTO daily_results
                       (received_at, benchmark_version, household_id, date,
                        resolution_min, quotient, oracle_cost, real_cost,
                        battery_throughput_kwh, relaxed, quality, devices)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (time.time(), p["benchmark_version"], p["household_id"],
                     p["date"], p.get("resolution_min"), p["quotient"],
                     p.get("oracle_cost"), p.get("real_cost"),
                     p.get("battery_throughput_kwh"), int(bool(p.get("relaxed"))),
                     json.dumps(p.get("quality", {})), json.dumps(slim)))
            return self._send(200, {"ok": True, "stored": p["date"]})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    def log_message(self, fmt, *args):
        print(f"[api] {self.address_string()} {fmt % args}")


if __name__ == "__main__":
    print(f"EMS-Benchmark API on :{PORT}, db={DB}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
