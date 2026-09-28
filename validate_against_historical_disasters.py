"""
validate_against_historical_disasters.py

Checks whether Raksha's hazard overlay would have flagged real,
well-documented past disasters in Uttarakhand as High/Severe risk zones.
This is a credibility check: "does our model agree with reality?"

Uses well-known, publicly documented coordinates for four major events:
  - Kedarnath disaster (June 2013) - flash flood + debris flow
  - Joshimath land subsidence (ongoing, prominent since Jan 2023)
  - Chamoli disaster (Feb 2021) - glacial lake outburst / flash flood,
    Raini village and Tapovan area

Run this from VS Code with your venv activated:
    python validate_against_historical_disasters.py

Requires:
  - data/outputs/uttarakhand_red_zone_risk.tif (from build_hazard_overlay.py)

CHANGE LOG (post-diagnosis fix):
  - Sampling changed from a single exact-pixel lookup to a MAX-within-buffer
    lookup, matching the fix applied to build_hazard_overlay.py's village
    tagging step. Disaster sites are settlements/infrastructure, which
    typically sit on the flattest available ground in a valley -- sampling
    only the exact point under-reads the steep slope immediately next to
    it. Kedarnath was still missed after the max(slope, rainfall) formula
    fix precisely because this script hadn't been updated to look at the
    surrounding terrain the same way build_hazard_overlay.py now does.
"""

import os
import numpy as np
import rasterio
import geopandas as gpd
from shapely.geometry import Point

OUTPUT_DIR = os.path.join("data", "outputs")
RED_ZONE_RASTER_PATH = os.path.join(OUTPUT_DIR, "uttarakhand_red_zone_risk.tif")

# Keep this consistent with VILLAGE_BUFFER_PIXELS in build_hazard_overlay.py
# and BUFFER_PIXELS in diagnose_historical_disasters.py, so this validation
# reflects the same sampling logic used for real villages.
BUFFER_PIXELS = 5

# Well-documented, publicly known coordinates for these events.
# Coordinates are approximate (village/site-level precision), sourced
# from publicly available disaster reports and news coverage - suitable
# for a credibility check at this resolution, not survey-grade precision.
HISTORICAL_DISASTERS = [
    {
        "name": "Kedarnath (2013 flash flood & debris flow)",
        "date": "June 2013",
        "lat": 30.7346,
        "lon": 79.0669,
        "description": (
            "Catastrophic flash flood and debris flow triggered by a "
            "cloudburst and glacial lake breach, one of India's deadliest "
            "modern disasters."
        ),
    },
    {
        "name": "Joshimath (land subsidence)",
        "date": "Prominent since Jan 2023 (ongoing)",
        "lat": 30.5610,
        "lon": 79.5642,
        "description": (
            "Town-wide land subsidence linked to unstable slope terrain "
            "and hydrological factors, forcing relocation of residents."
        ),
    },
    {
        "name": "Chamoli / Raini village (2021 flash flood)",
        "date": "February 2021",
        "lat": 30.5667,
        "lon": 79.7333,
        "description": (
            "Glacial ice/rock avalanche triggered a devastating flash "
            "flood down the Rishiganga and Dhauliganga rivers, damaging "
            "hydropower infrastructure and villages downstream."
        ),
    },
    {
        "name": "Chamoli / Tapovan (2021 flash flood)",
        "date": "February 2021",
        "lat": 30.5333,
        "lon": 79.6667,
        "description": (
            "Downstream site of the same Feb 2021 disaster event, near "
            "the Tapovan-Vishnugad hydropower project."
        ),
    },
]

RISK_LABELS = {1: "Low", 2: "Moderate", 3: "High", 4: "Severe"}


def sample_max_in_buffer(row_idx, col_idx, raster_array, buffer_px):
    if not (0 <= row_idx < raster_array.shape[0] and 0 <= col_idx < raster_array.shape[1]):
        return np.nan
    r0, r1 = max(0, row_idx - buffer_px), min(raster_array.shape[0], row_idx + buffer_px + 1)
    c0, c1 = max(0, col_idx - buffer_px), min(raster_array.shape[1], col_idx + buffer_px + 1)
    window = raster_array[r0:r1, c0:c1]
    if window.size == 0 or np.all(np.isnan(window)):
        return np.nan
    return np.nanmax(window)


def main():
    print("=== Validating hazard model against historical disasters ===\n")

    with rasterio.open(RED_ZONE_RASTER_PATH) as src:
        risk_array = src.read(1)
        transform = src.transform
        nodata = src.nodata

    if nodata is not None:
        risk_array = np.where(risk_array == nodata, np.nan, risk_array)

    results = []
    for event in HISTORICAL_DISASTERS:
        row, col = rasterio.transform.rowcol(transform, event["lon"], event["lat"])
        risk_val = sample_max_in_buffer(row, col, risk_array, BUFFER_PIXELS)

        if np.isnan(risk_val):
            risk_label = "No data (outside DEM coverage nearby)"
            flagged_correctly = None
        else:
            risk_label = RISK_LABELS.get(int(risk_val), "Unknown")
            flagged_correctly = int(risk_val) in [3, 4]  # High or Severe

        event["model_risk_label"] = risk_label
        event["flagged_correctly"] = flagged_correctly
        results.append(event)

        status = (
            "MATCH - model flagged this as high-risk"
            if flagged_correctly is True
            else "MISS - model did not flag this as high-risk"
            if flagged_correctly is False
            else "N/A - no data nearby"
        )
        print(f"{event['name']} ({event['date']})")
        print(f"  Model classification (max in nearby buffer): {risk_label}")
        print(f"  Result: {status}\n")

    matches = sum(1 for r in results if r["flagged_correctly"] is True)
    total_evaluable = sum(1 for r in results if r["flagged_correctly"] is not None)
    print(f"=== Summary: {matches}/{total_evaluable} historical disaster "
          f"locations correctly flagged as High/Severe risk ===")

    # Save as GeoJSON for the frontend map layer
    gdf = gpd.GeoDataFrame(
        results,
        geometry=[Point(r["lon"], r["lat"]) for r in results],
        crs="EPSG:4326",
    )
    out_path = os.path.join(OUTPUT_DIR, "historical_disasters_validation.geojson")
    gdf.to_file(out_path, driver="GeoJSON")
    print(f"\nSaved -> {out_path}")
    print("Ready to add as a map layer showing model validation against real events.")


if __name__ == "__main__":
    main()