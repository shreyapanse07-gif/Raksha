"""
hazard_predictor.py
--------------------
Turns Raksha's static susceptibility map into a time-bound hazard prediction by
combining each high-risk village's terrain risk level with a short-term rainfall
forecast. Output: which villages are actively at risk in the next 1-3 days, not
just which ones sit on dangerous terrain.

WHY THIS EXISTS
Your slope+rainfall-average model tells you WHERE a landslide could happen.
It can't tell you WHEN. This script adds the "when" by checking incoming rainfall
against a trigger threshold, so a High-risk village only fires an alert when
conditions are actually turning dangerous.

DATA SOURCE
Open-Meteo Forecast API - free, no API key required.
Docs: https://open-meteo.com/en/docs

USAGE
    python hazard_predictor.py --input data/outputs/villages_red_alert.csv --output alerts.json

INPUT CSV columns expected (matches villages_red_alert.csv from
red_alert_priority.py -- Layer 2's output, NOT build_hazard_overlay.py's
raw priority_villages.csv):
    name, latitude, longitude, risk_label, response_priority

By default, only villages with response_priority == "Immediate Relocation"
are checked against the live rainfall forecast (use --include-short-term to
also include the "Short-Term Relocation" tier). This is deliberate: Layer 1
(build_hazard_overlay.py) may legitimately flag thousands of villages as
being in a hazard zone somewhere in a mountainous state -- that's a fact
about the terrain, not a bug. Layer 2 (red_alert_priority.py) narrows that
down to the villages that most urgently need attention, considering
population, relocation-site distance, and healthcare access, and labels
that narrowed list with the problem statement's own language: Immediate /
Short-Term / Medium-Term Relocation. Checking a live forecast against
thousands of villages is both slow and not meaningfully more informative
than checking the ones Layer 2 has already identified as most urgent.

CHANGE LOG (bug fixes):
  - Column names updated to match what build_hazard_overlay.py actually
    writes (`name` and `risk_label`), not the originally-assumed
    `village_name` and `risk_level`.
  - Input source changed from Layer 1's raw priority_villages.csv (which
    can legitimately contain thousands of villages statewide) to Layer 2's
    villages_red_alert.csv, filtered to the CRITICAL response_priority
    tier by default -- this is what actually keeps the live-forecast list
    small and actionable, regardless of how large Layer 1's hazard-zone
    output is.
  - Timeout reduced from 15s to 5s per API call, and per-village status is
    now printed immediately so progress is visible during a run.
"""

import argparse
import csv
import json
import time
from urllib.request import urlopen
from urllib.parse import urlencode

# ---------------------------------------------------------------------------
# Rainfall trigger thresholds (mm of rain in a 24h period that meaningfully
# raises landslide likelihood on that terrain type).
#
# These starting values are rough, literature-informed defaults for Himalayan
# terrain (steeper/already-risky ground needs less rain to trigger a slide
# than gentler ground). TREAT THESE AS PLACEHOLDERS.
#
# To calibrate properly: pull COOLR's historical landslide events for
# Uttarakhand, get the rainfall recorded on/before each event date, and set
# these thresholds to match what actually preceded real slides in your region.
# That turns this from "reasonable guess" into "evidence-based," which is a
# much stronger claim to make in your pitch.
# ---------------------------------------------------------------------------
THRESHOLDS_MM_24H = {
    "Low": 150,
    "Moderate": 120,
    "High": 90,
    "Severe": 60,
}

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def fetch_rainfall_forecast(lat, lon, days=3):
    """Fetch daily precipitation sum forecast (mm) for the next `days` days."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "precipitation_sum",
        "forecast_days": days,
        "timezone": "Asia/Kolkata",
    }
    url = f"{OPEN_METEO_URL}?{urlencode(params)}"
    with urlopen(url, timeout=5) as resp:
        data = json.loads(resp.read().decode())
    dates = data["daily"]["time"]
    precip = data["daily"]["precipitation_sum"]
    return list(zip(dates, precip))


def evaluate_village(village_name, lat, lon, risk_level):
    """Check one village's rainfall forecast against its risk-level threshold."""
    threshold = THRESHOLDS_MM_24H.get(risk_level, 999)

    try:
        forecast = fetch_rainfall_forecast(lat, lon)
    except Exception as e:
        return {
            "village": village_name,
            "risk_level": risk_level,
            "status": "error",
            "error": str(e),
        }

    alerts = []
    for date, mm in forecast:
        if mm is not None and mm >= threshold:
            alerts.append({"date": date, "forecast_mm": mm, "threshold_mm": threshold})

    if alerts:
        status = "ALERT"
    elif risk_level in ("High", "Severe"):
        status = "watch"
    else:
        status = "normal"

    return {
        "village": village_name,
        "latitude": lat,
        "longitude": lon,
        "risk_level": risk_level,
        "trigger_threshold_mm": threshold,
        "forecast": [{"date": d, "forecast_mm": mm} for d, mm in forecast],
        "active_alerts": alerts,
        "status": status,
    }


def main():
    parser = argparse.ArgumentParser(description="Predict near-term landslide hazard alerts")
    parser.add_argument("--input", required=True, help="CSV of villages (villages_red_alert.csv)")
    parser.add_argument("--output", default="alerts.json", help="Output JSON path")
    parser.add_argument(
        "--delay", type=float, default=0.5,
        help="Seconds between API calls (be polite to the free API)"
    )
    parser.add_argument(
        "--include-short-term", action="store_true",
        help="Also include the Short-Term Relocation tier, not just Immediate"
    )
    parser.add_argument(
        "--all-tiers", action="store_true",
        help="Skip response_priority filtering entirely and check every row "
             "in the input file (slow if the file is large -- use with caution)"
    )
    args = parser.parse_args()

    tiers_to_include = {"Immediate Relocation"}
    if args.include_short_term:
        tiers_to_include.add("Short-Term Relocation")

    results = []
    skipped = 0
    with open(args.input, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not args.all_tiers:
                priority = row.get("response_priority", "")
                if priority not in tiers_to_include:
                    skipped += 1
                    continue

            village = row["name"]
            lat = float(row["latitude"])
            lon = float(row["longitude"])
            risk_level = row.get("risk_label", "High")
            print(f"Checking {village}...", end=" ", flush=True)
            result = evaluate_village(village, lat, lon, risk_level)
            results.append(result)
            print(f"[{result.get('status', 'unknown')}]")
            time.sleep(args.delay)

    if not args.all_tiers:
        print(f"\n(Skipped {skipped} rows outside {sorted(tiers_to_include)} tier(s) "
              f"-- use --include-short-term or --all-tiers to widen this.)")

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    active = [r for r in results if r.get("status") == "ALERT"]
    watch = [r for r in results if r.get("status") == "watch"]
    print(f"\nDone. {len(results)} villages checked.")
    print(f"  ALERT (rain forecast crosses trigger threshold): {len(active)}")
    print(f"  Watch (High/Severe risk, no trigger yet): {len(watch)}")
    print(f"Full results written to {args.output}")


if __name__ == "__main__":
    main()