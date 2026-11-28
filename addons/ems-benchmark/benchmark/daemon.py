"""Entry point: nightly benchmark of yesterday (+ backfill), then push.

Konfiguration v0.4.0: deutsch (inkl. Umlaute) im Supervisor-Schema. Fixwerte,
die kein Nutzer anfassen soll: Zeitzone (Container-TZ Europe/Berlin),
Sensorabdeckungs-Gate 90 %, Verschleiß-Aufschlag 5 ct/kWh.
API-URL, HA-URL, Port und App-Version sind Konstanten — nicht konfigurierbar.
Der Push startet zu einem zufälligen Zeitpunkt zwischen 00:30 und 04:30,
damit bei vielen Haushalten nicht alle gleichzeitig auf dem Server stehen.
"""
import datetime as dtm
import os
import random
import re
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from benchmark import ha, quality, store, push, userconfig
from benchmark.manufacturers import MANUFACTURERS
from benchmark.model import BenchConfig, BatteryConfig, DeviceLimits, DayData, run_day

BENCHMARK_VERSION = "0.8.5"
DATA_DIR = os.environ.get("BENCH_DATA", "/data")
OPTIONS_FILE = os.environ.get("BENCH_OPTIONS", "/data/options.json")

# Fest im Code (Integritaet + Bedienbarkeit):
API_URL = os.environ.get("BENCH_API_URL", "http://192.168.2.213:8090")
HA_URL = os.environ.get("BENCH_HA_URL", "http://supervisor/core/api")
DASHBOARD_PORT = int(os.environ.get("BENCH_PORT", "8091"))
KAPPA_EUR_PER_KWH = 0.05      # Speicher-Verschleiß-Aufschlag
MIN_COVERAGE = 0.90           # unter 90 % Messabdeckung: Tag verwerfen, nicht pushen
PUSH_WINDOW = (0, 30, 4, 30)  # zufaelliges Push-Fenster 00:30–04:30


def local_tz():
    """Zeitzone des Containers (HAOS: Europe/Berlin, inkl. Sommerzeit)."""
    return dtm.datetime.now().astimezone().tzinfo


def _norm_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())

_HEMS_INDEX = {_norm_key(m): m for m in MANUFACTURERS["HEMS"]}


def norm_hems(name: str) -> str:
    """HEMS-Hersteller validieren: normalisiert gegen Liste, sonst 'eigene'."""
    key = _norm_key(name)
    if not key:
        return ""
    return _HEMS_INDEX.get(key, "eigene")


def load_options():
    # Supervisor-Optionen + Nutzer-Overlay (Dashboard-Einstellungen, /data)
    return userconfig.load(DATA_DIR, OPTIONS_FILE)


