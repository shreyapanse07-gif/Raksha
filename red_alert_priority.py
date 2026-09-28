"""
red_alert_priority.py

===========================================================================
THIS PROJECT USES TWO SEPARATE CLASSIFICATION LAYERS - understanding the
difference is key to explaining the dashboard clearly:

LAYER 1: HAZARD ZONE (Low / Moderate / High / Severe)
  - Computed in build_hazard_overlay.py, from terrain + rainfall alone
  - Answers: "How dangerous is this LAND, physically?"
  - Applies to ALL 15,201 villages
  - This is the base "Red Zone" hazard map itself

LAYER 2: RESPONSE PRIORITY (Immediate / Short-Term / Medium-Term Relocation)
  - Computed in THIS script, only for the High/Severe villages
  - Answers: "Given this village IS in a hazard zone, how urgently does
    it need action - considering population, evacuation access, and
    healthcare availability at the nearest safe relocation site?"
  - This is the ACTION/TRIAGE layer on top of the hazard layer
  - These three tier names are a deliberate, direct match to the problem
    statement's own wording: "prioritizes vulnerable habitations for
    immediate, short-term, and medium-term relocation." The underlying
    0-100 red_alert_score still has four natural bands (what used to be
    labeled CRITICAL/HIGH/MEDIUM/WATCH); WATCH is merged into
    "Medium-Term Relocation" here since the problem statement only names
    three categories, and every at-risk village should map cleanly onto
    one of them rather than falling into an unnamed fourth bucket.

In short: Hazard Zone tells you WHERE the danger is.
Response Priority tells you WHICH dangerous villages to act on FIRST,
and WHY (a plain-language explanation is generated per village).

This script also assigns guidance to every village, not just the at-risk
ones:
  - High/Severe villages: detailed relocation-focused safety measures
  - Moderate villages: "monitor and prepare" guidance
  - Low villages: brief general awareness note
===========================================================================

ASSUMPTIONS (documented so you can explain/defend these):
  - Vulnerable population ratio: ~37% of any population (India Census
    average: ~27% under 15, ~10% over 60) - used as a proxy since we
    don't have per-village age breakdowns
  - "Nearest good relocation site" = nearest candidate site scoring
    70+/100 suitability
  - Response priority thresholds are calibrated to this dataset's actual
    score range (typically 20-55), not arbitrary fixed cutoffs - this
    keeps the top category (Immediate Relocation) meaningful and
    reachable instead of empty

Run this from VS Code with your venv activated:
    python red_alert_priority.py

Requires outputs from earlier scripts:
  - data/outputs/at_risk_villages_enriched.csv
  - data/outputs/candidate_sites_enriched.csv
  - data/outputs/uttarakhand_villages_risk_tagged.geojson
"""

import os
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

OUTPUT_DIR = os.path.join("data", "outputs")
AT_RISK_PATH = os.path.join(OUTPUT_DIR, "at_risk_villages_enriched.csv")
CANDIDATES_PATH = os.path.join(OUTPUT_DIR, "candidate_sites_enriched.csv")
VILLAGES_GEOJSON_PATH = os.path.join(OUTPUT_DIR, "uttarakhand_villages_risk_tagged.geojson")

VULNERABLE_POPULATION_RATIO = 0.37  # proxy: children + seniors combined
GOOD_SITE_THRESHOLD = 70            # suitability score to count as "good"
METRIC_CRS = "EPSG:32644"

SAFETY_MEASURES = {
    "landslide": [
        "Watch for cracks in ground/walls, tilting trees or poles - early signs of slope movement.",
        "Avoid construction that alters natural slope drainage.",
        "Keep an evacuation route to higher, stable ground identified in advance.",
        "Do not ignore sudden increase in water flow or spring activity on slopes.",
    ],
    "flood_cloudburst": [
        "Keep emergency supplies (food, water, torch, first aid) ready during monsoon months.",
        "Avoid crossing flooded streams or roads on foot or by vehicle.",
        "Identify the nearest higher-ground shelter point in advance.",
        "Monitor IMD rainfall alerts during heavy monsoon periods.",
    ],
}

MODERATE_GUIDANCE = (
    "This area has some hazard exposure but is not currently flagged for "
    "relocation. Recommended: monitor conditions during monsoon season, "
    "keep a household emergency plan and evacuation route identified, "
    "and report any new ground cracks or unusual water flow to local "
    "authorities immediately - early reporting is what prevents a "
    "Moderate zone from becoming a High-risk one."
)
LOW_GUIDANCE = (
    "This area currently shows low hazard exposure based on terrain and "
    "rainfall patterns. General disaster preparedness awareness is still "
    "recommended, as with any location in a multi-hazard state."
)


