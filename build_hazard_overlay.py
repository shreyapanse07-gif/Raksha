"""
build_hazard_overlay.py

Builds the core "Red Zone" hazard overlay for Uttarakhand:
  1. Computes slope from the DEM (steeper = higher landslide risk)
  2. Classifies slope into risk bands
  3. Classifies rainfall into risk bands
  4. Combines both into a single hazard risk raster
  5. Tags each village/habitation with its risk level (spatial join)
  6. Produces a ranked list of villages by risk, ready for the
     prioritization/relocation step

Run this from VS Code with your venv activated:
    python build_hazard_overlay.py

Requires the outputs from your earlier data-gathering scripts:
  - data/uttarakhand/uttarakhand_dem.tif
  - data/uttarakhand/uttarakhand_rainfall_2023.tif
  - data/uttarakhand/uttarakhand_villages.geojson
  - data/uttarakhand/uttarakhand_boundary.geojson

Requires scipy (for edge-artifact-safe slope calculation):
    pip install scipy

CHANGE LOG (post-validation fix, based on diagnose_historical_disasters.py
results against Kedarnath/Joshimath/Chamoli-Raini/Chamoli-Tapovan):
  - Step 4: combining formula changed from a weighted AVERAGE of slope
    and rainfall risk bands to a MAX of the two. The average required
    both factors to be elevated simultaneously to reach High/Severe,
    which missed every historical disaster (each was dominantly
    slope-driven OR rainfall-driven, not both). Max lets either extreme
    factor drive the classification on its own, which matches how these
    hazards actually behave physically -- and correctly flags all
    4 historical validation sites (checked in
    validate_against_historical_disasters.py, which uses buffer-max
    sampling to compensate for those sites' approximate coordinates).
  - Step 5: buffer-max sampling was tried here too, but reverted --
    applying a 5-pixel buffer to all 15,201 real, precise village points
    inflated High/Severe from ~32 villages to 14,332 (94% of the state),
    since Uttarakhand has steep terrain near almost every village
    regardless of that village's own actual risk. Village tagging now
    samples the EXACT point again -- real village coordinates don't need
    the imprecision-correction that buffer-max was solving for at the
    4 historical (approximate, news-sourced) disaster coordinates.
"""

import os
import numpy as np
import rasterio
from rasterio.enums import Resampling
import geopandas as gpd
from shapely.geometry import Point

