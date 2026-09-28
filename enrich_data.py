"""
enrich_data.py

Fills in the data gaps needed for a genuinely useful rescue/relocation
dashboard:
  1. Estimated population per at-risk village (sampled from population
     density raster - the raster's ~1km pixels approximate population
     count per cell, used here as a reasonable estimate)
  2. Capacity math: how many households/how much land a village's
     population would need at a relocation site
  3. Nearest road type + distance (not just distance) for each village -
     a simple, fast proxy for transport/accessibility
  4. Basic needs adequacy check at each candidate relocation site -
     counts nearby hospitals/schools within a service radius and flags
     whether it looks adequate for the incoming population, using
     rough per-capita service ratios as a starting benchmark

NOTE ON ASSUMPTIONS (clearly documented so you can explain/adjust these
in your presentation):
  - Average household size: 5 people (~India Census average)
  - Land per household: 100 sq. meters (small-plot resettlement norm)
  - Hospital service ratio: 1 hospital per 50,000 people (rough
    benchmark - adjust based on actual IPHS norms if you want precision)
  - School service ratio: 1 school per 5,000 people
  - Service search radius: 15 km (reasonable rural access distance)

Run this from VS Code with your venv activated:
    python enrich_data.py

Requires outputs from the previous scripts:
  - data/outputs/at_risk_villages_accessibility.csv
  - data/outputs/candidate_relocation_sites.csv
  - data/uttarakhand/uttarakhand_population_2020.tif
  - data/uttarakhand/uttarakhand_roads.geojson
  - data/uttarakhand/uttarakhand_hospitals.geojson
  - data/uttarakhand/uttarakhand_schools.geojson
"""

import os
import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from shapely.geometry import Point

DATA_DIR = os.path.join("data", "uttarakhand")
OUTPUT_DIR = os.path.join("data", "outputs")

AT_RISK_PATH = os.path.join(OUTPUT_DIR, "at_risk_villages_accessibility.csv")
CANDIDATES_PATH = os.path.join(OUTPUT_DIR, "candidate_relocation_sites.csv")
POPULATION_PATH = os.path.join(DATA_DIR, "uttarakhand_population_2020.tif")
ROADS_PATH = os.path.join(DATA_DIR, "uttarakhand_roads.geojson")
HOSPITALS_PATH = os.path.join(DATA_DIR, "uttarakhand_hospitals.geojson")
SCHOOLS_PATH = os.path.join(DATA_DIR, "uttarakhand_schools.geojson")

# ---------------------------------------------------------------------------
# ASSUMPTIONS - adjust these to refine the model
# ---------------------------------------------------------------------------
HOUSEHOLD_SIZE = 5
LAND_PER_HOUSEHOLD_SQM = 100
HOSPITAL_RATIO_PER_PERSON = 1 / 50000   # 1 hospital per 50,000 people
SCHOOL_RATIO_PER_PERSON = 1 / 5000      # 1 school per 5,000 people
SERVICE_RADIUS_KM = 15

METRIC_CRS = "EPSG:32644"  # UTM 44N, good for Uttarakhand distance math


def load_projected(path):
    gdf = gpd.read_file(path)
    return gdf, gdf.to_crs(METRIC_CRS)


# ---------------------------------------------------------------------------
# LOAD DATA
# ---------------------------------------------------------------------------
print("=== Loading data ===")
at_risk_df = pd.read_csv(AT_RISK_PATH)
candidates_df = pd.read_csv(CANDIDATES_PATH)
roads_gdf, roads_proj = load_projected(ROADS_PATH)
hospitals_gdf, hospitals_proj = load_projected(HOSPITALS_PATH)
schools_gdf, schools_proj = load_projected(SCHOOLS_PATH)

with rasterio.open(POPULATION_PATH) as pop_src:
    pop_array = pop_src.read(1)
    pop_transform = pop_src.transform
    pop_nodata = pop_src.nodata

print(f"At-risk villages: {len(at_risk_df)}, Candidate sites: {len(candidates_df)}")


def sample_population_at(lon, lat):
    row, col = rasterio.transform.rowcol(pop_transform, lon, lat)
    if 0 <= row < pop_array.shape[0] and 0 <= col < pop_array.shape[1]:
        val = pop_array[row, col]
        if pop_nodata is not None and val == pop_nodata:
            return 0
        return max(float(val), 0)
    return 0


def nearest_road_type_and_distance(lon, lat):
    """Returns (road_type, distance_km) for the nearest road segment."""
    pt = gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs(METRIC_CRS).iloc[0]
    distances = roads_proj.geometry.distance(pt)
    nearest_idx = distances.idxmin()
    nearest_dist_km = distances.min() / 1000
    road_type = roads_proj.loc[nearest_idx].get("highway", "unknown")
    return road_type, nearest_dist_km


def count_within_radius(lon, lat, target_proj, radius_km):
    pt = gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs(METRIC_CRS).iloc[0]
    distances = target_proj.geometry.distance(pt)
    return int((distances <= radius_km * 1000).sum())