# ---------------------------------------------------------------------------
# LOAD DATA
# ---------------------------------------------------------------------------
print("=== Loading data ===")
at_risk_df = pd.read_csv(AT_RISK_PATH)
candidates_df = pd.read_csv(CANDIDATES_PATH)
print(f"At-risk villages: {len(at_risk_df)}, Candidate sites: {len(candidates_df)}")

good_sites = candidates_df[candidates_df["suitability_score"] >= GOOD_SITE_THRESHOLD].copy()
print(f"'Good' relocation sites (score >= {GOOD_SITE_THRESHOLD}): {len(good_sites)}")

if good_sites.empty:
    print("WARNING: no sites meet the 'good' threshold - lowering it to include "
          "all scored candidates for this run.")
    good_sites = candidates_df.copy()

good_sites_gdf = gpd.GeoDataFrame(
    good_sites,
    geometry=gpd.points_from_xy(good_sites["longitude"], good_sites["latitude"]),
    crs="EPSG:4326",
).to_crs(METRIC_CRS)


# ---------------------------------------------------------------------------
# STEP 1: VULNERABLE POPULATION ESTIMATE
# ---------------------------------------------------------------------------
print("\n=== Step 1: Estimating vulnerable population ===")
at_risk_df["vulnerable_population_est"] = (
    at_risk_df["estimated_population"] * VULNERABLE_POPULATION_RATIO
).round().astype(int)
print("Added vulnerable_population_est (children + seniors proxy).")


# ---------------------------------------------------------------------------
# STEP 2: DISTANCE TO NEAREST GOOD RELOCATION SITE + ITS ADEQUACY
# ---------------------------------------------------------------------------
print("\n=== Step 2: Finding nearest good relocation site per village ===")

nearest_site_distances, nearest_hosp_adequacy, nearest_school_adequacy = [], [], []
nearest_suitability, nearest_lat, nearest_lon = [], [], []

for _, row in at_risk_df.iterrows():
    village_pt = gpd.GeoSeries(
        [Point(row["longitude"], row["latitude"])], crs="EPSG:4326"
    ).to_crs(METRIC_CRS).iloc[0]

    distances = good_sites_gdf.geometry.distance(village_pt)
    nearest_idx = distances.idxmin()
    nearest_site = good_sites_gdf.loc[nearest_idx]

    nearest_site_distances.append(round(distances.min() / 1000, 1))
    nearest_hosp_adequacy.append(nearest_site.get("hospital_adequacy", "Unknown"))
    nearest_school_adequacy.append(nearest_site.get("school_adequacy", "Unknown"))
    nearest_suitability.append(nearest_site.get("suitability_score", np.nan))
    nearest_lat.append(nearest_site.get("latitude", np.nan))
    nearest_lon.append(nearest_site.get("longitude", np.nan))

at_risk_df["nearest_good_site_distance_km"] = nearest_site_distances
at_risk_df["nearest_site_hospital_adequacy"] = nearest_hosp_adequacy
at_risk_df["nearest_site_school_adequacy"] = nearest_school_adequacy
at_risk_df["nearest_site_suitability"] = nearest_suitability
at_risk_df["nearest_site_lat"] = nearest_lat
at_risk_df["nearest_site_lon"] = nearest_lon
print("Added distance to nearest good site + its healthcare/school adequacy.")


# ---------------------------------------------------------------------------
# STEP 3: COMBINE INTO RESPONSE PRIORITY SCORE
# ---------------------------------------------------------------------------
print("\n=== Step 3: Calculating response priority score ===")

severity_map = {3: 70, 4: 100}
at_risk_df["severity_score"] = at_risk_df["risk_band"].map(severity_map).fillna(70)

max_vuln = at_risk_df["vulnerable_population_est"].max()
at_risk_df["population_score"] = (
    100 * at_risk_df["vulnerable_population_est"] / max_vuln if max_vuln > 0 else 0
)

max_dist = at_risk_df["nearest_good_site_distance_km"].max()
at_risk_df["relocation_distance_score"] = (
    100 * at_risk_df["nearest_good_site_distance_km"] / max_dist if max_dist > 0 else 0
)

at_risk_df["healthcare_gap_score"] = at_risk_df["nearest_site_hospital_adequacy"].apply(
    lambda x: 100 if x == "Insufficient" else 0
)

