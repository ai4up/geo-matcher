import atexit
import webbrowser
import os

import click
import pandas as pd
from flask import Flask, render_template, request, jsonify
import folium
import geopandas as gpd

app = Flask(__name__)

PROGRESS_FILE = 'data/labeling-progress.pickle'
RESULTS_FILE = 'results.csv'

gdf_all = None
results = []


@click.command()
@click.argument('filepath', required=True, type=click.Path(exists=True))
def main(filepath):
    """Start labeling of duplicated buildings.

    FILEPATH to GeoParquet file which contains the buildings to label.
    """
    global gdf_all
    global results
    global candidates
 
    gdf_all = gpd.read_parquet(filepath).to_crs("EPSG:4326")
    results = _load_progress()
    already_labeled_ids = [item['id'] for item in results]
    candidates = gdf_all[gdf_all.index == gdf_all['candidate_id']].sort_values('dataset').drop(already_labeled_ids)

    atexit.register(_store_results)
    webbrowser.open('http://127.0.0.1:5001/show_candidate/0')
    from waitress import serve
    serve(app, host="127.0.0.1", port=5001)


@app.route("/")
def home():
    return render_template("index.html")


@app.route('/store-label', methods=['POST'])
def store_label():
    data = request.json
    i = data.get("i")

    id = candidates.iloc[i].name
    label = data.get("label")
    existing_id = data.get("existing_id")
    results.append({"id": id, "duplicate": label, "existing_id": existing_id})
    
    _store_progress()

    return jsonify({"message": "Success", "next_candidate": i + 1})


@app.route("/show_candidate/<int:i>")
def show_candidate(i):
    if i >= len(candidates):
        _store_results()
        return f"All buildings labeled! Results stored in {RESULTS_FILE}", 200

    candidate = candidates.iloc[i]
    centroid = candidate.geometry.centroid
    gdf = gdf_all[gdf_all['candidate_id'] == candidate.name]
    
    # Create Folium map centered on the candidate
    m = folium.Map(location=[centroid.y, centroid.x], zoom_start=19)

    # Add new buildings to the map (for reference)
    gdf_neighbors_new = gdf[(gdf['dataset'] == candidate.dataset) & (gdf.index != candidate.name)]
    folium.GeoJson(gdf_neighbors_new, style_function=lambda _: {'color': 'coral', 'fillOpacity': 0.2}).add_to(m)
    
    # Add existing buildings to the map
    gdf_neighbors_existing = gdf[gdf['dataset'] != candidate.dataset]
    for _, row in gdf_neighbors_existing.iterrows():
        html_str = f"<button onclick=\"labelPair({i}, 'yes', '{row.name}')\">Duplicated</button>"
        html = folium.Html(html_str, script=True)
        popup = folium.Popup(html, max_width=300)
        coords = _lat_lon(row.geometry)
        folium.Polygon(coords, popup=popup, color='skyblue', fill=True, fill_opacity=0.5).add_to(m)

    # Highlight the candidate building
    coords = _lat_lon(candidate.geometry)
    folium.Polygon(coords, color='red', weight=3).add_to(m)

    # Add the script to the map’s HTML
    m.get_root().html.add_child(folium.Element(_js_labeling_func()))
    map_html = m.get_root()._repr_html_()

    return render_template("show_candidate.html", map_html=map_html, label_function_script=_js_labeling_func(), i=i)


def _js_labeling_func():
    func = """
    <script>
        // Global function accessible from popups
        function labelPair(i, label, existing_id) {
            fetch('/store-label', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    i: i,
                    label: label,
                    existing_id: existing_id
                })
            })
            .then(response => response.json())
            .then(data => {
                console.log("Saved:", data);
                window.location.href = `/show_candidate/${data.next_candidate}`;
            });
        }
    </script>
    """
    return func


def _lat_lon(geom):
    return [(lat, lon) for lon, lat in geom.exterior.coords]


def _store_results():
    pd.DataFrame(results).to_csv(RESULTS_FILE, index=False)


def _store_progress():
    pd.DataFrame(results).to_pickle(PROGRESS_FILE)


def _load_progress():
    if os.path.exists(PROGRESS_FILE):
        return pd.read_pickle(PROGRESS_FILE).to_dict('records')
    
    return []

if __name__ == "__main__":
    main()