def _run_opts_v6(raw: dict) -> dict:
    """Deutsche Aufklapp-Menues -> internes Schema.

    v0.7: Zaehler und Wechselrichter sind eigene Menues; die Werte liegen bei
    aelteren Installationen noch flach im HEMS-Menue (Rueckwaertskompatibilitaet).
    """
    def s(v):
        return str(v or "").strip()
    h = raw.get("HEMS") or {}
    zh = raw.get("Zähler") or raw.get("Zaehler") or {}
    wr = raw.get("Wechselrichter") or {}
    sp = raw.get("Speicher") or {}
    wp = raw.get("Wärmepumpe") or raw.get("Waermepumpe") or {}
    wb = raw.get("Wallbox") or {}
    tf = raw.get("Tarif") or {}
    res = {
        "10 Sekunden": 10, "10 Sekunden (rechenintensiv)": 10,
        "1 Minute": 60, "5 Minuten": 300,
    }.get(s(raw.get("Auflösung", raw.get("Aufloesung"))), 60)
    modus = s(tf.get("Einspeisung"))
    fix = tf.get("Feste Vergütung in €/kWh (nur bei Einspeisevergütung)")
    eff = float(sp.get("Wirkungsgrad in %", 100))
    if eff > 1.5:
        eff /= 100.0
    return {
        "resolution_sec": max(10, min(int(res), 600)),
        "min_coverage": MIN_COVERAGE,
        "kappa": KAPPA_EUR_PER_KWH,
        "price_exp_fixed": ("" if modus == "Direktvermarktung"
                            else ("" if fix is None else str(fix))),
        "entities": {
            "pv": s(wr.get("PV Leistung") or h.get("PV Leistung")),
            "grid_net": s(zh.get("Netz gesamt (negativ = Einspeisung)")
                          or h.get("Netz gesamt (negativ = Einspeisung)")),
            "grid_import": s(zh.get("Netz Bezug (nur bei getrennten Sensoren)")
                             or h.get("Netz Bezug (nur bei getrennten Sensoren)")),
            "grid_export": s(zh.get("Netz Einspeisung (nur bei getrennten Sensoren)")
                             or h.get("Netz Einspeisung (nur bei getrennten Sensoren)")),
            "house": s(zh.get("Hausverbrauch") or h.get("Hausverbrauch")),
            "hp": s(wp.get("Sensor")),
            "wb": s(wb.get("Sensor")),
            "wb_connected": s(wb.get("Auto anwesend (Binärsensor)")),
            "price_imp": s(tf.get("Preis Bezug (Sensor)")),
            "price_exp": (s(tf.get("Sensor Einspeisung (nur bei Direktvermarktung)"))
                          if modus == "Direktvermarktung" else ""),
        },
        "battery": {
            "capacity_kwh": float(sp.get("Kapazität in kWh", 0) or 0),
            "p_charge_max": float(sp.get("Ladeleistung in kW", 0) or 0),
            "p_discharge_max": float(sp.get("Entladeleistung in kW", 0) or 0),
            "eta": eff,
        },
        "hp": {"p_min": float(wp.get("Mindestleistung in kW", 0.5) or 0.5),
               "p_max": float(wp.get("Höchstleistung in kW", 2.0) or 2.0)},
        "wallbox": {"p_min": float(wb.get("Mindestleistung in kW", 1.0) or 1.0),
                    "p_max": float(wb.get("Höchstleistung in kW", 11.0) or 11.0)},
        "devices": {
            "ems_product": norm_hems(s(h.get("Hersteller"))),
            "battery_brand": s(sp.get("Hersteller")),
            "pv_brand": s(wr.get("Hersteller") or h.get("Wechselrichter")),
            "hp_brand": s(wp.get("Hersteller")),
            "wallbox_brand": s(wb.get("Hersteller")),
        },
    }


