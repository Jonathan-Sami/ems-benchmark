"""EMS-Benchmark core: oracle MILP + real cost + quotient.

Per-day experiment, 50/50 battery rule:
  battery starts AND ends the day at 50% SOC, free optimization in between.

Variables per slot t (0..N-1):
  g[t]  grid import [kW]        e[t]  feed-in [kW]
  c[t]  battery charge [kW AC]  d[t]  battery discharge [kW AC]
  h[t]  heat-pump power [kW]    w[t]  wallbox power [kW]
  yh[t] HP on/off binary        yw[t] wallbox on/off binary
  E[t]  battery energy [kWh], t=0..N

Constraints: power balance, SOC dynamics+bounds (E[0]=E[N]=0.5*C),
device min/max ({0} u [min,max] kW via binary), wallbox presence window,
fixed total energies for HP and wallbox (from measurement).

Objective: min sum (lambda_imp*g - lambda_exp*e + kappa*(c+d)) * dt
kappa prices cycle wear so the oracle cannot cheat with endless cycling.
"""

from dataclasses import dataclass, field
import numpy as np
from scipy.optimize import milp, LinearConstraint, Bounds
from scipy.sparse import lil_matrix, eye as sieye, diags


@dataclass
class DayData:
    """Measured traces of one day, already resampled onto the slot grid."""
    pv: np.ndarray            # PV production [kW]
    base_load: np.ndarray     # non-shiftable base load [kW] (house minus HP/wallbox)
    price_imp: np.ndarray     # import price [EUR/kWh]
    price_exp: np.ndarray     # feed-in price [EUR/kWh]
    hp_energy_kwh: float      # total HP energy that must be placed (measured)
    wb_energy_kwh: float      # total wallbox energy that must be placed (measured)
    wb_window: np.ndarray     # bool mask, slots where wallbox may charge
    # measured (real EMS behaviour) for the real-cost side:
    grid_import: np.ndarray   # [kW]
    grid_export: np.ndarray   # [kW]
    dt_h: float               # slot length in hours

    @property
    def n(self):
        return len(self.pv)


@dataclass
class BatteryConfig:
    capacity_kwh: float
    p_charge_max: float       # kW
    p_discharge_max: float    # kW
    eta: float = 1.0          # one-way AC->battery->AC efficiency
    soc_start_frac: float = 0.5
    soc_end_frac: float = 0.5
    soc_min_frac: float = 0.0
    soc_max_frac: float = 1.0


@dataclass
class DeviceLimits:
    p_min: float              # min power when on [kW]
    p_max: float              # max power [kW]


@dataclass
class BenchConfig:
    battery: BatteryConfig
    hp: DeviceLimits
    wb: DeviceLimits
    kappa: float = 0.05       # EUR per kWh battery throughput (cycle wear)
    grid_import_max: float = 11.0
    grid_export_max: float = 11.0


@dataclass
class DayResult:
    oracle_cost: float
    real_cost: float
    quotient: float           # oracle/real, raw, may be negative by design
    battery_throughput_kwh: float
    solver_status: str
    relaxed: bool             # True if binary device mins had to be dropped
    oracle_plan: dict = field(default_factory=dict, repr=False)


