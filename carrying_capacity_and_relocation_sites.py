"""
carrying_capacity_and_relocation_sites.py

Two things happen in this script:

PART A - ACCESSIBILITY FOR AT-RISK VILLAGES
  Adds distance-to-nearest-major-road to every high/severe-risk village,
  since a hard-to-reach at-risk village is a bigger evacuation/emergency-
  response concern than one right next to a highway.

PART B - CANDIDATE RELOCATION SITE SCORING
  Generates candidate relocation site points across Uttarakhand's LOW-risk
  zones, then scores each by:
    - Distance to nearest major road      (accessibility / logistics)
    - Distance to nearest hospital        (essential services)
    - Distance to nearest school          (essential services)
    - Local population density            (prefer emptier land - safer
                                             to build on, less displacement
                                             of existing residents)
    - Hazard risk band                    (must be Low, already filtered)
  Combined into one 0-100 "Site Suitability Score" per candidate site.

Run this from VS Code with your venv activated:
    python carrying_capacity_and_relocation_sites.py

Requires outputs from the previous two scripts:
  - data/outputs/uttarakhand_villages_risk_tagged.geojson
  - data/outputs/uttarakhand_red_zone_risk.tif
  - data/uttarakhand/uttarakhand_roads.geojson
  - data/uttarakhand/uttarakhand_hospitals.geojson
  - data/uttarakhand/uttarakhand_schools.geojson
  - data/uttarakhand/uttarakhand_population_2020.tif
  - data/uttarakhand/uttarakhand_boundary.geojson
"""

import os
import numpy as np
import rasterio
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

