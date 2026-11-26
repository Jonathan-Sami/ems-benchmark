#!/usr/bin/env python3
"""Generiert addon/configuration.yaml (+ config.yaml) mit MariaDB-artigem
Native-Schema aus server/manufacturers.json:

- Hersteller:  list(...)  -> Supervisor-Dropdown mit exakt den Listennamen
- Sensoren:    Liste von Datensätzen {Funktion, Entität} -> Zeilen-Editor
               mit "Hinzufügen"-Dialog (wie MariaDB Logins/Rights)
- optionale Felder enden auf '?'

Erzeugt außerdem /tmp/newopts<ver>.json (schema-konforme Optionen für Kais HA).
"""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
M = json.load(open(BASE / "server/manufacturers.json"))

VER = "0.5.0"

FUNKTIONEN = [
    "PV Leistung",
    "Netz gesamt (negativ = Einspeisung)",
    "Netz Bezug (nur bei getrennten Sensoren)",
    "Netz Einspeisung (nur bei getrennten Sensoren)",
    "Hausverbrauch",
    "Wärmepumpe Leistung",
    "Wallbox Leistung",
    "Wallbox verbunden (optional)",
    "Preis Bezug",
    "Preis Einspeisung (nur bei Direktvermarktung)",
]
for f in FUNKTIONEN:
    assert "|" not in f, f

KAIS_SENSOREN = {  # (aktive Zeilen fürs Default-options-Block)
    "PV Leistung": "sensor.s10_451517000332_pv_power",
    "Netz gesamt (negativ = Einspeisung)": "sensor.s10_451517000332_grid_power",
    "Hausverbrauch": "sensor.s10_451517000332_home_power",
    "Wärmepumpe Leistung": "sensor.luxtronik_0_00_current_power_consumption",
    "Wallbox Leistung": "sensor.s10_451517000332_wallbox_power",
    "Preis Bezug": "sensor.netzentgelt_ss14a_modul_3",
}

# Kais Geräteangaben exakt gegen die Listen normalisieren
def exact(lst, want):
    low = {v.lower(): v for v in lst}
    hit = low.get(want.lower())
    if hit:
        return hit
    for k, v in low.items():
        if want.lower() in k:
            return v
    return None

GERATE = {
    "HEMS": "eigene",
    "Wechselrichter": exact(M["WECHSELRICHTER"], "E3DC") or "E3DC",
    "Speicher": exact(M["SPEICHER"], "E3/DC") or "E3/DC",
    "Wärmepumpe": exact(M["WAERMEPUMPE"], "Alpha Innotec") or "Alpha Innotec",
    "Wallbox": exact(M["WALLBOX"], "E3DC") or "E3DC",
}
# Falls ein Wert nicht in der Liste ist: ergänzen, damit Options validieren
LISTMAP = {"HEMS": M["HEMS"], "Wechselrichter": M["WECHSELRICHTER"],
           "Speicher": M["SPEICHER"], "Wärmepumpe": M["WAERMEPUMPE"],
           "Wallbox": M["WALLBOX"]}
for g, val in GERATE.items():
    if val not in LISTMAP[g]:
        LISTMAP[g] = sorted(set(LISTMAP[g]) | {val}, key=str.lower)


