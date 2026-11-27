"""Konfiguration: Add-on-Optionen (Supervisor) + Nutzer-Overlay (/data/user_config.json).

Der Overlay wird vom Dashboard-Einstellungsseiten geschrieben und liegt in /data —
das übersteht jedes Add-on-Update, der Nutzer muss nach Updates nichts neu
eintragen. Werte im Overlay gewinnen gegenüber den Supervisor-Defaults.
"""
import copy
import json
import os

USER_FILE = "user_config.json"


def _deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load(data_dir, options_file):
    try:
        with open(options_file) as f:
            opts = json.load(f)
    except Exception:
        opts = {}
    try:
        with open(os.path.join(data_dir, USER_FILE)) as f:
            user = json.load(f)
        opts = _deep_merge(opts, user)
    except Exception:
        pass
    return opts


def save(data_dir, patch):
    p = os.path.join(data_dir, USER_FILE)
    try:
        with open(p) as f:
            cur = json.load(f)
    except Exception:
        cur = {}
    cur = _deep_merge(cur, patch)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cur, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    return cur
