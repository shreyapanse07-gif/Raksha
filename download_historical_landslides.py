"""
download_historical_landslides.py

Downloads real historical landslide event locations for Uttarakhand from
NASA's COOLR (Cooperative Open Online Landslide Repository) via their
public ArcGIS REST API - no login/API key needed.

This gives us actual "ground truth" points: real places where landslides
have happened before. We'll use these to train an ML model in the next
script (train_landslide_model.py).

Run this from VS Code with your venv activated:
    python download_historical_landslides.py
"""

import os
import time
import requests
import geopandas as gpd

OUTPUT_DIR = os.path.join("data", "uttarakhand")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# NASA COOLR is mirrored across a few different ArcGIS hosts - try each
# in order, since any single host can be temporarily unreachable.
COOLR_URLS = [
    "https://maps.nccs.nasa.gov/mapping/rest/services/COOLR/COOLR_Events_Point/FeatureServer/0/query",
    "https://gis.earthdata.nasa.gov/gis05/rest/services/Landslides/COOLR_Events_Points/FeatureServer/0/query",
    "https://maps.disasters.nasa.gov/ags01/rest/services/Hosted/nasa_glc_poly_point/FeatureServer/0/query",
]

# Uttarakhand bounding box (west, south, east, north) - same as we've used
# throughout the project
BBOX = {"xmin": 77.5, "ymin": 28.7, "xmax": 81.1, "ymax": 31.5}


def fetch_landslides(max_retries_per_url=2):
    params = {
        "where": "1=1",
        "outFields": "*",
        "geometry": f"{BBOX['xmin']},{BBOX['ymin']},{BBOX['xmax']},{BBOX['ymax']}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outSR": "4326",
        "f": "geojson",
    }
    headers = {"User-Agent": "UttarakhandRedZoneProject/1.0 (student research project)"}

    for url in COOLR_URLS:
        for attempt in range(1, max_retries_per_url + 1):
            try:
                print(f"Querying {url} (attempt {attempt}/{max_retries_per_url})...")
                response = requests.get(url, params=params, headers=headers, timeout=60)
                response.raise_for_status()
                data = response.json()
                if "error" in data:
                    raise ValueError(f"API returned error: {data['error']}")
                n_features = len(data.get("features", []))
                print(f"  Success - received {n_features} landslide records.")
                return data
            except Exception as e:
                print(f"  Attempt {attempt} failed: {e}")
                if attempt < max_retries_per_url:
                    time.sleep(8)
        print(f"  Giving up on this host, trying next mirror if available...\n")
    print("  All mirrors failed.")
    return None


# ---------------------------------------------------------------------------
# FETCH AND SAVE
# ---------------------------------------------------------------------------
data = fetch_landslides()

if data and data.get("features"):
    gdf = gpd.GeoDataFrame.from_features(data["features"], crs="EPSG:4326")
    out_path = os.path.join(OUTPUT_DIR, "uttarakhand_historical_landslides.geojson")
    gdf.to_file(out_path, driver="GeoJSON")
    print(f"\nSaved {len(gdf)} historical landslide events -> {out_path}")

    if len(gdf) < 15:
        print(
            "\nNOTE: This is a fairly small number of historical points for "
            "training an ML model reliably. This is common for a single "
            "state - COOLR's coverage varies by region and reporting "
            "history. We'll supplement with synthetic points derived from "
            "known high-risk zones (e.g. Kedarnath, Joshimath corridor) "
            "in the next script if needed."
        )
else:
    print(
        "\nCould not fetch data automatically. Manual fallback:\n"
        "1. Go to https://landslides.nasa.gov/viewer\n"
        "2. Pan/zoom to Uttarakhand, India\n"
        "3. Use the export/download option to get a CSV or GeoJSON\n"
        "4. Save it as: data/uttarakhand/uttarakhand_historical_landslides.geojson\n"
        "   (convert from CSV using geopandas if needed: \n"
        "   gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.longitude, df.latitude)))"
    )