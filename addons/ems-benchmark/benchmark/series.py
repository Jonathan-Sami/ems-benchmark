"""Zeitserien eines Tages fuer das Dashboard: Real-Verlauf vs. Optimum-Plan.

Real-Batterieleistung wird aus der physikalischen Leistungsbilanz
rekonstruiert (es dafuer kein Sensor noetig):
    batt_net = imp + pv - base - hp - wb - exp   (+ = laden)
Ladezustand real: konfigurierter Sensor, sonst Rekonstruktion ab 50 %.
"""
import numpy as np


def build_series(day, res_sec, tr, dd, result, o):
    n = dd.n
    step = max(1, round(300 / res_sec))  # Anzeige: 5-Minuten-Punkte
    dt_slot = dd.dt_h * step
    m = max(1, n // step)
    opt = result.oracle_plan
    b = o["battery"]
    cap = float(b.get("capacity_kwh") or 0) or 1.0
    eta = float(b.get("eta") or 1.0) or 1.0

    def r3(a):
        a = np.nan_to_num(np.asarray(a, dtype=float))[:n]
        if step == 1:
            return [round(float(x), 3) for x in a]
        return [round(float(x), 3)
                for x in a[:m * step].reshape(m, step).mean(axis=1)]

    t = [int(i * step * res_sec / 60.0) for i in range(m)]

    net = (tr["imp"] + tr["pv"] - tr["base_load"] - tr["hp"]
           - tr["wb"] - tr["exp"])
    bc = np.maximum(net, 0.0)
    bd = np.maximum(-net, 0.0)

    soc = tr.get("soc")
    if soc is None or not np.any(np.isfinite(soc)):
        dsoc = (bc * eta - bd / eta) * dd.dt_h / cap * 100.0
        soc = np.clip(50.0 + np.cumsum(dsoc), 0.0, 100.0)
    else:
        soc = np.nan_to_num(soc, nan=50.0)

    real = {"pv": r3(tr["pv"]), "base": r3(tr["base_load"]), "hp": r3(tr["hp"]),
            "wb": r3(tr["wb"]), "bc": r3(bc), "bd": r3(bd),
            "imp": r3(tr["imp"]), "exp": r3(tr["exp"]), "soc": r3(soc)}
    osoc = np.clip(np.asarray(opt["E"], dtype=float)[:n] / cap * 100.0, 0.0, 100.0)
    optd = {"pv": real["pv"], "base": real["base"], "hp": r3(opt["h"]),
            "wb": r3(opt["w"]), "bc": r3(opt["c"]), "bd": r3(opt["d"]),
            "imp": r3(opt["g"]), "exp": r3(opt["e"]), "soc": r3(osoc)}

    cons_kwh = float(np.sum(tr["base_load"] + tr["hp"] + tr["wb"]) * dd.dt_h)
    pv_kwh = float(np.sum(tr["pv"]) * dd.dt_h)

    def kpi(imp_arr, exp_arr, cost):
        imp_k = float(np.sum(imp_arr) * dd.dt_h)
        exp_k = float(np.sum(exp_arr) * dd.dt_h)
        return {"consum_kwh": round(cons_kwh, 2), "pv_kwh": round(pv_kwh, 2),
                "imp_kwh": round(imp_k, 2), "exp_kwh": round(exp_k, 2),
                "autarky": round(100.0 * (1.0 - imp_k / cons_kwh), 1) if cons_kwh > 0 else None,
                "eigen": round(100.0 * (1.0 - exp_k / pv_kwh), 1) if pv_kwh > 0 else None,
                "cost": round(float(cost), 2)}

    return {"date": str(day), "res_sec": res_sec * step, "t": t,
            "price": r3(tr["price_imp"]),
            "real": real, "opt": optd,
            "kpi": {"real": kpi(tr["imp"], tr["exp"], result.real_cost),
                    "opt": kpi(np.asarray(opt["g"]), np.asarray(opt["e"]),
                               result.oracle_cost)}}