SEVERITY_WEIGHT = 0.40
POPULATION_WEIGHT = 0.25
DISTANCE_WEIGHT = 0.20
HEALTHCARE_WEIGHT = 0.15

at_risk_df["red_alert_score"] = (
    at_risk_df["severity_score"] * SEVERITY_WEIGHT
    + at_risk_df["population_score"] * POPULATION_WEIGHT
    + at_risk_df["relocation_distance_score"] * DISTANCE_WEIGHT
    + at_risk_df["healthcare_gap_score"] * HEALTHCARE_WEIGHT
).round(1)

# CHANGE (tier relabeling): the problem statement asks for exactly three
# relocation urgency categories -- "immediate, short-term, and medium-term
# relocation" -- so these tiers were renamed from the earlier internal
# CRITICAL/HIGH/MEDIUM/WATCH naming to match that language directly. The
# former WATCH band (score < 20) is merged into "Medium-Term Relocation"
# rather than kept as a separate fourth, unnamed category -- every at-risk
# village should map onto one of the three officially-named tiers.
# Thresholds are unchanged from before, still calibrated to this dataset's
# actual score range (see module docstring).
def priority_level(score):
    if score >= 50:
        return "Immediate Relocation"
    elif score >= 35:
        return "Short-Term Relocation"
    else:
        return "Medium-Term Relocation"

at_risk_df["response_priority"] = at_risk_df["red_alert_score"].apply(priority_level)

# Explicit numbered ranking - #1 = most urgent, unambiguous
at_risk_df = at_risk_df.sort_values("red_alert_score", ascending=False).reset_index(drop=True)
at_risk_df["priority_rank"] = at_risk_df.index + 1

print("Response priority calculated (Immediate / Short-Term / Medium-Term Relocation).")
print("\nPriority distribution:")
print(at_risk_df["response_priority"].value_counts())


# ---------------------------------------------------------------------------
# STEP 4: PLAIN-LANGUAGE "WHY IS THIS VILLAGE PRIORITIZED?" EXPLANATION
# ---------------------------------------------------------------------------
print("\n=== Step 4: Building priority explanations ===")

def explain_priority(row):
    reasons = []
    if row["severity_score"] >= 100:
        reasons.append("it sits in a Severe hazard zone")
    elif row["severity_score"] >= 70:
        reasons.append("it sits in a High hazard zone")
    if row["population_score"] >= 60:
        reasons.append("it has a large vulnerable population (children/seniors)")
    elif row["population_score"] >= 30:
        reasons.append("it has a moderate vulnerable population")
    if row["relocation_distance_score"] >= 60:
        reasons.append("it is relatively far from a suitable relocation site")
    if row["healthcare_gap_score"] > 0:
        reasons.append("its nearest relocation site currently lacks adequate healthcare access")
    if not reasons:
        reasons.append("it has a comparatively lower combination of risk factors")
    return "This village is prioritized because " + "; ".join(reasons) + "."

at_risk_df["priority_explanation"] = at_risk_df.apply(explain_priority, axis=1)
print("Explanations generated for each village.")


# ---------------------------------------------------------------------------
# STEP 5: SAFETY MEASURES BASED ON DOMINANT HAZARD TYPE (at-risk villages)
# ---------------------------------------------------------------------------
print("\n=== Step 5: Attaching safety measure guidance ===")

def get_hazard_type(row):
    # Simple heuristic: Severe classification in our overlay was driven
    # primarily by slope -> treat as landslide-dominant; otherwise treat
    # as flood/cloudburst-dominant (rainfall-driven).
    return "landslide" if row["risk_band"] == 4 else "flood_cloudburst"

at_risk_df["hazard_type"] = at_risk_df.apply(get_hazard_type, axis=1)
at_risk_df["safety_measures"] = at_risk_df["hazard_type"].map(
    lambda h: " | ".join(SAFETY_MEASURES[h])
)
print("Safety measure guidance attached (landslide vs flood/cloudburst sets).")


# ---------------------------------------------------------------------------
# SAVE AT-RISK VILLAGES OUTPUT
# ---------------------------------------------------------------------------
out_path = os.path.join(OUTPUT_DIR, "villages_red_alert.csv")
at_risk_df.to_csv(out_path, index=False)
print(f"\nSaved -> {out_path}")

print("\nTop 10 highest-priority villages (rank 1 = act on this FIRST):")
print(at_risk_df[["priority_rank", "name", "response_priority", "red_alert_score",
                   "estimated_population", "vulnerable_population_est",
                   "nearest_good_site_distance_km",
                   "nearest_site_hospital_adequacy"]].head(10).to_string(index=False))


