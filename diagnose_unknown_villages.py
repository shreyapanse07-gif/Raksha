"""
diagnose_unknown_villages.py

Investigates why ~8,000 villages got tagged "Unknown" in the risk overlay.
Checks the most likely causes:
  1. Villages falling outside the DEM's actual coverage extent
  2. Villages landing exactly on nodata/edge pixels
  3. CRS mismatches between villages and raster
  4. Villages with missing/invalid geometry

Run this from VS Code with your venv activated:
    python diagnose_unknown_villages.py
"""

import os
import numpy as np
import rasterio
import geopandas as gpd

DATA_DIR = os.path.join("data", "uttarakhand")
OUTPUT_DIR = os.path.join("data", "outputs")

DEM_PATH = os.path.join(DATA_DIR, "uttarakhand_dem.tif")
TAGGED_VILLAGES_PATH = os.path.join(OUTPUT_DIR, "uttarakhand_villages_risk_tagged.geojson")


# ---------------------------------------------------------------------------
# LOAD DATA
# ---------------------------------------------------------------------------
villages_gdf = gpd.read_file(TAGGED_VILLAGES_PATH)
print(f"Total villages: {len(villages_gdf)}")
print(f"Villages CRS: {villages_gdf.crs}")

with rasterio.open(DEM_PATH) as dem_src:
    dem_bounds = dem_src.bounds
    dem_crs = dem_src.crs
    dem_transform = dem_src.transform
    dem_array = dem_src.read(1)
    dem_nodata = dem_src.nodata

print(f"DEM CRS: {dem_crs}")
print(f"DEM bounds: {dem_bounds}")
print(f"DEM nodata value: {dem_nodata}")
print(f"DEM shape: {dem_array.shape}")


# ---------------------------------------------------------------------------
# CHECK 1: CRS MATCH
# ---------------------------------------------------------------------------
print("\n=== Check 1: CRS match ===")
if str(villages_gdf.crs) != str(dem_crs):
    print(f"MISMATCH! Villages are in {villages_gdf.crs}, DEM is in {dem_crs}")
    print("This alone could explain misaligned sampling.")
else:
    print("CRS matches - not the cause.")


# ---------------------------------------------------------------------------
# CHECK 2: HOW MANY UNKNOWN VILLAGES, AND WHY
# ---------------------------------------------------------------------------
print("\n=== Check 2: Investigating Unknown villages ===")
unknown_villages = villages_gdf[villages_gdf["risk_label"] == "Unknown"].copy()
print(f"Unknown villages: {len(unknown_villages)}")

outside_bounds_count = 0
nodata_pixel_count = 0
invalid_geom_count = 0
other_count = 0

for idx, row in unknown_villages.iterrows():
    geom = row.geometry
    if geom is None or geom.is_empty:
        invalid_geom_count += 1
        continue

    x, y = geom.x, geom.y

    # Check if point is outside DEM's bounding box entirely
    if not (dem_bounds.left <= x <= dem_bounds.right and
            dem_bounds.bottom <= y <= dem_bounds.top):
        outside_bounds_count += 1
        continue

    # Point is within bounds - check if it landed on a nodata pixel
    row_idx, col_idx = rasterio.transform.rowcol(dem_transform, x, y)
    if 0 <= row_idx < dem_array.shape[0] and 0 <= col_idx < dem_array.shape[1]:
        pixel_value = dem_array[row_idx, col_idx]
        if dem_nodata is not None and pixel_value == dem_nodata:
            nodata_pixel_count += 1
        elif np.isnan(pixel_value):
            nodata_pixel_count += 1
        else:
            other_count += 1
    else:
        outside_bounds_count += 1

print(f"\nBreakdown of {len(unknown_villages)} Unknown villages:")
print(f"  Outside DEM bounds entirely: {outside_bounds_count}")
print(f"  Within bounds but on nodata/edge pixel: {nodata_pixel_count}")
print(f"  Invalid/empty geometry: {invalid_geom_count}")
print(f"  Other/unexplained (valid pixel, but still flagged Unknown): {other_count}")

if outside_bounds_count > 0:
    # Show a few examples of out-of-bounds points for sanity checking
    print("\nSample out-of-bounds village coordinates (first 5):")
    sample_count = 0
    for idx, row in unknown_villages.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        x, y = geom.x, geom.y
        if not (dem_bounds.left <= x <= dem_bounds.right and
                dem_bounds.bottom <= y <= dem_bounds.top):
            name = row.get("name", "unnamed")
            print(f"  {name}: ({x:.4f}, {y:.4f})")
            sample_count += 1
            if sample_count >= 5:
                break

print(f"\nDEM bounds for reference: "
      f"lon [{dem_bounds.left:.4f}, {dem_bounds.right:.4f}], "
      f"lat [{dem_bounds.bottom:.4f}, {dem_bounds.top:.4f}]")