import os
import pickle
import pandas as pd
from flask import Flask, render_template, request, jsonify
import folium
from shapely.geometry import Point
from shapely.ops import transform
from pyproj import Transformer
import geopandas as gpd


# Initialize Flask app
app = Flask(__name__)

# Load the data
gdf = gpd.read_parquet('data/duplicate-candidates-5.parquet')
progress_file = 'data/labeling-progress.pickle'

# Load progress if exists
if os.path.exists(progress_file):
    results = pd.read_pickle(progress_file).to_dict('records')
else:
    results = []

already_labeled_ids = [item['id'] for item in results]
candidates = gdf[gdf.index == gdf['candidate_id']].sort_values('dataset').drop(already_labeled_ids)

# Helper function to convert geometry to lat/lng
def _lng_lat(p: Point, source_crs: str):
    t = Transformer.from_crs(source_crs, "EPSG:4326", always_xy=True)
    transformed_p = transform(t.transform, p)
    return transformed_p.y, transformed_p.x

# Function to get neighbors
def _get_neighbors(location, gdf, dis):
    neighbor_idx = gdf.sindex.query(location, predicate="dwithin", distance=dis)
    return gdf.iloc[neighbor_idx]

# Show duplicate candidate and neighbors
@app.route("/show_candidate/<int:i>")
def show_candidate(i):
    if i >= len(candidates):
        return "All buildings labeled!", 200

    candidate = candidates.iloc[i]
    centroid = candidate.geometry.centroid
    lng, lat = _lng_lat(centroid, gdf.crs)
    
    # Create Folium map centered on the candidate
    m = folium.Map(location=[lng, lat], zoom_start=19)

    # Add neighbors to the map (existing and new)
    gdf_neighbors_existing = gdf[(gdf['candidate_id'] == candidate.name) & (gdf['dataset'] != candidate.dataset)]
    gdf_neighbors_new = gdf[(gdf['candidate_id'] == candidate.name) & (gdf['dataset'] == candidate.dataset) & (gdf.index != candidate.name)]
    folium.GeoJson(gdf_neighbors_existing.to_crs("EPSG:4326"), style_function=lambda x: {'color': 'skyblue', 'fillOpacity': 1}).add_to(m)
    folium.GeoJson(gdf_neighbors_new.to_crs("EPSG:4326"), style_function=lambda x: {'color': 'coral', 'fillOpacity': 0.2}).add_to(m)

    # Highlight the candidate building
    folium.GeoJson(candidates.iloc[[i]].geometry.to_crs("EPSG:4326"), style_function=lambda x: {'color': 'red', 'weight': 3}).add_to(m)

    # m = gdf_neighbors_existing.explore(m=m, color='skyblue', style_kwds={"fillOpacity": 1})
    # m = gdf_neighbors_new.explore(m=m, color='coral', style_kwds={"fillOpacity": 0.2})
    
    # # Highlight candidate building with red boundary
    # m = candidates.iloc[[i]].explore(color="red", m=m, style_kwds={"fillOpacity": 0, "weight": 2.5})

    # Save the map to an HTML file
    map_file = f"static/maps/candidate_{i}.html"
    m.save(map_file)
    
    return render_template("show_candidate.html", i=str(i))


# Label the candidate (duplicate or not)
@app.route("/label_candidate/<int:i>/<label>", methods=["POST"])
def label_candidate(i, label):
    id = candidates.iloc[i].name
    results.append({"id": id, "duplicate": label})
    # Store progress
    with open(progress_file, "wb") as f:
        pickle.dump(pd.DataFrame(results), f)
    
    return jsonify({"message": "Success", "next_candidate": i + 1})

# Home route
@app.route("/")
def home():
    return render_template("index.html")

if __name__ == "__main__":
    app.run(debug=True)
