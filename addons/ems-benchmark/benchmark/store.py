"""Local SQLite: results + push queue (offline-safe)."""
import json
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS days (
  date TEXT PRIMARY KEY,
  benchmark_version TEXT, resolution_min INTEGER,
  oracle_cost REAL, real_cost REAL, quotient REAL,
  battery_throughput_kwh REAL, relaxed INTEGER,
  quality_flags TEXT, pushed INTEGER DEFAULT 0,
  created_at REAL
);
"""


def store(path):
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def save_day(con, date, version, resolution_sec, result, flags):
    con.execute(
        "INSERT OR REPLACE INTO days VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (date, version, resolution_sec,
         result.oracle_cost, result.real_cost, result.quotient,
         result.battery_throughput_kwh, int(result.relaxed),
         json.dumps(flags), 0, time.time()))
    con.commit()


def save_skipped(con, date, version, resolution_sec, flags):
    """Qualitätsgate verworfen: Null-Werte, pushed=1 (wird NIEMALS gemeldet),
    damit das Dashboard transparent vorwarnen kann."""
    con.execute(
        "INSERT OR REPLACE INTO days VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (date, version, resolution_sec, None, None, None, None, 0,
         json.dumps(flags), 1, time.time()))
    con.commit()


def day_row(con, date):
    """(oracle_cost, real_cost, quotient) fuer den Tag, None wenn kein Eintrag."""
    return con.execute(
        "SELECT oracle_cost, real_cost, quotient FROM days WHERE date=?",
        (date,)).fetchone()


def pending(con):
    return con.execute(
        "SELECT date, benchmark_version, resolution_min, oracle_cost, real_cost,"
        " quotient, battery_throughput_kwh, relaxed, quality_flags"
        " FROM days WHERE pushed=0 ORDER BY date").fetchall()


def mark_pushed(con, dates):
    con.executemany("UPDATE days SET pushed=1 WHERE date=?",
                    [(d,) for d in dates])
    con.commit()
