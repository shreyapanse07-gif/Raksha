"""
calibrate_buffer_size.py

Finds the right buffer size empirically, instead of guessing one -- and,
critically, checks BOTH sides: does it catch real disasters (recall) AND
does it correctly leave known-safe places alone (precision)?

Testing only the 4 historical disasters was a methodological gap: 4/4
matches is trivially easy to get by flagging almost everything (a model
that marks the whole state "High risk" also scores 4/4 -- it just has zero
discriminative power). That's exactly what happened: even at buffer=0,
switching to max(slope_risk, rainfall_risk) alone flagged 29% of all
15,201 villages as High/Severe. Buffer size wasn't the main problem --
the formula's OR-logic was already too permissive on its own.

This script now also checks a handful of known-SAFE reference towns
(flat Doon Valley / Terai plains locations with no landslide history) as
negative controls, alongside the 4 known disasters as positive controls.
You want a configuration that scores well on BOTH: catches the real
disasters AND doesn't false-alarm on clearly safe, flat towns.

Run from VS Code with your venv activated, after build_hazard_overlay.py
has already produced uttarakhand_red_zone_risk.tif:
    python calibrate_buffer_size.py

Requires:
  - data/outputs/uttarakhand_red_zone_risk.tif (from build_hazard_overlay.py)
  - data/uttarakhand/uttarakhand_villages.geojson
  - data/uttarakhand/uttarakhand_boundary.geojson (optional, for the same
    boundary filtering build_hazard_overlay.py applies)
"""

import os
import numpy as np
import rasterio
import geopandas as gpd

DATA_DIR = os.path.join("data", "uttarakhand")
OUTPUT_DIR = os.path.join("data", "outputs")
RED_ZONE_RASTER_PATH = os.path.join(OUTPUT_DIR, "uttarakhand_red_zone_risk.tif")
VILLAGES_PATH = os.path.join(DATA_DIR, "uttarakhand_villages.geojson")
BOUNDARY_PATH = os.path.join(DATA_DIR, "uttarakhand_boundary.geojson")

BUFFER_SIZES_TO_TEST = [0, 1, 2, 3, 4, 5]

HISTORICAL_DISASTERS = [
    {"name": "Kedarnath", "lat": 30.7346, "lon": 79.0669},
    {"name": "Joshimath", "lat": 30.5610, "lon": 79.5642},
    {"name": "Chamoli/Raini", "lat": 30.5667, "lon": 79.7333},
    {"name": "Chamoli/Tapovan", "lat": 30.5333, "lon": 79.6667},
]

# Known-safe reference sites: flat Doon Valley / Terai plains towns with no
# landslide history. These are negative controls -- the model should NOT
# flag these as High/Severe. Coordinates are approximate town centers.
KNOWN_SAFE_SITES = [
    {"name": "Dehradun (Doon Valley)", "lat": 30.3165, "lon": 78.0322},
    {"name": "Haridwar (Ganga plains)", "lat": 29.9457, "lon": 78.1642},
    {"name": "Rishikesh (valley floor)", "lat": 30.0869, "lon": 78.2676},
    {"name": "Haldwani (Terai plains)", "lat": 29.2183, "lon": 79.5130},
]


def sample_max_in_buffer(row_idx, col_idx, raster_array, buffer_px):
    if not (0 <= row_idx < raster_array.shape[0] and 0 <= col_idx < raster_array.shape[1]):
        return np.nan
    if buffer_px == 0:
        return raster_array[row_idx, col_idx]
    r0, r1 = max(0, row_idx - buffer_px), min(raster_array.shape[0], row_idx + buffer_px + 1)
    c0, c1 = max(0, col_idx - buffer_px), min(raster_array.shape[1], col_idx + buffer_px + 1)
    window = raster_array[r0:r1, c0:c1]
    if window.size == 0 or np.all(np.isnan(window)):
        return np.nan
    return np.nanmax(window)


def main():
    print("=== Calibrating buffer size against real disasters + statewide impact ===\n")

    with rasterio.open(RED_ZONE_RASTER_PATH) as src:
        risk_array = src.read(1)
        transform = src.transform
        nodata = src.nodata
        pixel_size_m = abs(src.transform[0]) * 111320

    if nodata is not None:
        risk_array = np.where(risk_array == nodata, np.nan, risk_array)

    villages_gdf = gpd.read_file(VILLAGES_PATH)
    if os.path.exists(BOUNDARY_PATH):
        boundary_gdf = gpd.read_file(BOUNDARY_PATH)
        boundary_union = boundary_gdf.geometry.union_all()
        villages_gdf = villages_gdf[villages_gdf.geometry.within(boundary_union)].copy()
    total_villages = len(villages_gdf)

    print(f"{'Buffer(px)':<12}{'~Radius(m)':<12}{'Disasters':<12}{'Safe false-alarms':<20}{'High/Severe villages':<24}{'% of state'}")
    print("-" * 100)

    for buffer_px in BUFFER_SIZES_TO_TEST:
        # Historical disaster check (want these flagged -- true positives)
        matches = 0
        for event in HISTORICAL_DISASTERS:
            row, col = rasterio.transform.rowcol(transform, event["lon"], event["lat"])
            val = sample_max_in_buffer(row, col, risk_array, buffer_px)
            if not np.isnan(val) and int(val) in (3, 4):
                matches += 1

        # Known-safe site check (want these NOT flagged -- false positives if they are)
        false_alarms = 0
        for site in KNOWN_SAFE_SITES:
            row, col = rasterio.transform.rowcol(transform, site["lon"], site["lat"])
            val = sample_max_in_buffer(row, col, risk_array, buffer_px)
            if not np.isnan(val) and int(val) in (3, 4):
                false_alarms += 1

        # Statewide village check
        high_severe_count = 0
        for geom in villages_gdf.geometry:
            if geom is None:
                continue
            row, col = rasterio.transform.rowcol(transform, geom.x, geom.y)
            val = sample_max_in_buffer(row, col, risk_array, buffer_px)
            if not np.isnan(val) and int(val) in (3, 4):
                high_severe_count += 1

        pct = (high_severe_count / total_villages) * 100 if total_villages else 0
        radius_m = buffer_px * pixel_size_m
        print(f"{buffer_px:<12}{radius_m:<12.0f}{matches}/4{'':<9}{false_alarms}/{len(KNOWN_SAFE_SITES)}{'':<15}{high_severe_count:<24}{pct:.1f}%")

    print(
        "\nLook for the buffer size with the BEST BALANCE: high disaster matches "
        "(ideally 4/4), LOW safe false-alarms (ideally 0), and a plausible "
        "statewide percentage (not 90%+). If every buffer size that gets 4/4 "
        "disasters ALSO false-alarms on the safe sites, the max() formula "
        "itself needs more work (e.g. requiring the extreme band specifically, "
        "or bringing rainfall back in as a secondary check) -- buffer size "
        "alone won't fix that. Share the full table and we'll decide the next "
        "step together."
    )


if __name__ == "__main__":
    main()