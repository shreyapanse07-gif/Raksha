"""
download_uttarakhand_data.py

Downloads geospatial data for UTTARAKHAND directly from original source
portals - NO Google Earth Engine, NO Google account/project registration
needed at all.

Data pulled:
  1. Uttarakhand state boundary (for clipping everything else)
  2. DEM (elevation) -> OpenTopography (SRTM 30m)
  3. Rainfall -> CHIRPS (direct HTTP download)
  4. Population -> WorldPop (direct HTTP download)
  5. Land cover -> ESA WorldCover (direct HTTP download)

Everything gets clipped to Uttarakhand locally using rasterio + geopandas
(both already installed in your venv).

Run this from VS Code with your venv activated:
    python download_uttarakhand_data.py
"""

import os
import requests
import geopandas as gpd
import rasterio
from rasterio.mask import mask

# ---------------------------------------------------------------------------
# SETUP
# ---------------------------------------------------------------------------
DATA_DIR = "data"
RAW_DIR = os.path.join(DATA_DIR, "raw")
CLIPPED_DIR = os.path.join(DATA_DIR, "uttarakhand")
os.makedirs(RAW_DIR, exist_ok=True)
os.makedirs(CLIPPED_DIR, exist_ok=True)


def download_file(url: str, out_path: str, max_retries: int = 4):
    """Streaming download with progress and automatic retry on connection
    errors (common with large files on flaky connections)."""
    if os.path.exists(out_path):
        print(f"Already downloaded, skipping: {out_path}")
        return

    for attempt in range(1, max_retries + 1):
        try:
            print(f"Downloading (attempt {attempt}/{max_retries}): {url}")
            response = requests.get(url, stream=True, timeout=60)
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0))
            downloaded = 0
            tmp_path = out_path + ".part"
            with open(tmp_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded / total * 100
                        print(f"\r  {pct:.1f}% ({downloaded/1e6:.1f} MB)", end="")
            os.replace(tmp_path, out_path)  # only rename on full success
            print(f"\nSaved: {out_path}")
            return
        except Exception as e:
            print(f"\n  Attempt {attempt} failed: {e}")
            tmp_path = out_path + ".part"
            if os.path.exists(tmp_path):
                os.remove(tmp_path)  # discard partial file, don't keep corrupt data
            if attempt < max_retries:
                import time
                wait = 5 * attempt
                print(f"  Retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise


def clip_raster_to_boundary(raster_path: str, boundary_gdf, out_path: str):
    """Clip a raster to the Uttarakhand boundary polygon, preserving nodata."""
    with rasterio.open(raster_path) as src:
        print(f"  Source raster info: CRS={src.crs}, size={src.width}x{src.height}, "
              f"dtype={src.dtypes[0]}, bounds={src.bounds}, nodata={src.nodata}")
        # Reproject boundary to match raster CRS if needed
        boundary = boundary_gdf.to_crs(src.crs)
        print(f"  Boundary bounds (in raster CRS): {boundary.total_bounds}")
        geoms = [geom.__geo_interface__ for geom in boundary.geometry]
        out_image, out_transform = mask(
            src, geoms, crop=True, all_touched=True, nodata=src.nodata
        )
        out_meta = src.meta.copy()
        out_meta.update(
            {
                "height": out_image.shape[1],
                "width": out_image.shape[2],
                "transform": out_transform,
                "nodata": src.nodata,  # preserve nodata marker in output
            }
        )
    with rasterio.open(out_path, "w", **out_meta) as dest:
        dest.write(out_image)
    print(f"Clipped -> {out_path}")


# ---------------------------------------------------------------------------
# STEP 1: UTTARAKHAND BOUNDARY (needed to clip everything else)
# ---------------------------------------------------------------------------
# Using GADM (Database of Global Administrative Areas) - free, no login,
# reliable state-level boundaries for India.
print("\n=== Step 1: Uttarakhand boundary ===")

gadm_url = "https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_IND_1.json"
boundary_raw_path = os.path.join(RAW_DIR, "india_states.geojson")
download_file(gadm_url, boundary_raw_path)

india_states_gdf = gpd.read_file(boundary_raw_path)
# Filter to just Uttarakhand
uttarakhand_gdf = india_states_gdf[india_states_gdf["NAME_1"] == "Uttarakhand"]

if uttarakhand_gdf.empty:
    print("WARNING: 'Uttarakhand' not found by that exact name. "
          "Available state names:")
    print(india_states_gdf["NAME_1"].unique())
else:
    uk_boundary_path = os.path.join(CLIPPED_DIR, "uttarakhand_boundary.geojson")
    uttarakhand_gdf.to_file(uk_boundary_path, driver="GeoJSON")
    print(f"Uttarakhand boundary saved -> {uk_boundary_path}")


# ---------------------------------------------------------------------------
# STEP 2: DEM (Elevation) - via OpenTopography's public SRTM API
# ---------------------------------------------------------------------------
# OpenTopography's Global DEM API lets you request a bounding-box DEM
# directly, no login needed for SRTM GL1 (30m) at reasonable sizes.
print("\n=== Step 2: DEM (elevation) ===")

# Uttarakhand's approximate bounding box (south, north, west, east)
UK_BBOX = {
    "south": 28.7,
    "north": 31.5,
    "west": 77.5,
    "east": 81.1,
}

# OpenTopography now requires a free API key (as of their recent policy
# change). Get yours at https://opentopography.org/ -> My Account ->
# Request an API Key, then paste it below.
OPENTOPOGRAPHY_API_KEY = "PASTE_YOUR_API_KEY_HERE"

dem_url = (
    "https://portal.opentopography.org/API/globaldem"
    f"?demtype=SRTMGL1"
    f"&south={UK_BBOX['south']}&north={UK_BBOX['north']}"
    f"&west={UK_BBOX['west']}&east={UK_BBOX['east']}"
    f"&outputFormat=GTiff"
    f"&API_Key={OPENTOPOGRAPHY_API_KEY}"
)
dem_raw_path = os.path.join(RAW_DIR, "uttarakhand_dem_raw.tif")
try:
    download_file(dem_url, dem_raw_path)
    # OpenTopography sometimes returns a small HTML/JSON error page with a
    # 200 status instead of a real GeoTIFF (e.g. rate limit or bad params).
    # Verify it's actually a readable raster before trusting it.
    try:
        with rasterio.open(dem_raw_path) as test_src:
            pass  # opened fine -> it's a real GeoTIFF
    except Exception as verify_err:
        print(
            f"WARNING: downloaded DEM file is not a valid GeoTIFF "
            f"({verify_err}). Deleting it so it can be retried.\n"
            "This usually means OpenTopography returned an error page - "
            "check your API key is correct, or try again in a few minutes."
        )
        os.remove(dem_raw_path)
except Exception as e:
    print(f"OpenTopography download failed: {e}")
    print("NOTE: OpenTopography's public API has rate limits and may need "
          "a free API key for reliable access. Sign up at "
          "https://opentopography.org/ if this fails - it's free and instant.")


# ---------------------------------------------------------------------------
# STEP 3: RAINFALL - CHIRPS (direct download, no login)
# ---------------------------------------------------------------------------
print("\n=== Step 3: Rainfall (CHIRPS) ===")
# CHIRPS provides monthly/annual global rainfall rasters as direct downloads.
# The exact filename/extension on their server changes over time (they've
# moved between .tif and .tif.gz, and v2.0 -> v3.0), so we try a few
# candidate URLs in order until one works.
chirps_candidates = [
    "https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_annual/tifs/chirps-v2.0.2023.tif.gz",
    "https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_annual/tifs/chirps-v2.0.2023.tif",
    "https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_annual/tifs/chirps-v2.0.2022.tif.gz",
]
chirps_tif_path = os.path.join(RAW_DIR, "chirps_annual.tif")
chirps_downloaded = False

for url in chirps_candidates:
    try:
        raw_out = os.path.join(RAW_DIR, os.path.basename(url))
        download_file(url, raw_out)
        if raw_out.endswith(".gz"):
            import gzip
            import shutil
            with gzip.open(raw_out, "rb") as f_in:
                with open(chirps_tif_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            print(f"Unzipped -> {chirps_tif_path}")
        else:
            os.replace(raw_out, chirps_tif_path)
        chirps_downloaded = True
        break
    except Exception as e:
        print(f"  Failed: {e}")
        continue

if not chirps_downloaded:
    print(
        "CHIRPS auto-download failed on all candidate URLs.\n"
        "Manual fallback: browse "
        "https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_annual/tifs/ "
        "in your browser, find the current filename for the year you want, "
        "download it manually, and place it at:\n"
        f"  {chirps_tif_path}\n"
        "(rename it to exactly that if needed) - the rest of the script "
        "will pick it up automatically on re-run."
    )


# ---------------------------------------------------------------------------
# STEP 4: POPULATION - WorldPop (direct download, no login)
# ---------------------------------------------------------------------------
print("\n=== Step 4: Population (WorldPop via HDX) ===")
# The direct WorldPop 100m file (~1.7GB) has proven unreliable - the server
# keeps resetting the connection partway through. Switching to HDX
# (Humanitarian Data Exchange), which hosts the same WorldPop-derived
# population density data via a more reliable CDN, at 1km resolution
# (much smaller file, ~20-40MB - still plenty precise for state-level
# hazard/relocation analysis).
worldpop_url = (
    "https://data.humdata.org/dataset/4e74db9e-8ee2-4051-bc9f-002d818ebbf7/"
    "resource/c91c2a60-9e08-4949-9c5f-4f9de480ec83/download/"
    "ind_pd_2020_1km_unadj.tif"
)
worldpop_path = os.path.join(RAW_DIR, "india_population_density_2020.tif")
try:
    download_file(worldpop_url, worldpop_path)
except Exception as e:
    print(f"HDX population download failed: {e}")
    print(
        "NOTE: HDX resource URLs can change. If this fails, manually visit:\n"
        "  https://data.humdata.org/dataset/worldpop-population-density-for-india\n"
        "download 'ind_pd_2020_1km_UNadj.tif', and place it at:\n"
        f"  {worldpop_path}"
    )


# ---------------------------------------------------------------------------
# STEP 5: CLIP EVERYTHING TO UTTARAKHAND
# ---------------------------------------------------------------------------
print("\n=== Step 5: Clipping all rasters to Uttarakhand ===")

if not uttarakhand_gdf.empty:
    rasters_to_clip = {
        "uttarakhand_dem.tif": dem_raw_path,
        "uttarakhand_rainfall_2023.tif": chirps_tif_path,
        "uttarakhand_population_2020.tif": worldpop_path,
    }

    for out_name, raw_path in rasters_to_clip.items():
        if os.path.exists(raw_path):
            out_path = os.path.join(CLIPPED_DIR, out_name)
            try:
                clip_raster_to_boundary(raw_path, uttarakhand_gdf, out_path)
            except Exception as e:
                import traceback
                print(f"Failed to clip {raw_path}:")
                traceback.print_exc()
        else:
            print(f"Skipping clip for {out_name} - raw file not found "
                  f"(download may have failed above).")

print(
    f"\nDone. Check the '{CLIPPED_DIR}' folder for your Uttarakhand-clipped "
    "datasets, ready to use in your hazard overlay analysis."
)