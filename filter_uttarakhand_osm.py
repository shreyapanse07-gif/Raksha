"""
filter_uttarakhand_osm.py

Filters the downloaded BBBike GeoPackage (uttarakhand_osm.gpkg) into the
specific layers we need for the Red Zone project:
  1. Villages / hamlets / towns (habitation points)
  2. Hospitals
  3. Schools
  4. Major roads (primary/secondary/tertiary/trunk)

Run this from VS Code with your venv activated:
    python filter_uttarakhand_osm.py

Make sure uttarakhand_osm.gpkg is in data/raw/ before running.
"""

import os
import geopandas as gpd
import pyogrio

RAW_PATH = os.path.join("data", "raw", "uttarakhand_osm.gpkg")
OUTPUT_DIR = os.path.join("data", "uttarakhand")
os.makedirs(OUTPUT_DIR, exist_ok=True)

if not os.path.exists(RAW_PATH):
    raise FileNotFoundError(
        f"Could not find {RAW_PATH}. Make sure you've downloaded, "
        "extracted, and renamed the BBBike file to this exact path."
    )

# ---------------------------------------------------------------------------
# STEP 1: SEE WHAT LAYERS ARE INSIDE THIS GEOPACKAGE
# ---------------------------------------------------------------------------
# GeoPackages can contain multiple layers (points, lines, polygons all
# stored separately, sometimes multiple layers per geometry type).
# Let's list them first so we know what we're working with.
print("=== Layers found in this GeoPackage ===")
layers_info = pyogrio.list_layers(RAW_PATH)
# pyogrio returns a numpy array of [name, geometry_type] pairs
for layer_name, geom_type in layers_info:
    print(f"  - {layer_name} ({geom_type})")
print()


# ---------------------------------------------------------------------------
# STEP 2: LOAD RELEVANT LAYERS
# ---------------------------------------------------------------------------
# BBBike GeoPackages typically split data into layers like:
#   points, lines, multipolygons, multilinestrings, etc.
# We'll load 'points' (for villages/hospitals/schools - all point features)
# and 'lines' (for roads).
# If the actual layer names differ from what's printed above, update the
# names below to match.

print("=== Loading points layer ===")
try:
    points_gdf = gpd.read_file(RAW_PATH, layer="points")
    print(f"Loaded {len(points_gdf)} point features.")
    print(f"Available columns: {list(points_gdf.columns)}")
except Exception as e:
    print(f"Could not load 'points' layer: {e}")
    print("Check the layer names printed above and adjust the script.")
    points_gdf = gpd.GeoDataFrame()

print("\n=== Loading lines layer ===")
try:
    lines_gdf = gpd.read_file(RAW_PATH, layer="lines")
    print(f"Loaded {len(lines_gdf)} line features.")
    print(f"Available columns: {list(lines_gdf.columns)}")
except Exception as e:
    print(f"Could not load 'lines' layer: {e}")
    print("Check the layer names printed above and adjust the script.")
    lines_gdf = gpd.GeoDataFrame()


# ---------------------------------------------------------------------------
# STEP 3: FILTER VILLAGES / HABITATIONS
# ---------------------------------------------------------------------------
print("\n=== Filtering villages/habitations ===")
if not points_gdf.empty and "place" in points_gdf.columns:
    villages = points_gdf[
        points_gdf["place"].isin(["village", "hamlet", "town"])
    ]
    if not villages.empty:
        out_path = os.path.join(OUTPUT_DIR, "uttarakhand_villages.geojson")
        villages.to_file(out_path, driver="GeoJSON")
        print(f"Saved {len(villages)} villages/habitations -> {out_path}")
    else:
        print("No villages found with place=village/hamlet/town.")
else:
    print("'place' column not found in points layer - check available "
          "columns printed above and adjust the filter condition.")


# ---------------------------------------------------------------------------
# STEP 4: FILTER HOSPITALS
# ---------------------------------------------------------------------------
print("\n=== Filtering hospitals ===")
if not points_gdf.empty and "other_tags" in points_gdf.columns:
    # BBBike bundles less-common tags into 'other_tags' as a string like:
    # "amenity"=>"hospital","name"=>"City Hospital" - search within it.
    hospitals = points_gdf[
        points_gdf["other_tags"].str.contains(
            '"amenity"=>"hospital"', na=False
        )
    ]
    if not hospitals.empty:
        out_path = os.path.join(OUTPUT_DIR, "uttarakhand_hospitals.geojson")
        hospitals.to_file(out_path, driver="GeoJSON")
        print(f"Saved {len(hospitals)} hospitals -> {out_path}")
    else:
        print("No hospitals found via other_tags search.")
else:
    print("'other_tags' column not found in points layer.")


# ---------------------------------------------------------------------------
# STEP 5: FILTER SCHOOLS
# ---------------------------------------------------------------------------
print("\n=== Filtering schools ===")
if not points_gdf.empty and "other_tags" in points_gdf.columns:
    schools = points_gdf[
        points_gdf["other_tags"].str.contains(
            '"amenity"=>"school"', na=False
        )
    ]
    if not schools.empty:
        out_path = os.path.join(OUTPUT_DIR, "uttarakhand_schools.geojson")
        schools.to_file(out_path, driver="GeoJSON")
        print(f"Saved {len(schools)} schools -> {out_path}")
    else:
        print("No schools found via other_tags search.")
else:
    print("'other_tags' column not found in points layer.")


# ---------------------------------------------------------------------------
# STEP 6: FILTER MAJOR ROADS
# ---------------------------------------------------------------------------
print("\n=== Filtering major roads ===")
if not lines_gdf.empty and "highway" in lines_gdf.columns:
    major_types = ["primary", "secondary", "tertiary", "trunk"]
    roads = lines_gdf[lines_gdf["highway"].isin(major_types)]
    if not roads.empty:
        out_path = os.path.join(OUTPUT_DIR, "uttarakhand_roads.geojson")
        roads.to_file(out_path, driver="GeoJSON")
        print(f"Saved {len(roads)} road segments -> {out_path}")
    else:
        print("No major roads found - check 'highway' column values.")
else:
    print("'highway' column not found in lines layer.")


print(
    f"\nDone. Check the '{OUTPUT_DIR}' folder for the filtered "
    "villages, hospitals, schools, and roads datasets."
)