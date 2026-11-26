"""Synthetic end-to-end test: known day, dumb EMS behaviour, benchmark must
score it < 1 and the oracle must beat the dumb behaviour."""
import sys, os, time
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "addon"))
from benchmark.model import run_day, DayData, BenchConfig, BatteryConfig, DeviceLimits

N = 1440                       # 1-minute resolution
dt = 1/60.0

# --- prices: EPEX-like: cheap 0-6h and 12-15h, evening spike 17-20h
hours = np.arange(N) / 60.0
price = np.where((hours < 6) | ((hours >= 12) & (hours < 15)), 0.18, 0.0)
price = np.where((hours >= 17) & (hours < 20), 0.42, price)
price = np.where(price == 0, 0.28, price)
price_exp = price * 0.8

# --- PV winter day, cloudier: bump + noise
pv = np.clip(3.0 * np.exp(-((hours - 12.5) ** 2) / 6.0) *
             (1 + 0.3 * np.sin(hours * 20)), 0, 4.0)

# --- base load
base = np.full(N, 0.35) + np.where((hours > 6) & (hours < 9), 0.6, 0) \
       + np.where((hours > 18) & (hours < 23), 0.5, 0)

# --- dumb EMS (real side): battery charges on surplus only, discharges when
#     battery full-ish and house load; wallbox charges at full 11kW from 19-22
hp = np.where((hours >= 5) & (hours < 8), 2.0,
     np.where((hours >= 16) & (hours < 19), 2.0, 0.4))
wb = np.where((hours >= 19) & (hours < 22), 11.0, 0.0)

pv0 = np.nan_to_num(pv)
supply = pv0
load = base + hp + wb
net = load - supply                     # >0 import
imp = np.clip(net, 0, None)
exp = np.clip(-net, 0, None)

# battery naive: surplus -> charge up to 10 kWh, deficit -> discharge
soc = 5.0
bat_c = np.zeros(N); bat_d = np.zeros(N)
for t in range(N):
    surplus = supply[t] - load[t]
    if surplus > 0 and soc < 10:
        c = min(surplus, 5.0, (10 - soc) / dt)
        bat_c[t] = c; soc += c * dt
        exp[t] = max(surplus - c, 0.0)
    elif surplus < 0 and soc > 0:
        d = min(-surplus, 5.0, soc / dt)
        bat_d[t] = d; soc -= d * dt
        imp[t] = max(imp[t] + surplus + d, 0)

dd = DayData(
    pv=pv0, base_load=base, price_imp=price, price_exp=price_exp,
    hp_energy_kwh=float(np.sum(hp)) * dt,
    wb_energy_kwh=float(np.sum(wb)) * dt,
    wb_window=(hours >= 19) & (hours < 22.001),
    grid_import=imp, grid_export=exp, dt_h=dt)

cfg = BenchConfig(
    battery=BatteryConfig(capacity_kwh=10, p_charge_max=5, p_discharge_max=5),
    hp=DeviceLimits(p_min=0.5, p_max=2.0),
    wb=DeviceLimits(p_min=1.0, p_max=11.0),
    kappa=0.05)

t0 = time.time()
r = run_day(dd, cfg)
wall = time.time() - t0
print(f"oracle  : {r.oracle_cost:8.2f} EUR   ({r.solver_status}, relaxed={r.relaxed})")
print(f"real    : {r.real_cost:8.2f} EUR")
print(f"quotient: {r.quotient:.4f}")
print(f"battery throughput oracle: {r.battery_throughput_kwh} kWh")
print(f"wall time (1440 slots, MILP): {wall:.1f}s")
assert r.oracle_cost <= r.real_cost + 1e-6, "oracle must not be worse than reality"
assert 0 < r.quotient < 1, "quotient should be strictly between 0 and 1 here"
# sanity: oracle should charge cheap & discharge during spike
plan = r.oracle_plan
night_c = plan["c"][(hours < 6)].sum() * dt
spike_d = plan["d"][(hours >= 17) & (hours < 20)].sum() * dt
print(f"oracle night charge (cheap): {night_c:.1f} kWh, spike discharge: {spike_d:.1f} kWh")
assert spike_d > 3, "oracle should discharge during the evening spike"
print("ALL CHECKS PASSED")
