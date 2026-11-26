<p align="left"><img src="assets/logo/logo.png" width="110" alt="EMS-Benchmark Logo"></p>

# EMS-Benchmark

Unabhängiger Blackbox-Benchmark für Heim-Energiemanagementsysteme (EMS).
Misst **Intelligenz statt Ausstattung**: Bewertet wird nur, wie gut ein EMS
das Potential desselben Hauses ausschöpft — PV-Größe und Speicher skalieren
heraus.

## Methodik (kurz)

Pro Kalendertag (Experiment, 15-Min bis 1-Min-Raster, konfigurierbar):

- **Replay-Benchmark:** Die reale Umgebung (PV, Grundlast, dynamische Preise,
  gemessene Wärmepumpen-/Wallbox-Energie mit Fenster) wird aufgezeichnet und
  daraus ein **Oracle-MILP** (HiGHS via `scipy.optimize.milp`) gerechnet:
  kostenoptimale Tagesplanung unter denselben physikalischen Grenzen wie das
  echte EMS (Geräte: 0 oder [p_min, p_max] kW).
- **50/50-Regel:** Speicher startet und beendet den Tag bei 50 % SOC →
  jeder Tag ist ein in sich geschlossenes, faires Experiment.
- **Verschleiß κ:** Durchfluss-Kosten (Default 0,05 €/kWh) verhindern, dass
  das Oracle mit sinnlosem Zyklieren die Latte unrealistisch hochlegt.
- **Kennzahl:** `quotient = Kosten_Oracle / Kosten_real` (raw, wie besprochen;
  Gewinn-Tage mit negativen Kosten werden auf dem Server als eigener Bucket
  geführt). Nahe 1 = optimale Steuerung, < 1 = verschenktes Potential.
- **Blackbox:** Es werden nur Leistungssensoren gebraucht (PV, Netz, Haus,
  WP, Wallbox, Batterie, SOC, Preis) — kein EMS-Quellcode.

## Komponenten

```
addon/                    Home-Assistant-Add-on (Community-Client)
  config.yaml             Options/Schema: Entitäten, Batterie, Geräte-Grenzen
  benchmark/model.py      Oracle-MILP + reale Kosten + Quotient (Kern)
  benchmark/ha.py         HA-History-Rest + zeitgewichtete Resampling
  benchmark/quality.py    Tages-Datenqualitäts-Gate (Abdeckung, Residual)
  benchmark/store.py      lokale SQLite + Push-Queue (offline-safe)
  benchmark/push.py       API-Client (nur Aggregat verlässt das Haus!)
  benchmark/daemon.py     nightly Runner (00:40) + Backfill via CLI
server/api.py             Community-API auf dem KI-Server (stdlib, :8090)
tests/synthetic_test.py   End-to-End-Verifikation mit bekanntem Tag
```

## API

- `POST /api/v1/daily` — ein Tagesergebnis pro Haushalt (Upsert auf
  version+household+date). Pflichtfelder: benchmark_version, household_id,
  date, quotient.
- `GET  /api/v1/stats` — Mittelwerte pro Produkt/Version (Tage, Haushalte).
- `GET  /api/v1/healthz`

`benchmark_version` ist Pflicht: Modelländerungen machen Zahlen unvergleichbar.

## Betrieb

Add-on: in die Add-on-Store-Repo einhängen, Entitäten + Batterie + api_url
konfigurieren; rechnet nachtens den Vortag, schiebt nur die Kennzahl.

Server: systemd-Userdienst `ems-bench-api`
(linger=yes), DB: `server/results.db`.

```
systemctl --user status ems-bench-api
.venv/bin/python addon/benchmark/daemon.py 2026-11-21   # manueller Backfill
.venv/bin/python tests/synthetic_test.py                # Modell-Test
```

## Bekannte offene Punkte (v2)

- WP-Flexibilitätsfenster aktuell "frei im Tag verschiebbar" (Komfortneutral);
  später aus Sessions gelernt.
- Wallbox-Fenster = gemessene Ladezeit (ohne Anwesenheits-Sensor); mit
  `wb_connected`-Entität genauer.
- Negativ-Kosten-Tage in der Aggregation separat ausweisen.
- Rechenzeit 1440-Slot-MILP: ~37 s (Ryzen AI Max); auf Pi 4 evtl. Minuten →
  ggf. mip_rel_gap lockern oder 15-Min-Fallback.
```
