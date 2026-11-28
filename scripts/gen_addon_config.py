#!/usr/bin/env python3
"""Generiert addons/ems-benchmark/configuration.yaml (+ config.yaml) aus server/manufacturers.json.

v0.6.0 — Aufklapp-Menüs: verschachtelte Dicts rendert der Supervisor als
aufklappbare Abschnitte (type: schema). Reihenfolge: Auflösung, HEMS,
Speicher, Wärmepumpe, Wallbox, Tarif. Jedes Menü: Hersteller (Dropdown),
Sensoren, Leistungsgrenzen. Wallbox hat Pflichtfeld "Auto anwesend
(Binärsensor)". Tarif: Auswahl Direktvermarktung (Sensor) vs.
Einspeisevergütung (Preis).

- Hersteller: list(...) -> Supervisor-Dropdown mit exakt den Listennamen
- Pflichtfelder enden NICHT auf '?', optionale auf '?'
- Leere Saiten '' sind valides str — Daemon meldet fehlende Pflichtangaben

Erzeugt außerdem /tmp/newopts060.json (schema-konforme Optionen für Kais HA).
"""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
M = json.load(open(BASE / "server/manufacturers.json"))

VER = "0.10.10"

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

KAIS = {
    "HEMS": "eigene",
    "Wechselrichter": exact(M["WECHSELRICHTER"], "E3DC") or "E3DC",
    "Speicher": exact(M["SPEICHER"], "E3/DC") or "E3/DC",
    "Wärmepumpe": exact(M["WAERMEPUMPE"], "Alpha Innotec") or "Alpha Innotec",
    "Wallbox": exact(M["WALLBOX"], "E3DC") or "E3DC",
}
LISTMAP = {"HEMS": M["HEMS"], "Wechselrichter": M["WECHSELRICHTER"],
           "Speicher": M["SPEICHER"], "Wärmepumpe": M["WAERMEPUMPE"],
           "Wallbox": M["WALLBOX"]}
for g, val in KAIS.items():
    if val not in LISTMAP[g]:
        LISTMAP[g] = sorted(set(LISTMAP[g]) | {val}, key=str.lower)

KAIS_SENSOREN = {
    "pv": "sensor.s10_451517000332_pv_power",
    "grid": "sensor.s10_451517000332_grid_power",
    "house": "sensor.s10_451517000332_home_power",
    "hp": "sensor.luxtronik_0_00_current_power_consumption",
    "wb": "sensor.s10_451517000332_wallbox_power",
    "wb_present": "binary_sensor.wallbox_auto_verbunden",
    "price_imp": "sensor.netzentgelt_ss14a_modul_3",
}


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
L.append("url: https://github.com/Jonathan-Sami/ems-benchmark")
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
L.append("mqtt: true")
L.append("map:\n  - homeassistant_config:ro")
L.append("ingress_port: 8091")
L.append("panel_icon: mdi:sword-cross")
L.append('panel_title: "EMS-Benchmark"')
L.append("")
L.append("schema:")
L.append("  Auflösung: list(10 Sekunden (rechenintensiv)|1 Minute|5 Minuten)")
L.append("  HEMS:  # das Energiemanagement, das bewertet wird")
L.append("    Hersteller: " + listtype(LISTMAP["HEMS"]) + "?")
L.append("  Zähler:  # Messpunkte für Netz und Hausverbrauch")
L.append("    Netz gesamt (negativ = Einspeisung): str?")
L.append("    Netz Bezug (nur bei getrennten Sensoren): str?")
L.append("    Netz Einspeisung (nur bei getrennten Sensoren): str?")
L.append("    Hausverbrauch: str?")
L.append("  Wechselrichter:")
L.append("    Hersteller: " + listtype(LISTMAP["Wechselrichter"]) + "?")
L.append("    PV Leistung: str")
L.append("  Speicher:")
L.append("    Hersteller: " + listtype(LISTMAP["Speicher"]) + "?")
L.append("    Kapazität in kWh: float")
L.append("    Ladeleistung in kW: float")
L.append("    Entladeleistung in kW: float")
L.append("    Wirkungsgrad in %: int(1,100)")
L.append('    Ladezustand in % (Sensor): str')
L.append("  Wärmepumpe:")
L.append("    Hersteller: " + listtype(LISTMAP["Wärmepumpe"]) + "?")
L.append("    Sensor: str")
L.append("    Mindestleistung in kW: float")
L.append("    Höchstleistung in kW: float")
L.append("  Wallbox:")
L.append("    Hersteller: " + listtype(LISTMAP["Wallbox"]) + "?")
L.append("    Sensor: str")
L.append("    Auto anwesend (Binärsensor): str")
L.append("    Mindestleistung in kW: float")
L.append("    Höchstleistung in kW: float")
L.append("  Tarif:")
L.append("    Preis Bezug (Sensor): str")
L.append("    Einspeisung: list(Direktvermarktung|Einspeisevergütung)")
L.append("    Sensor Einspeisung (nur bei Direktvermarktung): str?")
L.append("    Feste Vergütung in €/kWh (nur bei Einspeisevergütung): float?")
L.append("  MQTT Sensoren: bool")
L.append("")
# Release-Defaults: bewusst leer/nutral. Die Supervisor-Optionen überleben
# Updates — wer etwas einstellt, behält es. Kais eigene Werte stehen NUR in
# /tmp/newopts070.json (Options-POST), nie in config.yaml.
L.append("options:")
L.append('  Auflösung: "1 Minute"')
L.append("  HEMS: {}")
L.append("  Zähler:")
L.append('    Netz gesamt (negativ = Einspeisung): ""')
L.append('    Netz Bezug (nur bei getrennten Sensoren): ""')
L.append('    Netz Einspeisung (nur bei getrennten Sensoren): ""')
L.append('    Hausverbrauch: ""')
L.append("  Wechselrichter:")
L.append('    PV Leistung: ""')
L.append("  Speicher:")
L.append("    Kapazität in kWh: 0.0")
L.append("    Ladeleistung in kW: 0.0")
L.append("    Entladeleistung in kW: 0.0")
L.append("    Wirkungsgrad in %: 95")
L.append('    Ladezustand in % (Sensor): ""')
L.append("  Wärmepumpe:")
L.append('    Sensor: ""')
L.append("    Mindestleistung in kW: 0.5")
L.append("    Höchstleistung in kW: 2.0")
L.append("  Wallbox:")
L.append('    Sensor: ""')
L.append('    Auto anwesend (Binärsensor): ""')
L.append("    Mindestleistung in kW: 1.0")
L.append("    Höchstleistung in kW: 11.0")
L.append("  Tarif:")
L.append('    Preis Bezug (Sensor): ""')
L.append('    Einspeisung: "Einspeisevergütung"')
L.append('    Sensor Einspeisung (nur bei Direktvermarktung): ""')
L.append("    Feste Vergütung in €/kWh (nur bei Einspeisevergütung): 0.124")
L.append("  MQTT Sensoren: false")
L.append("")