def yq(s: str) -> str:
    """YAML-Doppel-String (Escapes für \\ und ")."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def listtype(values):
    # list(...) spaltet der Supervisor an '|' — '|' in Namen ist verboten
    assert all("|" not in v for v in values)
    return "list(" + "|".join(values) + ")"

L = []
L.append("name: EMS Benchmark")
L.append("description: Unabhängiger Blackbox-Benchmark für Heim-Energiemanagementsysteme")
L.append(f'version: "{VER}"')
L.append("slug: ems-benchmark")
L.append("url: https://github.com/kai/ems-benchmark")
L.append("arch:\n  - amd64\n  - aarch64")
L.append("startup: application")
L.append("stage: experimental")
L.append('code_owner: "Kai <kai@example.com>"')
L.append("")
L.append("homeassistant_api: true")
L.append("")
L.append("ports:\n  8091/tcp: 8091")
L.append("ports_description:\n  8091/tcp: Dashboard (Port ggf. im Netzwerk-Tab anpassen)")
L.append("")
L.append("ingress: true")
L.append("ingress_port: 8091")
L.append("panel_icon: mdi:sword-cross")
L.append('panel_title: "EMS-Benchmark"')
L.append("")
L.append("schema:")
L.append("  Auflösung: list(10 Sekunden|1 Minute|5 Minuten)")
L.append("  Einspeisung:")
L.append("    Sensor: str?")
L.append("    Feste Vergütung in €/kWh: str?")
L.append("  Sensoren:  # Zeilen wie bei MariaDB: Hinzufügen → Funktion wählen → Sensor-ID eintragen")
L.append("    - Funktion: " + listtype(FUNKTIONEN))
L.append("      Entität: str?")
L.append("  Batterie:")
L.append("    Kapazität in kWh: float")
L.append("    Ladeleistung in kW: float")
L.append("    Entladeleistung in kW: float")
L.append("    Wirkungsgrad in %: int(1,100)")
L.append("  Grenzwerte:")
L.append("    Wärmepumpe mindestens in kW: float")
L.append("    Wärmepumpe höchstens in kW: float")
L.append("    Wallbox mindestens in kW: float")
L.append("    Wallbox höchstens in kW: float")
L.append("  Geräte:")
for g in ("HEMS", "Wechselrichter", "Speicher", "Wärmepumpe", "Wallbox"):
    L.append(f"    {g}: " + listtype(LISTMAP[g]) + "?")
L.append("")
L.append("options:")
L.append('  Auflösung: "1 Minute"')
L.append("  Einspeisung:")
L.append('    Sensor: ""')
L.append('    Feste Vergütung in €/kWh: "0.124"')
L.append("  Sensoren:")
for f, e in KAIS_SENSOREN.items():
    L.append(f"    - Funktion: {yq(f)}")
    L.append(f"      Entität: {yq(e)}")
L.append("  Batterie:")
L.append("    Kapazität in kWh: 4.8")
L.append("    Ladeleistung in kW: 4.6")
L.append("    Entladeleistung in kW: 4.6")
L.append("    Wirkungsgrad in %: 95")
L.append("  Grenzwerte:")
L.append("    Wärmepumpe mindestens in kW: 0.5")
L.append("    Wärmepumpe höchstens in kW: 2.5")
L.append("    Wallbox mindestens in kW: 1.0")
L.append("    Wallbox höchstens in kW: 11.0")
L.append("  Geräte:")
for g, val in GERATE.items():
    L.append(f"    {g}: {yq(val)}")
L.append("")

out = "\n".join(L)
for fn in ("configuration.yaml", "config.yaml"):
    (BASE / "addon" / fn).write_text(out, encoding="utf-8")
print(f"geschrieben: addon/configuration.yaml + config.yaml ({len(out)} Zeichen)")

# Schema-konforme Optionen fürs POST an den Supervisor
import copy
opts = {
    "options": {
        "Auflösung": "1 Minute",
        "Einspeisung": {"Sensor": "", "Feste Vergütung in €/kWh": "0.124"},
        "Sensoren": [{"Funktion": f, "Entität": e} for f, e in KAIS_SENSOREN.items()],
        "Batterie": {"Kapazität in kWh": 4.8, "Ladeleistung in kW": 4.6,
                     "Entladeleistung in kW": 4.6, "Wirkungsgrad in %": 95},
        "Grenzwerte": {"Wärmepumpe mindestens in kW": 0.5, "Wärmepumpe höchstens in kW": 2.5,
                       "Wallbox mindestens in kW": 1.0, "Wallbox höchstens in kW": 11.0},
        "Geräte": GERATE,
    }
}
Path("/tmp/newopts050.json").write_text(json.dumps(opts, ensure_ascii=False), encoding="utf-8")
print("geschrieben: /tmp/newopts050.json")
print("Geräte-Werte:", GERATE)
