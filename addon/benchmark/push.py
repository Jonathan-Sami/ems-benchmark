"""Push daily benchmark results to the community API.

Privacy contract: ONLY aggregate numbers + product metadata leave the house.
No raw sensor data, no addresses. household_id is a locally generated random
hash stored in /data/household_id (never derived from anything real).
"""
import json
import urllib.request


def push_day(api_url, payload, timeout=30):
    req = urllib.request.Request(
        api_url.rstrip("/") + "/api/v1/daily",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def build_payload(date, version, resolution_min, result, flags,
                  household_id, devices):
    return {
        "benchmark_version": version,
        "household_id": household_id,
        "date": date,
        "resolution_min": resolution_min,
        "quotient": result.quotient,
        "oracle_cost": result.oracle_cost,
        "real_cost": result.real_cost,
        "battery_throughput_kwh": result.battery_throughput_kwh,
        "relaxed": result.relaxed,
        "quality": flags,
        "devices": devices,   # manufacturer/model selections from options
    }