DATA_DIR = os.path.join("data", "uttarakhand")
OUTPUT_DIR = os.path.join("data", "outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

VILLAGES_RISK_PATH = os.path.join(OUTPUT_DIR, "uttarakhand_villages_risk_tagged.geojson")
RED_ZONE_RASTER_PATH = os.path.join(OUTPUT_DIR, "uttarakhand_red_zone_risk.tif")
ROADS_PATH = os.path.join(DATA_DIR, "uttarakhand_roads.geojson")
HOSPITALS_PATH = os.path.join(DATA_DIR, "uttarakhand_hospitals.geojson")
SCHOOLS_PATH = os.path.join(DATA_DIR, "uttarakhand_schools.geojson")
POPULATION_PATH = os.path.join(DATA_DIR, "uttarakhand_population_2020.tif")
BOUNDARY_PATH = os.path.join(DATA_DIR, "uttarakhand_boundary.geojson")


def nearest_distance_km(point, target_gdf_projected, point_crs_projected):
    """Distance in km from a point to the nearest feature in target_gdf."""
    if target_gdf_projected.empty:
        return np.nan
    distances = target_gdf_projected.geometry.distance(point)
    return distances.min() / 1000  # meters -> km


# ---------------------------------------------------------------------------
# LOAD EVERYTHING
# ---------------------------------------------------------------------------
print("=== Loading datasets ===")
villages_gdf = gpd.read_file(VILLAGES_RISK_PATH)
roads_gdf = gpd.read_file(ROADS_PATH)
hospitals_gdf = gpd.read_file(HOSPITALS_PATH)
schools_gdf = gpd.read_file(SCHOOLS_PATH)
boundary_gdf = gpd.read_file(BOUNDARY_PATH)
print(f"Villages: {len(villages_gdf)}, Roads: {len(roads_gdf)}, "
      f"Hospitals: {len(hospitals_gdf)}, Schools: {len(schools_gdf)}")

# Project everything to a metric CRS for accurate distance calculations.
# UTM zone 44N (EPSG:32644) covers Uttarakhand well.
METRIC_CRS = "EPSG:32644"
villages_proj = villages_gdf.to_crs(METRIC_CRS)
roads_proj = roads_gdf.to_crs(METRIC_CRS)
hospitals_proj = hospitals_gdf.to_crs(METRIC_CRS)
schools_proj = schools_gdf.to_crs(METRIC_CRS)
boundary_proj = boundary_gdf.to_crs(METRIC_CRS)


# ---------------------------------------------------------------------------
# PART A: DISTANCE-TO-ROAD FOR HIGH/SEVERE RISK VILLAGES
# ---------------------------------------------------------------------------
print("\n=== Part A: Accessibility for at-risk villages ===")
at_risk = villages_proj[villages_proj["risk_band"].isin([3, 4])].copy()
print(f"Computing road distance for {len(at_risk)} high/severe-risk villages...")

at_risk["distance_to_road_km"] = at_risk.geometry.apply(
    lambda pt: nearest_distance_km(pt, roads_proj, METRIC_CRS)
)

# Flag villages that are BOTH high-risk AND hard to reach - these are the
# most urgent evacuation-planning priority
at_risk["evacuation_urgency"] = np.where(
    at_risk["distance_to_road_km"] > 5, "Critical - remote + high risk",
    np.where(at_risk["distance_to_road_km"] > 2, "High - moderate access",
             "Moderate - good access")
)

at_risk_export = at_risk.to_crs("EPSG:4326").copy()
at_risk_export["longitude"] = at_risk_export.geometry.x
at_risk_export["latitude"] = at_risk_export.geometry.y
at_risk_export_df = at_risk_export.drop(columns="geometry")
at_risk_csv = os.path.join(OUTPUT_DIR, "at_risk_villages_accessibility.csv")
at_risk_export_df.to_csv(at_risk_csv, index=False)
print(f"Saved -> {at_risk_csv}")
print("\nEvacuation urgency breakdown:")
print(at_risk["evacuation_urgency"].value_counts())


# ---------------------------------------------------------------------------
# PART B: GENERATE CANDIDATE RELOCATION SITES
# ---------------------------------------------------------------------------
print("\n=== Part B: Generating candidate relocation sites ===")

# Generate a regular grid of candidate points across Uttarakhand, then
# keep only those that fall within LOW-risk zones (band 1) - these are
# areas safe enough to be considered for new/expanded settlement.
with rasterio.open(RED_ZONE_RASTER_PATH) as risk_src:
    risk_array = risk_src.read(1)
    risk_transform = risk_src.transform
    risk_crs = risk_src.crs
    bounds = risk_src.bounds

# Grid spacing: ~2km between candidate points - dense enough for useful
# coverage without generating an unmanageable number of points.
GRID_SPACING_DEG = 0.02  # roughly ~2km at this latitude

lons = np.arange(bounds.left, bounds.right, GRID_SPACING_DEG)
lats = np.arange(bounds.bottom, bounds.top, GRID_SPACING_DEG)

candidate_points = []
for lon in lons:
    for lat in lats:
        row, col = rasterio.transform.rowcol(risk_transform, lon, lat)
        if 0 <= row < risk_array.shape[0] and 0 <= col < risk_array.shape[1]:
            risk_val = risk_array[row, col]
            if risk_val == 1:  # Low risk only
                candidate_points.append(Point(lon, lat))

print(f"Generated {len(candidate_points)} candidate points in low-risk zones.")

candidates_gdf = gpd.GeoDataFrame(geometry=candidate_points, crs="EPSG:4326")

# Filter to only points actually within Uttarakhand's boundary (grid can
# extend slightly beyond the raster's bounding box otherwise)
boundary_union_4326 = boundary_gdf.geometry.union_all()
candidates_gdf = candidates_gdf[candidates_gdf.geometry.within(boundary_union_4326)].copy()
print(f"After boundary filtering: {len(candidates_gdf)} candidates remain.")

# Cap the number of candidates for performance if there are a lot -
# random sample down to a manageable number for distance calculations
MAX_CANDIDATES = 2000
if len(candidates_gdf) > MAX_CANDIDATES:
    candidates_gdf = candidates_gdf.sample(MAX_CANDIDATES, random_state=42)
    print(f"Sampled down to {MAX_CANDIDATES} candidates for scoring performance.")

candidates_proj = candidates_gdf.to_crs(METRIC_CRS)


# ---------------------------------------------------------------------------
# SCORE EACH CANDIDATE SITE
# ---------------------------------------------------------------------------
print("\n=== Scoring candidate sites ===")
print("(This may take a minute for road/hospital/school distance calcs...)")

candidates_proj["dist_to_road_km"] = candidates_proj.geometry.apply(
    lambda pt: nearest_distance_km(pt, roads_proj, METRIC_CRS)
)
candidates_proj["dist_to_hospital_km"] = candidates_proj.geometry.apply(
    lambda pt: nearest_distance_km(pt, hospitals_proj, METRIC_CRS)
)
candidates_proj["dist_to_school_km"] = candidates_proj.geometry.apply(
    lambda pt: nearest_distance_km(pt, schools_proj, METRIC_CRS)
)

# Sample population density at each candidate location
with rasterio.open(POPULATION_PATH) as pop_src:
    pop_array = pop_src.read(1)
    pop_transform = pop_src.transform
    pop_nodata = pop_src.nodata

def sample_population(point_4326):
    x, y = point_4326.x, point_4326.y
    row, col = rasterio.transform.rowcol(pop_transform, x, y)
    if 0 <= row < pop_array.shape[0] and 0 <= col < pop_array.shape[1]:
        val = pop_array[row, col]
        if pop_nodata is not None and val == pop_nodata:
            return 0
        return max(val, 0)  # clamp negative nodata artifacts to 0
    return 0

candidates_gdf["population_density"] = candidates_gdf.geometry.apply(sample_population)
candidates_proj["population_density"] = candidates_gdf["population_density"].values


# ---------------------------------------------------------------------------
# COMBINE INTO A 0-100 SUITABILITY SCORE
# ---------------------------------------------------------------------------
# Scoring logic (each component normalized 0-100, then weighted):
#   - Closer to roads   -> higher score (accessibility)
#   - Closer to hospital -> higher score (essential services)
#   - Closer to school   -> higher score (essential services)
#   - LOWER population density -> higher score (less displacement,
#     more available land to actually build on)
def distance_to_score(distances, max_reasonable_km=20):
    """Closer = higher score. Caps at max_reasonable_km for normalization."""
    clipped = distances.clip(upper=max_reasonable_km)
    return 100 * (1 - clipped / max_reasonable_km)

candidates_proj["road_score"] = distance_to_score(candidates_proj["dist_to_road_km"])
candidates_proj["hospital_score"] = distance_to_score(candidates_proj["dist_to_hospital_km"], max_reasonable_km=30)
candidates_proj["school_score"] = distance_to_score(candidates_proj["dist_to_school_km"], max_reasonable_km=15)

# Population score: lower density = higher score (inverse, normalized
# against the max density seen among candidates)
max_pop = candidates_proj["population_density"].max()
if max_pop > 0:
    candidates_proj["population_score"] = 100 * (
        1 - (candidates_proj["population_density"] / max_pop)
    )
else:
    candidates_proj["population_score"] = 100

# Weighted combination - adjust these weights as you refine priorities
ROAD_WEIGHT = 0.35
HOSPITAL_WEIGHT = 0.25
SCHOOL_WEIGHT = 0.15
POPULATION_WEIGHT = 0.25

candidates_proj["suitability_score"] = (
    candidates_proj["road_score"] * ROAD_WEIGHT
    + candidates_proj["hospital_score"] * HOSPITAL_WEIGHT
    + candidates_proj["school_score"] * SCHOOL_WEIGHT
    + candidates_proj["population_score"] * POPULATION_WEIGHT
).round(1)

# Export final ranked candidate sites
final = candidates_proj.to_crs("EPSG:4326").copy()
final["longitude"] = final.geometry.x
final["latitude"] = final.geometry.y
final = final.sort_values("suitability_score", ascending=False)
final_df = final.drop(columns="geometry")

candidates_csv = os.path.join(OUTPUT_DIR, "candidate_relocation_sites.csv")
final_df.to_csv(candidates_csv, index=False)
print(f"\nSaved {len(final_df)} scored candidate sites -> {candidates_csv}")

print("\nTop 10 candidate relocation sites:")
print(final_df[["latitude", "longitude", "suitability_score",
                 "dist_to_road_km", "dist_to_hospital_km",
                 "dist_to_school_km", "population_density"]].head(10).to_string(index=False))

print(
    f"\nDone. Check '{OUTPUT_DIR}' for:\n"
    "  - at_risk_villages_accessibility.csv (evacuation urgency ranking)\n"
    "  - candidate_relocation_sites.csv (ranked relocation site suitability)"
)