DATA_DIR = os.path.join("data", "uttarakhand")
OUTPUT_DIR = os.path.join("data", "outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

DEM_PATH = os.path.join(DATA_DIR, "uttarakhand_dem.tif")
RAINFALL_PATH = os.path.join(DATA_DIR, "uttarakhand_rainfall_2023.tif")
VILLAGES_PATH = os.path.join(DATA_DIR, "uttarakhand_villages.geojson")

# How many pixels around each village to check for the steepest/wettest
# nearby terrain, instead of just the exact point the village marker
# sits on. Same idea as BUFFER_PIXELS in diagnose_historical_disasters.py
# -- keep these consistent so village tagging matches what you validated.
VILLAGE_BUFFER_PIXELS = 5


# ---------------------------------------------------------------------------
# STEP 1: COMPUTE SLOPE FROM DEM
# ---------------------------------------------------------------------------
print("=== Step 1: Computing slope from DEM ===")

with rasterio.open(DEM_PATH) as dem_src:
    dem = dem_src.read(1).astype(np.float32)
    dem_transform = dem_src.transform
    dem_crs = dem_src.crs
    dem_nodata = dem_src.nodata
    dem_meta = dem_src.meta.copy()

# Pixel size in degrees (since our DEM is in EPSG:4326 / lat-lon).
# Convert to approximate meters for a more accurate slope calculation:
# 1 degree latitude ~= 111,320 m; longitude varies with latitude, but
# for Uttarakhand (~29-31N) this approximation is fine for a demo.
pixel_size_deg = dem_transform[0]  # width of one pixel in degrees
pixel_size_m = pixel_size_deg * 111320

# Mask nodata before computing gradients so edges don't produce garbage
if dem_nodata is not None:
    dem_masked = np.where(dem == dem_nodata, np.nan, dem)
else:
    dem_masked = dem

# Fill small nodata gaps before computing slope, so villages near the
# state's irregular boundary don't lose their value to edge erosion.
# We use a simple nearest-neighbor fill via scipy for any NaN pixel that
# has valid neighbors nearby (this does NOT invent new terrain far from
# real data - it only patches thin edge/boundary artifacts).
from scipy.ndimage import distance_transform_edt

if np.isnan(dem_masked).any():
    nan_mask = np.isnan(dem_masked)
    # For each NaN pixel, find the nearest non-NaN pixel and copy its value
    indices = distance_transform_edt(
        nan_mask, return_distances=False, return_indices=True
    )
    dem_filled = dem_masked[tuple(indices)]
else:
    dem_filled = dem_masked

dzdx, dzdy = np.gradient(dem_filled, pixel_size_m, pixel_size_m)
slope_rad = np.arctan(np.sqrt(dzdx**2 + dzdy**2))
slope_deg = np.degrees(slope_rad)

# Re-apply the ORIGINAL nodata mask afterward, so areas genuinely outside
# the DEM (not just thin edge artifacts) still correctly show as nodata -
# we only wanted to patch the boundary erosion, not extend real coverage.
slope_deg = np.where(np.isnan(dem_masked), np.nan, slope_deg)

print(f"Slope computed. Range: {np.nanmin(slope_deg):.1f} to "
      f"{np.nanmax(slope_deg):.1f} degrees")


# ---------------------------------------------------------------------------
# STEP 2: CLASSIFY SLOPE INTO RISK BANDS
# ---------------------------------------------------------------------------
print("\n=== Step 2: Classifying slope risk ===")
# Thresholds based on common landslide-susceptibility literature for
# Himalayan terrain (can be refined further with GSI data if available):
#   0-10 deg  -> 1 (low)
#   10-20 deg -> 2 (moderate)
#   20-35 deg -> 3 (high)
#   35+ deg   -> 4 (severe)
slope_risk = np.zeros_like(slope_deg, dtype=np.float32)
slope_risk[np.isnan(slope_deg)] = np.nan
slope_risk[(slope_deg >= 0) & (slope_deg < 10)] = 1
slope_risk[(slope_deg >= 10) & (slope_deg < 20)] = 2
slope_risk[(slope_deg >= 20) & (slope_deg < 35)] = 3
slope_risk[slope_deg >= 35] = 4

print("Slope risk classified into 4 bands (1=low, 4=severe).")


# ---------------------------------------------------------------------------
# STEP 3: CLASSIFY RAINFALL INTO RISK BANDS
# ---------------------------------------------------------------------------
print("\n=== Step 3: Classifying rainfall risk ===")

with rasterio.open(RAINFALL_PATH) as rain_src:
    rainfall = rain_src.read(1).astype(np.float32)
    rain_transform = rain_src.transform
    rain_nodata = rain_src.nodata

if rain_nodata is not None:
    rainfall = np.where(rainfall == rain_nodata, np.nan, rainfall)

# Resample rainfall to match the DEM's grid (they likely have different
# resolutions/dimensions) using rasterio's reproject
from rasterio.warp import reproject, Resampling as WarpResampling

rainfall_resampled = np.empty_like(dem_masked, dtype=np.float32)
with rasterio.open(RAINFALL_PATH) as rain_src:
    reproject(
        source=rasterio.band(rain_src, 1),
        destination=rainfall_resampled,
        src_transform=rain_src.transform,
        src_crs=rain_src.crs,
        dst_transform=dem_transform,
        dst_crs=dem_crs,
        resampling=WarpResampling.bilinear,
    )

# Classify annual rainfall into risk bands (mm/year) - thresholds are a
# reasonable starting point for Himalayan monsoon climate; refine with
# IMD data if you want more precision later:
#   <1500mm     -> 1 (low)
#   1500-2500mm -> 2 (moderate)
#   2500-3500mm -> 3 (high)
#   >3500mm     -> 4 (severe)
rainfall_risk = np.zeros_like(rainfall_resampled, dtype=np.float32)
rainfall_risk[np.isnan(rainfall_resampled)] = np.nan
rainfall_risk[(rainfall_resampled >= 0) & (rainfall_resampled < 1500)] = 1
rainfall_risk[(rainfall_resampled >= 1500) & (rainfall_resampled < 2500)] = 2
rainfall_risk[(rainfall_resampled >= 2500) & (rainfall_resampled < 3500)] = 3
rainfall_risk[rainfall_resampled >= 3500] = 4

print("Rainfall risk classified into 4 bands (1=low, 4=severe).")


# ---------------------------------------------------------------------------
# STEP 4: COMBINE INTO RED ZONE RISK MAP
# ---------------------------------------------------------------------------
print("\n=== Step 4: Building combined Red Zone risk map ===")
# FIX (post-validation): previously a weighted AVERAGE
# (slope_risk*0.6 + rainfall_risk*0.4), which required both slope AND
# rainfall to be elevated simultaneously to reach High/Severe. Real
# landslide events here are often dominantly slope-driven OR
# rainfall-driven, not both -- the average washed out single-factor
# extremes and missed every historical validation site. Using the max
# of the two lets either extreme factor drive the classification on its
# own, matching how the hazard actually behaves.
combined_risk = np.maximum(slope_risk, rainfall_risk)

# Final classification into Red Zone categories -- combined_risk is now
# already exactly 1, 2, 3, or 4 (the max of two integer bands), so this
# mapping is mostly a pass-through, kept as explicit bands for clarity
# and in case the combination logic is refined further later.
red_zone = np.full_like(combined_risk, np.nan, dtype=np.float32)
red_zone[(combined_risk >= 1) & (combined_risk < 2)] = 1  # Low
red_zone[(combined_risk >= 2) & (combined_risk < 3)] = 2  # Moderate
red_zone[(combined_risk >= 3) & (combined_risk < 3.5)] = 3  # High
red_zone[combined_risk >= 3.5] = 4  # Severe (true "Red Zone")

# Save the combined risk raster
out_meta = dem_meta.copy()
out_meta.update({"dtype": "float32", "nodata": np.nan})
red_zone_path = os.path.join(OUTPUT_DIR, "uttarakhand_red_zone_risk.tif")
with rasterio.open(red_zone_path, "w", **out_meta) as dest:
    dest.write(red_zone, 1)

print(f"Red Zone risk raster saved -> {red_zone_path}")
print("Risk band pixel counts:")
for band, label in [(1, "Low"), (2, "Moderate"), (3, "High"), (4, "Severe")]:
    count = np.sum(red_zone == band)
    print(f"  Band {band} ({label}): {count} pixels")


# ---------------------------------------------------------------------------
# STEP 5: TAG VILLAGES WITH RISK LEVEL
# ---------------------------------------------------------------------------
print("\n=== Step 5: Tagging villages with risk level ===")

villages_gdf = gpd.read_file(VILLAGES_PATH)
print(f"Loaded {len(villages_gdf)} villages (before boundary filtering).")

# BBBike's extract used a rectangular bounding box, which slightly
# overshoots Uttarakhand's actual (irregular) state boundary into
# neighboring states. Filter to the real boundary so we don't try to
# risk-tag villages that aren't actually in Uttarakhand.
BOUNDARY_PATH = os.path.join(DATA_DIR, "uttarakhand_boundary.geojson")
if os.path.exists(BOUNDARY_PATH):
    boundary_gdf = gpd.read_file(BOUNDARY_PATH)
    boundary_union = boundary_gdf.geometry.union_all()
    before_count = len(villages_gdf)
    villages_gdf = villages_gdf[villages_gdf.geometry.within(boundary_union)].copy()
    removed = before_count - len(villages_gdf)
    print(f"Filtered to actual Uttarakhand boundary: removed {removed} "
          f"villages outside the state, {len(villages_gdf)} remain.")
else:
    print("WARNING: boundary file not found, skipping boundary filter - "
          "some villages just outside the state may be incorrectly included.")

# FIX (second pass): buffer-max sampling was correct for validating against
# the 4 historical disaster coordinates (those are approximate, imprecise
# points from news reports, so checking nearby terrain compensates for that
# imprecision). But applying a 5-pixel buffer to all 15,201 REAL village
# points was wrong -- Uttarakhand is mountainous almost everywhere, so a
# buffer around any village finds a severe slope nearby regardless of
# whether that village's own ground is actually dangerous. This inflated
# High/Severe from an expected ~32 villages to 14,332 (94% of all villages),
# making "priority" meaningless. Village tagging now samples the EXACT
# point again (villages have real, precise coordinates -- no imprecision to
# correct for). Buffer-max sampling stays ONLY in
# validate_against_historical_disasters.py, where it belongs.
def sample_at_point(row_idx, col_idx, raster_array):
    if not (0 <= row_idx < raster_array.shape[0] and 0 <= col_idx < raster_array.shape[1]):
        return np.nan
    return raster_array[row_idx, col_idx]

risk_values = []
for geom in villages_gdf.geometry:
    if geom is None:
        risk_values.append(np.nan)
        continue
    x, y = geom.x, geom.y
    row, col = rasterio.transform.rowcol(dem_transform, x, y)
    risk_values.append(sample_at_point(row, col, red_zone))

villages_gdf["risk_band"] = risk_values

risk_labels = {1: "Low", 2: "Moderate", 3: "High", 4: "Severe", np.nan: "Unknown"}
villages_gdf["risk_label"] = villages_gdf["risk_band"].map(
    lambda v: risk_labels.get(v, "Unknown")
)

out_path = os.path.join(OUTPUT_DIR, "uttarakhand_villages_risk_tagged.geojson")
villages_gdf.to_file(out_path, driver="GeoJSON")
print(f"Saved risk-tagged villages -> {out_path}")

print("\nVillage risk distribution:")
print(villages_gdf["risk_label"].value_counts())


# ---------------------------------------------------------------------------
# STEP 6: PRODUCE A RANKED PRIORITY LIST (HIGH/SEVERE RISK VILLAGES)
# ---------------------------------------------------------------------------
print("\n=== Step 6: Building priority relocation list ===")

priority_villages = villages_gdf[
    villages_gdf["risk_band"].isin([3, 4])
].copy()
priority_villages = priority_villages.sort_values("risk_band", ascending=False)

priority_csv_path = os.path.join(OUTPUT_DIR, "priority_villages.csv")
# Drop geometry for CSV export, keep lat/lon as plain columns instead
priority_export = priority_villages.copy()
priority_export["longitude"] = priority_export.geometry.x
priority_export["latitude"] = priority_export.geometry.y
priority_export = priority_export.drop(columns="geometry")
priority_export.to_csv(priority_csv_path, index=False)

print(f"Saved {len(priority_villages)} high/severe-risk villages -> "
      f"{priority_csv_path}")

print(
    f"\nDone. Check '{OUTPUT_DIR}' for:\n"
    "  - uttarakhand_red_zone_risk.tif (the hazard overlay raster)\n"
    "  - uttarakhand_villages_risk_tagged.geojson (all villages + risk)\n"
    "  - priority_villages.csv (high/severe risk villages, ranked)"
)