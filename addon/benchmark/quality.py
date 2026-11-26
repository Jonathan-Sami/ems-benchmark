"""Per-day data-quality gate. A day may only enter the leaderboard if the
sensors were trustworthy — otherwise one broken clamp ruins a manufacturer."""
import numpy as np


def quality_check(traces: dict, dt_h: float,
                  min_coverage=0.90, max_residual_kwh=2.0,
                  max_residual_frac=0.08,
                  min_import_kwh=0.5):
    """Return (ok, flag_dict). residual = mean power-balance error."""
    n = len(traces["nan_mask"])
    valid = ~traces["nan_mask"]
    coverage = float(valid.mean())

    # energy balance over valid slots: sum(pv + imp + batt_out - batt_in) vs loads
    # (battery power optional here; balance computed without battery uses
    #  imp/exp which already carry the battery effect through the grid point)
    pv = traces["pv"] * valid
    imp = traces["imp"] * valid
    exp = traces["exp"] * valid
    base = traces["base_load"] * valid
    hp = traces["hp"] * valid
    wb = traces["wb"] * valid
    e_in = np.sum(pv + imp) * dt_h
    e_out = np.sum(base + hp + wb + exp) * dt_h
    residual = abs(e_in - e_out)

    flags = {
        "coverage": round(coverage, 3),
        "residual_kwh": round(residual, 3),
        "import_kwh": round(float(np.sum(imp) * dt_h), 3),
        "export_kwh": round(float(np.sum(exp) * dt_h), 3),
        "hp_kwh": round(float(np.sum(hp) * dt_h), 3),
        "wb_kwh": round(float(np.sum(wb) * dt_h), 3),
    }
    ok = (coverage >= min_coverage
          and residual <= max(max_residual_kwh, max_residual_frac * max(e_in, 1.0))
          and flags["import_kwh"] >= min_import_kwh
          and flags["hp_kwh"] + flags["wb_kwh"] >= 0  # sanity
          and np.any(traces["price_imp"] != 0))
    return ok, flags