# ---------------------------------------------------------------------------
# STEP 6: MERGE INTO FULL VILLAGES GEOJSON (for map click-details)
# ---------------------------------------------------------------------------
print("\n=== Step 6: Merging detail into full villages map layer ===")

all_villages_gdf = gpd.read_file(VILLAGES_GEOJSON_PATH)

if "osm_id" in at_risk_df.columns and "osm_id" in all_villages_gdf.columns:
    print("Matching villages using osm_id (unique identifier).")
    merge_key = "osm_id"
    at_risk_df["osm_id"] = at_risk_df["osm_id"].astype(str)
    all_villages_gdf["osm_id"] = all_villages_gdf["osm_id"].astype(str)
else:
    print("WARNING: osm_id not found - falling back to name+coordinate "
          "matching, which is less reliable for villages with missing "
          "or duplicate names.")
    at_risk_df["_match_key"] = (
        at_risk_df["name"].astype(str) + "_" +
        at_risk_df["latitude"].round(5).astype(str) + "_" +
        at_risk_df["longitude"].round(5).astype(str)
    )
    all_villages_gdf["_match_key"] = (
        all_villages_gdf.get("name", "").astype(str) + "_" +
        all_villages_gdf.geometry.y.round(5).astype(str) + "_" +
        all_villages_gdf.geometry.x.round(5).astype(str)
    )
    merge_key = "_match_key"

detail_cols = [
    merge_key, "response_priority", "priority_rank", "red_alert_score",
    "priority_explanation", "estimated_population", "vulnerable_population_est",
    "households_needing_relocation", "land_required_sqm",
    "distance_to_road_km", "nearest_road_type", "evacuation_urgency",
    "nearest_good_site_distance_km", "nearest_site_hospital_adequacy",
    "nearest_site_school_adequacy", "nearest_site_suitability",
    "nearest_site_lat", "nearest_site_lon", "hazard_type", "safety_measures",
]
merged = all_villages_gdf.merge(at_risk_df[detail_cols], on=merge_key, how="left")
if merge_key in merged.columns:
    merged = merged.drop(columns=merge_key)


# ---------------------------------------------------------------------------
# STEP 7: GUIDANCE FOR MODERATE/LOW RISK VILLAGES (all of them, not just the at-risk subset)
# ---------------------------------------------------------------------------
print("\n=== Step 7: Adding guidance for Moderate/Low risk villages ===")

def general_guidance(risk_label):
    if risk_label == "Moderate":
        return MODERATE_GUIDANCE
    elif risk_label == "Low":
        return LOW_GUIDANCE
    return ""  # High/Severe villages already have detailed guidance above

def zone_action_type(risk_label):
    if risk_label == "Moderate":
        return "Monitor & Prepare"
    elif risk_label == "Low":
        return "Standard Awareness"
    return ""  # High/Severe villages use response_priority instead

merged["general_safety_guidance"] = merged["risk_label"].apply(general_guidance)
merged["zone_action_type"] = merged["risk_label"].apply(zone_action_type)
print("Guidance added for all Moderate and Low risk villages.")


# ---------------------------------------------------------------------------
# SAVE FINAL MERGED OUTPUT
# ---------------------------------------------------------------------------
merged_out_path = os.path.join(OUTPUT_DIR, "uttarakhand_villages_full_detail.geojson")
merged.to_file(merged_out_path, driver="GeoJSON")

matched_count = merged["response_priority"].notna().sum()
print(f"\nMerged detail into full village layer: {matched_count} villages "
      f"carry full at-risk detail, {len(merged) - matched_count} carry "
      f"basic/moderate/low guidance only.")

if matched_count != len(at_risk_df):
    print(
        f"\nWARNING: Expected {len(at_risk_df)} matches (one per at-risk "
        f"village) but got {matched_count}. This usually means duplicate "
        "or colliding match keys - investigate before trusting this output."
    )
else:
    print(f"Sanity check passed: matched count ({matched_count}) equals "
          f"at-risk village count ({len(at_risk_df)}).")

print(f"Saved -> {merged_out_path}")

print(
    "\nDone. Two output files ready:\n"
    "  - villages_red_alert.csv (the at-risk villages, ranked with "
    "response priority, population/capacity math, and safety guidance)\n"
    "  - uttarakhand_villages_full_detail.geojson (ALL 15,201 villages, "
    "with tiered guidance: detailed for High/Severe, general for "
    "Moderate/Low) - ready for the frontend."
)