def _build_and_solve(dd: DayData, cfg: BenchConfig, binaries: bool):
    N = dd.n
    dt = dd.dt_h
    B = cfg.battery
    E_mid = B.soc_start_frac * B.capacity_kwh

    # variable blocks
    ix = {}
    off = 0
    for name, size in [("g", N), ("e", N), ("c", N), ("d", N),
                       ("h", N), ("w", N), ("yh", N), ("yw", N), ("E", N + 1)]:
        ix[name] = slice(off, off + size)
        off += size
    nvar = off

    def v(name, t):
        """absolute variable index of block name at slot t"""
        return ix[name].start + t

    rows = []          # (A_row, lb, ub)
    def add(A, lb, ub):
        rows.append((A, lb, ub))

    # --- power balance: g - e + d - c - h - w = L - pv
    # (physical: pv + g + d = L + e + c + w + h)
    A = lil_matrix((N, nvar))
    for t in range(N):
        A[t, v("g", t)] = 1
        A[t, v("e", t)] = -1
        A[t, v("d", t)] = 1
        A[t, v("c", t)] = -1
        A[t, v("h", t)] = -1
        A[t, v("w", t)] = -1
    add(A.tocsr(), dd.base_load - dd.pv, dd.base_load - dd.pv)

    # --- SOC dynamics: E[t+1] - E[t] - eta*c*dt + d*dt/eta = 0
    A = lil_matrix((N, nvar))
    for t in range(N):
        A[t, v("E", t + 1)] = 1
        A[t, v("E", t)] = -1
        A[t, v("c", t)] = -B.eta * dt
        A[t, v("d", t)] = dt / B.eta
    add(A.tocsr(), np.zeros(N), np.zeros(N))

    # --- device min/max:  p_min*y <= p <= p_max*y  (binary off -> p=0)
    for p_name, y_name, lim in [("h", "yh", cfg.hp), ("w", "yw", cfg.wb)]:
        A = lil_matrix((2 * N, nvar))
        for t in range(N):
            A[2 * t, v(p_name, t)] = 1
            A[2 * t, v(y_name, t)] = -lim.p_max
            A[2 * t + 1, v(p_name, t)] = -1
            A[2 * t + 1, v(y_name, t)] = lim.p_min
        lb = np.full(2 * N, -np.inf)
        ub = np.zeros(2 * N)   # p - pmax*y <= 0 ; pmin*y - p <= 0
        add(A.tocsr(), lb, ub)

    # --- fixed total energies
    A = lil_matrix((2, nvar))
    A[0, ix["h"]] = dt
    A[1, ix["w"]] = dt
    add(A.tocsr(),
        np.array([dd.hp_energy_kwh, dd.wb_energy_kwh]),
        np.array([dd.hp_energy_kwh, dd.wb_energy_kwh]))

    # --- variable bounds
    lb = np.full(nvar, 0.0)
    ub = np.full(nvar, np.inf)
    ub[ix["g"]] = cfg.grid_import_max
    ub[ix["e"]] = cfg.grid_export_max
    ub[ix["c"]] = B.p_charge_max
    ub[ix["d"]] = B.p_discharge_max
    ub[ix["h"]] = cfg.hp.p_max
    ub[ix["w"]] = cfg.wb.p_max
    # wallbox only inside presence window
    wmask = ~dd.wb_window
    ub[np.where(wmask)[0] + ix["w"].start] = 0.0
    if binaries:
        lb[ix["yh"]], ub[ix["yh"]] = 0.0, 1.0
        lb[ix["yw"]], ub[ix["yw"]] = 0.0, 1.0
    else:
        # LP-Relaxierung: y frei nach oben -> p_min*y <= p wirkt nicht mehr,
        # Mindestlaufleistungen sind aufgehoben, p <= p_max*y bleibt.
        ub[ix["yh"]] = np.inf
        ub[ix["yw"]] = np.inf
    # battery bounds
    lb[ix["E"]] = B.soc_min_frac * B.capacity_kwh
    ub[ix["E"]] = B.soc_max_frac * B.capacity_kwh
    # 50/50-Regel: Start UND Ende hart auf Vorgabe (auch bei start==end —
    # sonst darf der Oracle den Tagesanfang gratis leerziehen).
    lb[ix["E"].start] = ub[ix["E"].start] = B.soc_start_frac * B.capacity_kwh
    lb[ix["E"].stop - 1] = ub[ix["E"].stop - 1] = B.soc_end_frac * B.capacity_kwh

    # --- objective
    cost = np.zeros(nvar)
    cost[ix["g"]] = dd.price_imp * dt
    cost[ix["e"]] = -dd.price_exp * dt
    cost[ix["c"]] = cfg.kappa * dt
    cost[ix["d"]] = cfg.kappa * dt

    integrality = np.zeros(nvar)
    if binaries:
        integrality[ix["yh"]] = 1
        integrality[ix["yw"]] = 1

    from scipy.sparse import vstack as svstack
    cons = LinearConstraint(svstack([r[0] for r in rows]),
                            np.concatenate([r[1] for r in rows]),
                            np.concatenate([r[2] for r in rows]))
    res = milp(c=cost, constraints=cons,
               integrality=integrality,
               bounds=Bounds(lb, ub),
               options={"time_limit": 300, "mip_rel_gap": 1e-4})
    return res, ix


def run_day(dd: DayData, cfg: BenchConfig) -> DayResult:
    """Solve one benchmark day. Retries with LP relaxation if the strict
    min-power binaries are infeasible (e.g. tiny measured energies)."""
    res, ix = _build_and_solve(dd, cfg, binaries=True)
    relaxed = False
    if not res.success:
        res, ix = _build_and_solve(dd, cfg, binaries=False)
        relaxed = True
    if not res.success:
        raise RuntimeError(f"oracle infeasible: {res.message}")

    x = res.x
    dt = dd.dt_h
    oracle_cost = float(res.fun)
    real_cost = float(np.sum(dd.price_imp * dd.grid_import * dt
                             - dd.price_exp * dd.grid_export * dt))
    thr = float(np.sum((x[ix["c"]] + x[ix["d"]])) * dt)
    q = oracle_cost / real_cost if real_cost != 0 else float("nan")
    return DayResult(
        oracle_cost=round(oracle_cost, 4),
        real_cost=round(real_cost, 4),
        quotient=round(q, 4),
        battery_throughput_kwh=round(thr, 3),
        solver_status=res.message,
        relaxed=relaxed,
        oracle_plan={
            "g": np.round(x[ix["g"]], 3), "e": np.round(x[ix["e"]], 3),
            "c": np.round(x[ix["c"]], 3), "d": np.round(x[ix["d"]], 3),
            "h": np.round(x[ix["h"]], 3), "w": np.round(x[ix["w"]], 3),
            "E": np.round(x[ix["E"]], 3),
        },
    )
