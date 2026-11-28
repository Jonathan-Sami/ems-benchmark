"""MQTT-Bereitstellung der wichtigsten Ergebnisse (HA-Discovery, QoS0).

Minimaler MQTT-3.1.1-Client auf reiner Python-Stdlib — keine Zusatz-
Abhaengigkeiten im Add-on. Supervisor injiziert MQTT_HOST/PORT/USERNAME/
PASSWORD, wenn config.yaml `mqtt: true` deklariert ist; falls MQTT_HOST
fehlt, wird `core-mosquitto` (Standard-Hostname des HA-Mosquitto-Add-ons)
versucht. Publiziert 20 Sensoren (5 Kennzahlen x 4 Perioden) retained.
"""
import datetime as dtm
import json
import os
import socket
import sqlite3
import struct

BASE_TOPIC = "homeassistant/sensor"
DEVICE = {"identifiers": ["ems_benchmark"], "name": "EMS Benchmark",
          "manufacturer": "Community EMS-Benchmark",
          "model": "EMS-Benchmark Add-on"}

# key, Label, Einheit, device_class, state_class
METRICS = [
    ("real_kosten", "Real Kosten", "€", "monetary", "total"),
    ("opt_kosten", "Optimum Kosten", "€", "monetary", "total"),
    ("sparpotential", "Sparpotential", "€", "monetary", "total"),
    ("autarkie", "Autarkie", "%", None, None),
    ("eigenverbrauch", "Eigenverbrauch", "%", None, None),
]
PERIODS = [("tag", "Tag"), ("woche", "Woche"), ("monat", "Monat"), ("jahr", "Jahr")]


def _enc_str(s):
    b = s.encode("utf-8")
    return struct.pack(">H", len(b)) + b


class Client:
    def __init__(self, host, port, user="", pw=""):
        self.s = socket.create_connection((host, port), timeout=10)
        var = b"\x00\x04MQTT\x04"
        rem = _enc_str("ems-benchmark")
        flags = 0x02  # clean session
        if user:
            flags |= 0xC0
            rem += _enc_str(user) + _enc_str(pw)
        self._packet(0x10, var + bytes([flags]) + struct.pack(">H", 60) + rem)
        hdr = self.s.recv(4)
        if len(hdr) < 4 or hdr[0] >> 4 != 2:
            raise RuntimeError("kein CONNACK empfangen")
        if hdr[3] != 0:
            raise RuntimeError(f"CONNACK Fehlercode {hdr[3]}")

    def _packet(self, ptype, rem):
        out = bytes([ptype])
        n = len(rem)
        while True:
            d = n % 128
            n //= 128
            out += bytes([d | (0x80 if n else 0)])
            if not n:
                break
        self.s.sendall(out + rem)

    def publish(self, topic, payload, retain=True):
        if not isinstance(payload, (str, bytes)):
            payload = json.dumps(payload, ensure_ascii=False)
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        self._packet(0x30 | (0x01 if retain else 0), _enc_str(topic) + payload)

    def close(self):
        try:
            self.s.sendall(b"\xe0\x00")
        except Exception:
            pass
        self.s.close()


def period_sums(con):
    """Kennzahlen je Periode, verankert auf den letzten bewerteten Tag."""
    row = con.execute(
        "SELECT MAX(date) FROM days WHERE quotient IS NOT NULL").fetchone()
    if not row or not row[0]:
        return None
    d = dtm.date.fromisoformat(row[0])
    out = {}
    for name, lo in [("tag", d), ("woche", d - dtm.timedelta(days=d.weekday())),
                     ("monat", d.replace(day=1)), ("jahr", d.replace(month=1, day=1))]:
        r = con.execute(
            "SELECT SUM(real_cost), SUM(oracle_cost) FROM days "
            "WHERE date BETWEEN ? AND ? AND quotient IS NOT NULL",
            (lo.isoformat(), d.isoformat())).fetchone()
        imp = cons = exp = pv = 0.0
        have = False
        for (pl,) in con.execute(
                "SELECT payload FROM day_series WHERE date BETWEEN ? AND ?",
                (lo.isoformat(), d.isoformat())):
            k = json.loads(pl)["kpi"]["real"]
            imp += k["imp_kwh"]
            cons += k["consum_kwh"]
            exp += k["exp_kwh"]
            pv += k["pv_kwh"]
            have = True
        out[name] = {
            "real_kosten": r[0], "opt_kosten": r[1],
            "sparpotential": (r[0] - r[1]) if None not in (r[0], r[1]) else None,
            "autarkie": 100.0 * (1.0 - imp / cons) if have and cons > 0 else None,
            "eigenverbrauch": 100.0 * (1.0 - exp / pv) if have and pv > 0 else None,
        }
    return out


