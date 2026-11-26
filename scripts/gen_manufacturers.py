#!/usr/bin/env python3
"""Regeneriert addon/benchmark/manufacturers.py + server/manufacturers.json
aus EMS-Benchmark_Herstellerlisten.md (mit manuellen Ergänzungen)."""
import json
import re

MD = "/home/kai/energy-universe/EMS-Benchmark_Herstellerlisten.md"
EXTRA_HEMS = ["Akkudoktor-EOS", "eigene"]


def grab(md, start_marker, end_marker, bold=False):
    s = md.index(start_marker)
    e = md.index(end_marker, s)
    out = []
    for line in md[s:e].splitlines():
        m = re.match(r"- (.+)", line.strip())
        if not m:
            continue
        t = m.group(1).strip()
        if bold:
            m2 = re.match(r"\*\*(.+?)\*\*", t)
            if not m2:
                continue
            t = m2.group(1)
        t = t.replace("]", "").replace("®", "").replace("\u00ad", "").strip()
        if not t or t.startswith("_"):
            continue
        out.append(t)
    seen, res = set(), []
    for t in sorted(out + EXTRA_HEMS if bold else out, key=str.lower):
        if t.lower() not in seen:
            seen.add(t.lower())
            res.append(t)
    return res


md = open(MD, encoding="utf-8").read()
lists = {
    "HEMS": grab(md, "## 1. HEMS", "Plattform-Hintergründe", bold=True),
    "WECHSELRICHTER": grab(md, "### 2a. Wechselrichter", "### 2b."),
    "SPEICHER": grab(md, "### 2b. Batteriespeicher", "PV-Modul-Hersteller"),
    "WALLBOX": grab(md, "## 3. Wallboxen", "## 4. Wärmepumpen"),
    "WAERMEPUMPE": grab(md, "## 4. Wärmepumpen", "## Quellen"),
}

py = ('"""Hersteller-Listen fuer die Dashboard-Dropdowns.\n\n'
      "Generiert aus EMS-Benchmark_Herstellerlisten.md via scripts/gen_manufacturers.py\n"
      "Plus manuelle Ergaenzungen (Akkudoktor-EOS, eigene).\n"
      '"""\n\nMANUFACTURERS = ')
py += json.dumps(lists, ensure_ascii=False, indent=1) + "\n"
open("addon/benchmark/manufacturers.py", "w", encoding="utf-8").write(py)
json.dump(lists, open("server/manufacturers.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print({k: len(v) for k, v in lists.items()})
assert "Akkudoktor-EOS" in lists["HEMS"] and "eigene" in lists["HEMS"]