out = "\n".join(L)
for fn in ("configuration.yaml", "config.yaml"):
    (BASE / "addons" / "ems-benchmark" / fn).write_text(out, encoding="utf-8")
print(f"geschrieben: addons/ems-benchmark/configuration.yaml + config.yaml ({len(out)} Zeichen)")

# Schema-konforme Optionen fürs POST an den Supervisor (Kais Anlage)
opts = {"options": {"MQTT Sensoren": True,
    "Auflösung": "1 Minute",
    "HEMS": {"Hersteller": KAIS["HEMS"]},
    "Zähler": {
        "Netz gesamt (negativ = Einspeisung)": KAIS_SENSOREN["grid"],
        "Netz Bezug (nur bei getrennten Sensoren)": "",
        "Netz Einspeisung (nur bei getrennten Sensoren)": "",
        "Hausverbrauch": KAIS_SENSOREN["house"],
    },
    "Wechselrichter": {
        "Hersteller": KAIS["Wechselrichter"],
        "PV Leistung": KAIS_SENSOREN["pv"],
    },
    "Speicher": {
        "Hersteller": KAIS["Speicher"], "Kapazität in kWh": 4.8,
        "Ladeleistung in kW": 2.2, "Entladeleistung in kW": 2.2,
        "Wirkungsgrad in %": 95,
        "Ladezustand in % (Sensor)": "sensor.s10_451517000332_ladezustand",
    },
    "Wärmepumpe": {
        "Hersteller": KAIS["Wärmepumpe"], "Sensor": KAIS_SENSOREN["hp"],
        "Mindestleistung in kW": 0.5, "Höchstleistung in kW": 2.5,
    },
    "Wallbox": {
        "Hersteller": KAIS["Wallbox"], "Sensor": KAIS_SENSOREN["wb"],
        "Auto anwesend (Binärsensor)": KAIS_SENSOREN["wb_present"],
        "Mindestleistung in kW": 1.0, "Höchstleistung in kW": 11.0,
    },
    "Tarif": {
        "Preis Bezug (Sensor)": KAIS_SENSOREN["price_imp"],
        "Einspeisung": "Einspeisevergütung",
        "Sensor Einspeisung (nur bei Direktvermarktung)": "",
        "Feste Vergütung in €/kWh (nur bei Einspeisevergütung)": 0.124,
    },
}}
Path("/tmp/newopts070.json").write_text(json.dumps(opts, ensure_ascii=False), encoding="utf-8")
print("geschrieben: /tmp/newopts070.json")
print("Geräte-Werte:", KAIS)
