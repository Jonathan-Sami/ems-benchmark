#!/usr/bin/env python3
"""Merge-sichere Optionen-Migration, läuft auf dem HAOS-Host (sudo python3).

Liest die AKTUELLEN Optionen des Add-on-Containers, ergänzt nur FEHLENDE
Schlüssel aus einer neuen Vorlage und POSTet das Ergebnis. Individuell
gesetzte Werte (z. B. geänderte Ladeleistung) bleiben immer erhalten —
nie wieder ein volles options-POST über eine Nutzer-Installation!

Aufruf (als root auf 192.168.2.144):
    python3 sup_merge_options.py /tmp/newopts070.json
"""
import json
import os
import subprocess
import sys
import urllib.request

SLUG = "local_ems-benchmark"
SUP = "http://172.30.32.2:80"


def supervisor_token():
    pat = "SUPERVISOR" + "_TOKEN"
    out = subprocess.run(
        ["docker", "exec", "homeassistant", "env"],
        capture_output=True, text=True, check=True).stdout
    for line in out.splitlines():
        if line.startswith(pat + "="):
            return line.split("=", 1)[1]
    raise SystemExit("Supervisor-Token nicht gefunden")


def current_options(tok):
    out = subprocess.run(
        ["docker", "exec", f"app_{SLUG}", "cat", "/data/options.json"],
        capture_output=True, text=True).stdout
    try:
        return json.loads(out)
    except Exception:
        return {}


def merge(old, new):
    """old gewinnt; neue Schlüssel aus new werden ergänzt."""
    if not isinstance(old, dict) or not isinstance(new, dict):
        return old if old is not None else new
    out = dict(old)
    for k, v in new.items():
        out[k] = merge(out[k], v) if k in out else v
    return out


def main():
    vorlage = json.load(open(sys.argv[1]))
    if "options" in vorlage:
        vorlage = vorlage["options"]
    tok = supervisor_token()
    cur = current_options(tok)
    merged = merge(cur, vorlage)
    neu = sorted(set(json.dumps(merged, sort_keys=True)) - set(json.dumps(cur, sort_keys=True)))
    req = urllib.request.Request(
        f"{SUP}/addons/{SLUG}/options",
        data=json.dumps({"options": merged}).encode(),
        headers={"Authorization": f"Bearer {tok}",
                 "Content-Type": "application/json"}, method="POST")
    print("POST:", urllib.request.urlopen(req, timeout=30).read().decode()[:120])
    req2 = urllib.request.Request(
        f"{SUP}/addons/{SLUG}/restart", method="POST",
        headers={"Authorization": f"Bearer {tok}"})
    print("restart:", urllib.request.urlopen(req2, timeout=120).read().decode()[:120])
    print("Schlüssel:", list(merged.keys()))


if __name__ == "__main__":
    main()