def run_opts(raw: dict) -> dict:
    """Deutsche Options-Schluessel -> internes Schema (mit Alt-Fallbacks)."""
    if any(k in raw for k in ("HEMS", "Speicher", "Wärmepumpe", "Waermepumpe",
                              "Wallbox", "Tarif")):
        return _run_opts_v6(raw)
    g = lambda k, d=None: raw.get(k, d)
    sr = g("Sensoren") or g("sensoren") or g("entities") or {}
    if isinstance(sr, list):
        # MariaDB-artige Zeilen: [{Funktion: ..., Entität: ...}, ...]
        s = {}
        for e in sr:
            if isinstance(e, dict) and e.get("Funktion") and e.get("Entität"):
                s[e["Funktion"]] = e["Entität"]
    else:
        s = sr
    b = g("Batterie") or g("batterie") or g("battery") or {}
    gr = g("Grenzwerte") or g("grenzwerte") or {}
    d = g("Geräte") or g("geräte") or g("devices") or {}
    e = g("Einspeisung") or {}

    def pick(*prefixes, dflt=""):
        """Vorzeichen-basiert: 'Netz Bezug' trifft auch
        'Netz Bezug (nur bei getrennten Sensoren)'."""
        for p in prefixes:
            np_ = _norm_key(p)
            for k, v in s.items():
                if v and _norm_key(k).startswith(np_):
                    return v
        return dflt

    # Auflösung: Auswahl-String (neu) > Sekunden > Minuten deutsch > Minuten alt
    RES = {"10 Sekunden": 10, "1 Minute": 60, "5 Minuten": 300}
    raw_res = g("Auflösung", g("Auflösung_in_Sekunden"))
    if isinstance(raw_res, str) and raw_res in RES:
        res = RES[raw_res]
    elif raw_res is None:
        am = g("aufloesung_minuten")
        res = int(am) * 60 if am else None
        if res is None:
            res = int(g("resolution_min", 1) or 1) * 60
    else:
        res = int(raw_res)
    res = max(10, min(int(res), 600))

    # Einspeisevergütung: Sensor (Direktvermarktung) > fixer Wert > ""
    # (Fallback Bezugspreis, geschieht in build_day_traces).
    e_fix = str(e.get("Feste Vergütung in €/kWh",
                      g("preis_einspeisung_fix_eur",
                        g("price_exp_fixed", ""))) or "").strip()
    e_sensor = str(e.get("Sensor", s.get("Preis Einspeisung", "")) or "").strip()

    eff = b.get("Wirkungsgrad in %",
               b.get("wirkungsgrad_prozent", b.get("eta", 100)))
    eff = float(eff)
    if eff > 1.5:
        eff /= 100.0

    return {
        "resolution_sec": res,
        "min_coverage": MIN_COVERAGE,
        "kappa": KAPPA_EUR_PER_KWH,
        "price_exp_fixed": e_fix,
        "entities": {
            "pv": pick("PV Leistung", "pv_leistung", "pv"),
            "grid_net": pick("Netz gesamt", "netz_gesamt", "grid_net"),
            "grid_import": pick("Netz Bezug", "netz_bezug_alternativ", "grid_import"),
            "grid_export": pick("Netz Einspeisung", "netz_einspeisung_alternativ", "grid_export"),
            "house": pick("Hausverbrauch", "haus_verbrauch", "house"),
            "hp": pick("Wärmepumpe Leistung", "waermepumpe", "hp"),
            "wb": pick("Wallbox Leistung", "wallbox", "wb"),
            "wb_connected": pick("Wallbox verbunden", "wallbox_verbunden", "wb_connected"),
            "price_imp": pick("Preis Bezug", "preis_bezug", "price_imp"),
            "price_exp": e_sensor or pick("Preis Einspeisung", "preis_einspeisung"),
        },
        "battery": {
            "capacity_kwh": float(b.get("Kapazität in kWh", b.get("kapazitaet_kwh", b.get("capacity_kwh", 0)))),
            "p_charge_max": float(b.get("Ladeleistung in kW", b.get("ladeleistung_kw", b.get("p_charge_max", 0)))),
            "p_discharge_max": float(b.get("Entladeleistung in kW", b.get("entladeleistung_kw", b.get("p_discharge_max", 0)))),
            "eta": eff,
        },
        "hp": {"p_min": float(gr.get("Wärmepumpe mindestens in kW", gr.get("waermepumpe_min_kw", 0.5))),
               "p_max": float(gr.get("Wärmepumpe höchstens in kW", gr.get("waermepumpe_max_kw", 2.0)))},
        "wallbox": {"p_min": float(gr.get("Wallbox mindestens in kW", gr.get("wallbox_min_kw", 1.0))),
                    "p_max": float(gr.get("Wallbox höchstens in kW", gr.get("wallbox_max_kw", 11.0)))},
        "devices": {
            "ems_product": norm_hems(d.get("HEMS", d.get("hersteller_hems", d.get("ems_product", "")))),
            "battery_brand": d.get("Speicher", d.get("hersteller_speicher", d.get("battery_brand", ""))),
            "pv_brand": d.get("Wechselrichter", d.get("hersteller_wechselrichter", d.get("pv_brand", ""))),
            "hp_brand": d.get("Wärmepumpe", d.get("hersteller_waermepumpe", d.get("hp_brand", ""))),
            "wallbox_brand": d.get("Wallbox", d.get("hersteller_wallbox", d.get("wallbox_brand", ""))),
        },
    }


