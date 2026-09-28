"""
diagnose_historical_disasters.py

Diagnostic step BEFORE recalibrating thresholds. Instead of just checking
the final Low/Moderate/High/Severe classification, this prints the RAW
slope (degrees) and RAW rainfall (mm/year) values at each historical
disaster site -- both at the exact point, and as a MAX within a small
buffer radius (to account for settlements sitting on flatter ground next
to the steep slopes that actually caused the disaster).

This tells us whether the miss is a sampling problem (steep slope nearby,
but not at the exact point), a formula problem (both layers need to be
high simultaneously under the current weighted-average), or a genuine
threshold problem (raw values really are unremarkable even nearby).

Run from VS Code with your venv activated:
    python diagnose_historical_disasters.py

Requires the intermediate slope_deg and rainfall_resampled arrays -- since
build_hazard_overlay.py doesn't currently save these as standalone files,
this script recomputes them the same way (slope from DEM, rainfall
resampled to match), so it must be run from the same project folder with
the same data/ layout.
"""

import os
import numpy as np
import rasterio
from scipy.ndimage import distance_transform_edt
from rasterio.warp import reproject, Resampling as WarpResampling

DATA_DIR = os.path.join("data", "uttarakhand")
OUTPUT_DIR = os.path.join("data", "outputs")
DEM_PATH = os.path.join(DATA_DIR, "uttarakhand_dem.tif")
RAINFALL_PATH = os.path.join(DATA_DIR, "uttarakhand_rainfall_2023.tif")

HISTORICAL_DISASTERS = [
    {"name": "Kedarnath (2013 flash flood & debris flow)", "lat": 30.7346, "lon": 79.0669},
    {"name": "Joshimath (land subsidence)", "lat": 30.5610, "lon": 79.5642},
    {"name": "Chamoli / Raini village (2021 flash flood)", "lat": 30.5667, "lon": 79.7333},
    {"name": "Chamoli / Tapovan (2021 flash flood)", "lat": 30.5333, "lon": 79.6667},
]

BUFFER_PIXELS = 5  # ~ a small neighborhood around each point; adjust based on your DEM resolution


def recompute_slope():
    with rasterio.open(DEM_PATH) as dem_src:
        dem = dem_src.read(1).astype(np.float32)
        dem_transform = dem_src.transform
        dem_nodata = dem_src.nodata

    pixel_size_m = dem_transform[0] * 111320
    dem_masked = np.where(dem == dem_nodata, np.nan, dem) if dem_nodata is not None else dem

    if np.isnan(dem_masked).any():
        nan_mask = np.isnan(dem_masked)
        indices = distance_transform_edt(nan_mask, return_distances=False, return_indices=True)
        dem_filled = dem_masked[tuple(indices)]
    else:
        dem_filled = dem_masked

    dzdx, dzdy = np.gradient(dem_filled, pixel_size_m, pixel_size_m)
    slope_deg = np.degrees(np.arctan(np.sqrt(dzdx**2 + dzdy**2)))
    slope_deg = np.where(np.isnan(dem_masked), np.nan, slope_deg)
    return slope_deg, dem_transform


def recompute_rainfall(dem_transform, dem_shape):
    with rasterio.open(RAINFALL_PATH) as rain_src:
        rainfall_resampled = np.empty(dem_shape, dtype=np.float32)
        reproject(
            source=rasterio.band(rain_src, 1),
            destination=rainfall_resampled,
            src_transform=rain_src.transform,
            src_crs=rain_src.crs,
            dst_transform=dem_transform,
            dst_crs=rain_src.crs,
            resampling=WarpResampling.bilinear,
        )
    return rainfall_resampled


def sample_point_and_buffer(array, transform, lat, lon, buffer_px):
    row, col = rasterio.transform.rowcol(transform, lon, lat)
    if not (0 <= row < array.shape[0] and 0 <= col < array.shape[1]):
        return np.nan, np.nan
    point_val = array[row, col]

    r0, r1 = max(0, row - buffer_px), min(array.shape[0], row + buffer_px + 1)
    c0, c1 = max(0, col - buffer_px), min(array.shape[1], col + buffer_px + 1)
    window = array[r0:r1, c0:c1]
    buffer_max = np.nanmax(window) if window.size else np.nan
    return point_val, buffer_max


def main():
    print("=== Diagnostic: raw slope & rainfall values at historical disaster sites ===\n")

    slope_deg, dem_transform = recompute_slope()
    rainfall_resampled = recompute_rainfall(dem_transform, slope_deg.shape)

    for event in HISTORICAL_DISASTERS:
        slope_pt, slope_buf = sample_point_and_buffer(
            slope_deg, dem_transform, event["lat"], event["lon"], BUFFER_PIXELS
        )
        rain_pt, rain_buf = sample_point_and_buffer(
            rainfall_resampled, dem_transform, event["lat"], event["lon"], BUFFER_PIXELS
        )

        print(f"{event['name']}")
        print(f"  Slope at exact point:      {slope_pt:.1f} deg")
        print(f"  Slope max in nearby buffer: {slope_buf:.1f} deg")
        print(f"  Rainfall at exact point:      {rain_pt:.0f} mm/yr")
        print(f"  Rainfall max in nearby buffer: {rain_buf:.0f} mm/yr")
        print()

    print(
        "Look at 'max in nearby buffer' vs 'at exact point'. A big jump means "
        "this is a point-sampling issue (steep terrain exists right next to the "
        "site, just not at the exact coordinate). If even the buffer max is "
        "unremarkable, the raw data/thresholds themselves need work."
    )


if __name__ == "__main__":
    main()