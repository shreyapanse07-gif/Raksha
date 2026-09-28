"""
app.py

Flask backend serving the Raksha project's data as JSON APIs, for the
custom HTML/CSS/JS frontend to consume.

Run this from VS Code with your venv activated:
    python app.py

Then open http://localhost:5000 in your browser.

First-time setup needed:
    pip install flask

CHANGE LOG:
  - Added /api/alerts, serving hazard_predictor.py's alerts.json output
    (the live Open-Meteo rainfall-trigger check for the Immediate
    Relocation tier). This is what the frontend's "LIVE" badge actually
    reads from now -- previously nothing served this file, so the badge
    was decorative. Note: this is a SNAPSHOT of whenever
    hazard_predictor.py was last run, not a live call on every page
    load -- re-run that script before a demo to refresh it.
"""

import os
import json
import pandas as pd
import geopandas as gpd
from flask import Flask, jsonify, render_template, send_from_directory

app = Flask(__name__, template_folder="templates", static_folder="static")

DATA_DIR = os.path.join("data", "uttarakhand")
OUTPUT_DIR = os.path.join("data", "outputs")

VILLAGES_PATH = os.path.join(OUTPUT_DIR, "uttarakhand_villages_full_detail.geojson")
AT_RISK_ENRICHED_PATH = os.path.join(OUTPUT_DIR, "villages_red_alert.csv")
CANDIDATES_ENRICHED_PATH = os.path.join(OUTPUT_DIR, "candidate_sites_enriched.csv")
BOUNDARY_PATH = os.path.join(DATA_DIR, "uttarakhand_boundary.geojson")
ALERTS_PATH = os.path.join(OUTPUT_DIR, "alerts.json")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/summary")
def api_summary():
    villages_gdf = gpd.read_file(VILLAGES_PATH)
    at_risk_df = pd.read_csv(AT_RISK_ENRICHED_PATH)
    candidates_df = pd.read_csv(CANDIDATES_ENRICHED_PATH)

    return jsonify({
        "total_villages": len(villages_gdf),
        "high_risk_villages": len(at_risk_df),
        "total_at_risk_population": int(at_risk_df["estimated_population"].sum()),
        "total_households_needing_relocation": int(at_risk_df["households_needing_relocation"].sum()),
        "candidate_sites_scored": len(candidates_df),
        "top_site_suitability": float(candidates_df["suitability_score"].max()),
        "risk_distribution": villages_gdf["risk_label"].value_counts().to_dict(),
    })

@app.route("/api/villages")
def api_villages():
    """All villages, with rich detail attached for the ones that are
    at-risk (response priority, population, safety measures, nearest
    relocation site) - Low/Moderate villages carry general guidance."""
    villages_gdf = gpd.read_file(VILLAGES_PATH)
    keep_cols = [c for c in [
        "name", "risk_label", "risk_band",
        "response_priority", "priority_rank", "red_alert_score",
        "priority_explanation",
        "estimated_population", "vulnerable_population_est",
        "households_needing_relocation", "land_required_sqm",
        "distance_to_road_km", "nearest_road_type", "evacuation_urgency",
        "nearest_good_site_distance_km", "nearest_site_hospital_adequacy",
        "nearest_site_school_adequacy", "nearest_site_suitability",
        "nearest_site_lat", "nearest_site_lon", "hazard_type",
        "safety_measures", "general_safety_guidance", "zone_action_type",
        "geometry",
    ] if c in villages_gdf.columns]
    villages_gdf = villages_gdf[keep_cols]
    return app.response_class(
        villages_gdf.to_json(), mimetype="application/json"
    )


@app.route("/api/at-risk-villages")
def api_at_risk_villages():
    """Enriched at-risk villages with population/capacity/road detail."""
    df = pd.read_csv(AT_RISK_ENRICHED_PATH)
    df = df.fillna("")
    return jsonify(df.to_dict(orient="records"))


@app.route("/api/candidate-sites")
def api_candidate_sites():
    """Enriched candidate relocation sites with needs adequacy."""
    df = pd.read_csv(CANDIDATES_ENRICHED_PATH)
    df = df.fillna("")
    return jsonify(df.to_dict(orient="records"))


@app.route("/api/boundary")
def api_boundary():
    """Uttarakhand state boundary, for drawing the outline on the map."""
    boundary_gdf = gpd.read_file(BOUNDARY_PATH)
    return app.response_class(
        boundary_gdf.to_json(), mimetype="application/json"
    )


@app.route("/api/alerts")
def api_alerts():
    """Live rainfall-trigger check results for the Immediate Relocation
    tier, from hazard_predictor.py's alerts.json. This is a SNAPSHOT of
    whenever that script was last run -- re-run it before a demo to
    refresh these numbers. Returns an empty list (not an error) if the
    file doesn't exist yet, so the frontend can handle "not run yet"
    gracefully."""
    if not os.path.exists(ALERTS_PATH):
        return jsonify([])
    with open(ALERTS_PATH, "r", encoding="utf-8") as f:
        alerts = json.load(f)
    return jsonify(alerts)


if __name__ == "__main__":
    app.run(debug=True, port=5000)