def get_or_create_household_id():
    p = os.path.join(DATA_DIR, "household_id")
    if os.path.exists(p):
        return open(p).read().strip()
    import secrets
    hid = secrets.token_hex(8)
    with open(p, "w") as f:
        f.write(hid)
    return hid


def run_one_day(con, o: dict, day: dtm.date, tz):
    res_sec = o["resolution_sec"]
    n = 24 * 3600 // res_sec
    dt_h = res_sec / 3600.0
    midnight = dtm.datetime.combine(day, dtm.time(0, 0), tzinfo=tz)
    start = midnight.astimezone(dtm.timezone.utc)
    end = (midnight + dtm.timedelta(days=1)).astimezone(dtm.timezone.utc)

    entities = [v for v in o["entities"].values() if v]
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    units = ha.fetch_units(HA_URL, token, entities)
    hist = ha.fetch_history(HA_URL, token, entities, start, end)
    tr = ha.build_day_traces(hist, o["entities"], start, n, dt_h, units)

    # Einspeisevergütung: Preis-Sensor > fixer Wert > Bezugspreis (Fallback
    # schon in build_day_traces). Der Bezugspreis als Fallback ist bewusst
    # streng: er legt die Optimum-Latte am höchsten aus.
    fx = o["price_exp_fixed"]
    if fx:
        tr["price_exp"] = np.full(n, float(fx))

    ok, flags = quality.quality_check(tr, dt_h, min_coverage=o["min_coverage"])
    if not ok:
        print(f"{day}: SKIPPED by quality gate {flags}")
        store.save_skipped(con, day.isoformat(), BENCHMARK_VERSION, res_sec, flags)
        return None

    dd = DayData(
        pv=tr["pv"], base_load=tr["base_load"],
        price_imp=tr["price_imp"], price_exp=tr["price_exp"],
        hp_energy_kwh=float(np.sum(tr["hp"])) * dt_h,
        wb_energy_kwh=float(np.sum(tr["wb"])) * dt_h,
        wb_window=tr["window"],
        grid_import=tr["imp"], grid_export=tr["exp"], dt_h=dt_h)
    b = o["battery"]
    cfg = BenchConfig(
        battery=BatteryConfig(
            capacity_kwh=float(b["capacity_kwh"]),
            p_charge_max=float(b["p_charge_max"]),
            p_discharge_max=float(b["p_discharge_max"]),
            eta=float(b.get("eta", 1.0))),
        hp=DeviceLimits(p_min=o["hp"]["p_min"], p_max=o["hp"]["p_max"]),
        wb=DeviceLimits(p_min=o["wallbox"]["p_min"], p_max=o["wallbox"]["p_max"]),
        kappa=o["kappa"])

    t0 = time.time()
    result = run_day(dd, cfg)
    print(f"{day}: optimum={result.oracle_cost:.2f}€ real={result.real_cost:.2f}€ "
          f"index={result.quotient:.3f} relaxed={result.relaxed} "
          f"({time.time()-t0:.1f}s) {flags}")
    store.save_day(con, day.isoformat(), BENCHMARK_VERSION, res_sec, result, flags)
    return result


def push_pending(con, hhid, o):
    """Alle noch nicht gemeldeten Tage senden. Payload fest zusammengestellt:
    NUR Index + validierte Hersteller + feste App-Version (Datenschutz)."""
    for r in store.pending(con):
        (date, ver, res, oc, rc, q, thr, rel, fl) = r
        dev = o["devices"]
        slim = {k: v for k, v in
                (("ems_product", dev["ems_product"]),
                 ("battery_brand", dev["battery_brand"]),
                 ("pv_brand", dev["pv_brand"]),
                 ("hp_brand", dev["hp_brand"]),
                 ("wallbox_brand", dev["wallbox_brand"])) if v}
        payload = {
            "benchmark_version": BENCHMARK_VERSION,
            "household_id": hhid, "date": date,
            "quotient": q, "devices": slim,
        }
        try:
            resp = push.push_day(API_URL, payload)
            store.mark_pushed(con, [date])
            print(f"{date}: pushed, server says {resp}")
        except Exception as e:
            print(f"{date}: push failed: {e}")
            break