# ---------------------------------------------------------------------------
# PART 1: ENRICH AT-RISK VILLAGES
# ---------------------------------------------------------------------------
print("\n=== Enriching at-risk villages ===")

estimated_populations = []
required_households = []
required_land_sqm = []
road_types = []

for _, row in at_risk_df.iterrows():
    lon, lat = row["longitude"], row["latitude"]

    pop_est = sample_population_at(lon, lat)
    estimated_populations.append(round(pop_est))

    households = max(1, round(pop_est / HOUSEHOLD_SIZE))
    required_households.append(households)
    required_land_sqm.append(households * LAND_PER_HOUSEHOLD_SQM)

    road_type, _ = nearest_road_type_and_distance(lon, lat)
    road_types.append(road_type)

at_risk_df["estimated_population"] = estimated_populations
at_risk_df["households_needing_relocation"] = required_households
at_risk_df["land_required_sqm"] = required_land_sqm
at_risk_df["nearest_road_type"] = road_types

print("Population/capacity estimates added.")
print(at_risk_df[["name", "estimated_population",
                   "households_needing_relocation",
                   "land_required_sqm"]].to_string(index=False))


# ---------------------------------------------------------------------------
# PART 2: ENRICH CANDIDATE RELOCATION SITES - BASIC NEEDS ADEQUACY
# ---------------------------------------------------------------------------
print("\n=== Assessing basic needs adequacy at top candidate sites ===")

# Only do this expensive nearby-count calculation for the top-scored
# sites, not all 2000 - keeps runtime reasonable
TOP_N_SITES = 100
top_candidates = candidates_df.nlargest(TOP_N_SITES, "suitability_score").copy()

# Total population that might need to be absorbed - sum of all at-risk
# villages' estimated population, as a rough "total incoming demand"
# figure to check candidate sites against.
total_at_risk_population = sum(estimated_populations)
print(f"Total estimated at-risk population (all high/severe villages): "
      f"{total_at_risk_population}")

hospitals_nearby = []
schools_nearby = []
hospital_adequacy = []
school_adequacy = []
nearest_road_type_list = []

# Adequacy check: does this site have enough nearby services to handle
# a representative share of the at-risk population? We assume each site
# might absorb population proportional to its suitability rank (simple
# even split across top sites, as a starting approximation).
assumed_population_per_site = total_at_risk_population / max(TOP_N_SITES, 1)
hospitals_needed = max(1, round(assumed_population_per_site * HOSPITAL_RATIO_PER_PERSON))
schools_needed = max(1, round(assumed_population_per_site * SCHOOL_RATIO_PER_PERSON))

for _, row in top_candidates.iterrows():
    lon, lat = row["longitude"], row["latitude"]

    h_count = count_within_radius(lon, lat, hospitals_proj, SERVICE_RADIUS_KM)
    s_count = count_within_radius(lon, lat, schools_proj, SERVICE_RADIUS_KM)
    hospitals_nearby.append(h_count)
    schools_nearby.append(s_count)

    hospital_adequacy.append("Adequate" if h_count >= hospitals_needed else "Insufficient")
    school_adequacy.append("Adequate" if s_count >= schools_needed else "Insufficient")

    road_type, _ = nearest_road_type_and_distance(lon, lat)
    nearest_road_type_list.append(road_type)

top_candidates["hospitals_within_15km"] = hospitals_nearby
top_candidates["schools_within_15km"] = schools_nearby
top_candidates["hospital_adequacy"] = hospital_adequacy
top_candidates["school_adequacy"] = school_adequacy
top_candidates["nearest_road_type"] = nearest_road_type_list
top_candidates["assumed_population_share"] = round(assumed_population_per_site)

print(f"\nAssuming ~{round(assumed_population_per_site)} people per site "
      f"(even split of {total_at_risk_population} across top {TOP_N_SITES} sites):")
print(f"  Benchmark: needs >= {hospitals_needed} hospital(s), "
      f">= {schools_needed} school(s) within {SERVICE_RADIUS_KM}km")

print("\nAdequacy summary across top sites:")
print(top_candidates["hospital_adequacy"].value_counts())
print(top_candidates["school_adequacy"].value_counts())


# ---------------------------------------------------------------------------
# SAVE ENRICHED OUTPUTS
# ---------------------------------------------------------------------------
at_risk_out = os.path.join(OUTPUT_DIR, "at_risk_villages_enriched.csv")
at_risk_df.to_csv(at_risk_out, index=False)
print(f"\nSaved -> {at_risk_out}")

candidates_out = os.path.join(OUTPUT_DIR, "candidate_sites_enriched.csv")
top_candidates.to_csv(candidates_out, index=False)
print(f"Saved -> {candidates_out}")

print(
    "\nDone. These enriched files now include population estimates, "
    "capacity math, road type, and basic-needs adequacy - ready for "
    "the frontend."
)