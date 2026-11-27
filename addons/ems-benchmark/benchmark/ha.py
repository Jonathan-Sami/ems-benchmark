"""Fetch HA history via REST (supervisor token) and resample onto the slot grid.

State changes are time-weighted averaged per slot (exactly what an energy
meter would integrate), so a 5s-wallbox spits no bias into the model.
"""
import datetime as dtm
import json
import urllib.request
import urllib.parse
import numpy as np


def _get(url, token):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def _api_base(ha_url):
    """Normalisiert auf API-Basis: direkt (http://host:8123/api) oder
    Supervisor-Proxy (http://supervisor/core/api)."""
    base = ha_url.rstrip("/")
    return base if base.endswith("/api") else base + "/api"


def fetch_history(ha_url, token, entity_ids, day_start: dtm.datetime,
                  day_end: dtm.datetime):
    """Return {entity: [(datetime, float|None), ...]} state-change lists."""
    params = urllib.parse.urlencode({
        "end_time": day_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "filter_entity_id": ",".join(entity_ids),
    })
    # start_time MUSS im Pfad stehen — als Query-Parameter ignoriert HA ihn
    # und liefert stillschweigend nur "jetzt minus 24h".
    url = (f"{_api_base(ha_url)}/history/period/"
           f"{day_start.strftime('%Y-%m-%dT%H:%M:%SZ')}?{params}")
    raw = _get(url, token)
    out = {}
    for series in raw:
        if not series:
            continue
        eid = series[0].get("entity_id")
        pts = []
        for s in series:
            st = s.get("last_changed")
            try:
                val = float(s["state"])
            except (KeyError, TypeError, ValueError):
                val = None
            pts.append((dtm.datetime.strptime(
                st[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dtm.timezone.utc), val))
        pts.sort(key=lambda p: p[0])
        out[eid] = pts
    return out


def resample(points, bin_start: dtm.datetime, n_slots: int, dt_h: float,
             hold_last=True):
    """Time-weighted average of a state-change series into n_slots bins.

    Each state is assumed constant until the next change (HA semantics).
    Unavailable slots become NaN.
    """
    edges = [bin_start + dtm.timedelta(hours=dt_h * i) for i in range(n_slots + 1)]
    out = np.full(n_slots, np.nan)
    if not points:
        return out
    # extend final state to horizon end so the last bin is covered
    pts = list(points)
    if pts and hold_last and pts[-1][1] is not None:
        pts.append((edges[-1], pts[-1][1]))
    for i in range(n_slots):
        lo, hi = edges[i], edges[i + 1]
        num = 0.0
        den = 0.0
        for j in range(len(pts) - 1):
            t0, v = pts[j]
            t1 = pts[j + 1][0]
            s = max(lo, t0)
            e = min(hi, t1)
            if e > s and v is not None:
                w = (e - s).total_seconds()
                num += v * w
                den += w
        if den > 0:
            out[i] = num / den
    return out


def fetch_units(ha_url, token, entity_ids):
    """unit_of_measurement per entity (for W->kW scaling)."""
    out = {}
    for eid in entity_ids:
        try:
            s = _get(f"{_api_base(ha_url)}/states/{eid}", token)
            out[eid] = (s.get("attributes") or {}).get("unit_of_measurement", "")
        except Exception:
            out[eid] = ""
    return out


def build_day_traces(hist, entities, bin_start, n_slots, dt_h, units=None):
    """Map raw history to the arrays the model needs (powers -> kW).

    units: optional {entity_id: unit_of_measurement}; 'W' scales by 1/1000.
    entities: dict with keys pv, grid_net (signed: + import / - export, or
    separate grid_import/grid_export), house, hp, wb, price_imp, price_exp
    (optional price_exp; defaults to price_imp), plus optional
    wb_connected (bool-ish sensor).
    """
    units = units or {}

    def rs(key):
        arr = resample(hist.get(entities[key], []), bin_start, n_slots, dt_h)
        if units.get(entities[key], "") == "W":
            arr = arr / 1000.0
        return arr

    pv = rs("pv")
    house = rs("house")
    hp = rs("hp")
    wb = rs("wb")
    price_imp = rs("price_imp")
    price_exp = rs("price_exp") if "price_exp" in entities and entities["price_exp"] else price_imp

    if "grid_net" in entities and entities["grid_net"]:
        net = rs("grid_net")
        imp = np.where(net > 0, net, 0.0)
        exp = np.where(net < 0, -net, 0.0)
    else:
        imp = rs("grid_import")
        exp = rs("grid_export")

    for arr in (imp, exp):
        arr[np.isnan(pv) | np.isnan(arr)] = 0.0  # handled by quality gate

    pv0 = np.nan_to_num(pv)
    hp0 = np.nan_to_num(hp)
    wb0 = np.nan_to_num(wb)
    house0 = np.nan_to_num(house)
    base_load = np.maximum(house0 - hp0 - wb0, 0.0)
    # if house sensor missing entirely: reconstruct from grid balance
    if np.all(np.isnan(house)) or np.nansum(house0) == 0:
        base_load = np.maximum(pv0 + np.nan_to_num(imp) - np.nan_to_num(exp)
                               - hp0 - wb0, 0.0)

    if "wb_connected" in entities and entities["wb_connected"]:
        conn = rs("wb_connected")
        window = conn > 0.5
        if not window.any():
            window = wb0 > 0.1
    else:
        window = wb0 > 0.1

    return {
        "pv": pv0, "base_load": base_load,
        "price_imp": np.nan_to_num(price_imp), "price_exp": np.nan_to_num(price_exp),
        "hp": hp0, "wb": wb0, "imp": imp, "exp": exp,
        "window": window,
        "nan_mask": (np.isnan(pv) | np.isnan(price_imp) | np.isnan(imp)
                     | np.isnan(exp)),
    }