def main():
    o = run_opts(load_options())
    tz = local_tz()
    os.makedirs(DATA_DIR, exist_ok=True)
    con = store.store(os.path.join(DATA_DIR, "bench.db"))
    hhid = get_or_create_household_id()

    if len(sys.argv) > 1:
        days = [dtm.date.fromisoformat(sys.argv[1])]
        loop = False
    else:
        days = [dtm.date.today() - dtm.timedelta(days=1)]
        loop = True

    if loop:
        from benchmark import web
        web.start_server(DASHBOARD_PORT,
                         os.path.join(DATA_DIR, "bench.db"),
                         BENCHMARK_VERSION, tz)

    while True:
        # Im Dauerbetrieb den Vortag JEDEN Durchlauf neu bestimmen —
        # sonst wird nach dem Aufwachen ewig der Starttag wiederholt.
        if loop:
            days = [dtm.datetime.now(tz).date() - dtm.timedelta(days=1)]
        for day in days:
            try:
                # Konfiguration pro Tag frisch: Dashboard-Aenderungen wirken
                # ohne Daemon-Neustart.
                run_one_day(con, run_opts(load_options()), day, tz)
            except Exception as e:
                print(f"{day}: ERROR {e}")

        if not loop:
            push_pending(con, hhid, run_opts(load_options()))
            break

        # Nachpruefung vor dem Push: spaet eingetroffene Messdaten
        # beruecksichtigen — wurde der Tag im Fruehlauf (00:05) verworfen oder
        # gar nicht angelegt, wird er hier ein zweites Mal bewertet.
        yday = dtm.datetime.now(tz).date() - dtm.timedelta(days=1)
        row = store.day_row(con, yday.isoformat())
        if row is None or row[2] is None:
            print(f"{yday}: Nachpruefung — Daten unvollstaendig, neuer Versuch")
            try:
                run_one_day(con, run_opts(load_options()), yday, tz)
            except Exception as e:
                print(f"{yday}: Nachpruefung ERROR {e}")

        # Push zu zufälligem Zeitpunkt im Fenster 00:30–04:30
        # (Lastverteilung des Community-Servers bei vielen Haushalten).
        now = dtm.datetime.now(tz)
        h0, m0, h1, m1 = PUSH_WINDOW
        lo = now.replace(hour=h0, minute=m0, second=0, microsecond=0)
        hi = now.replace(hour=h1, minute=m1, second=0, microsecond=0)
        span = max((hi - lo).total_seconds(), 60)
        push_at = lo + dtm.timedelta(seconds=random.uniform(0, span))
        if push_at <= now:
            push_at = now + dtm.timedelta(seconds=random.uniform(60, 600))
        wait = (push_at - now).total_seconds()
        if wait > 0:
            print(f"Push geplant für {push_at:%H:%M} Uhr ({wait/60:.0f} min)")
            time.sleep(wait)
        push_pending(con, hhid, run_opts(load_options()))

        # bis ~00:05 morgens (plus Jitter, damit Rechner nicht synchron rechnen).
        # Der fruehe Zeitpunkt ist moeglich, weil die History-Abdeckung des
        # Vortags um 00:05 bereits abgeschlossen ist; sollte der Tag trotzdem
        # verworfen werden, greift die Nachpruefung vor dem naechsten Push.
        now = dtm.datetime.now(tz)
        nxt = (now + dtm.timedelta(days=1)).replace(
            hour=0, minute=5, second=0, microsecond=0) + dtm.timedelta(
            seconds=random.uniform(0, 240))
        time.sleep(max((nxt - now).total_seconds(), 60))


if __name__ == "__main__":
    main()