def _broker_cfg():
    """Broker-Daten: Supervisor-Env, sonst Supervisor-Discovery, zuletzt Kern-Hostname."""
    if os.environ.get("MQTT_HOST"):
        return (os.environ["MQTT_HOST"], int(os.environ.get("MQTT_PORT", "1883")),
                os.environ.get("MQTT_USERNAME", ""), os.environ.get("MQTT_PASSWORD", ""))
    tok = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN")
    if tok:
        try:
            import urllib.request
            base = (os.environ.get("SUPERVISOR_ADDRESS")
                    or "http://172.30.32.2:80").rstrip("/")
            req = urllib.request.Request(
                base + "/discovery", headers={"Authorization": "Bearer " + tok})
            entries = json.loads(urllib.request.urlopen(req, timeout=10).read()
                                 )["data"]["discovery"]
            for e in entries:
                if e.get("service") == "mqtt":
                    c = e["config"]
                    print("MQTT: Broker aus Supervisor-Discovery:", c["host"])
                    return (c["host"], int(c.get("port", 1883)),
                            c.get("username", ""), c.get("password", ""))
        except Exception as ex:
            print("MQTT: Discovery fehlgeschlagen:", type(ex).__name__)
    # Letzter Weg: HA-Config-Entry der MQTT-Integration (Add-on-Map ro;
    # alter Supervisor mountet /config, neuer /homeassistant).
    for base in ("/config", "/homeassistant"):
        try:
            with open(base + "/.storage/core.config_entries") as f:
                for e in json.load(f)["data"]["entries"]:
                    if e.get("domain") == "mqtt" and e.get("data", {}).get("broker"):
                        c = e["data"]
                        print("MQTT: Broker aus HA-Config-Entry:", c["broker"])
                        return (c["broker"], int(c.get("port", 1883)),
                                c.get("username", ""), c.get("password", ""))
        except Exception:
            continue
    return "core-mosquitto", 1883, "", ""


def publish_all(db_path):
    host, port, user, pw = _broker_cfg()
    con = sqlite3.connect(db_path)
    try:
        data = period_sums(con)
    finally:
        con.close()
    if not data:
        return False
    c = Client(host, port, user, pw)
    n = 0
    try:
        for period, plabel in PERIODS:
            vals = data[period]
            for key, label, unit, dc, sc in METRICS:
                uid = f"ems_bench_{key}_{period}"
                cfg = {"name": f"{label} {plabel}",
                       "unique_id": uid,
                       "state_topic": f"{BASE_TOPIC}/{uid}/state",
                       "unit_of_measurement": unit,
                       "device": DEVICE, "availability_topic":
                           f"{BASE_TOPIC}/{uid}/availability"}
                if dc:
                    cfg["device_class"] = dc
                if sc:
                    cfg["state_class"] = sc
                c.publish(f"{BASE_TOPIC}/{uid}/config", cfg)
                c.publish(f"{BASE_TOPIC}/{uid}/availability", "online")
                v = vals.get(key)
                c.publish(f"{BASE_TOPIC}/{uid}/state",
                          "" if v is None else f"{v:.2f}" if key.endswith(
                              ("kosten", "potential")) else f"{v:.1f}")
                n += 3
    finally:
        c.close()
    print(f"MQTT: {n} Meldungen publiziert")
    return True
