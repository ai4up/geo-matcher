import atexit
import webbrowser
import os
from pathlib import Path
import shutil

import click
import pandas as pd
from flask import Flask, render_template, request, jsonify
from flask_executor import Executor
import folium
import geopandas as gpd
from waitress import serve

app = Flask(__name__)
executor = Executor(app)
maps_dir = Path(app.static_folder) / "maps"
shutil.rmtree(maps_dir, ignore_errors=True)
maps_dir.mkdir(parents=True, exist_ok=True)

PROGRESS_FILE = 'data/labeling-progress.pickle'
RESULTS_FILE = 'results.csv'

gdf_all = None
candidates = None
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
    click.echo(f"Loaded {len(gdf_all)} buildings from {filepath}")

    results = _load_progress()
    already_labeled_ids = [duplicate['id'] for duplicate in results]
    click.echo(f"Loaded latest labeling state: {len(results)} buildings already labeled")

    candidates = gdf_all[(gdf_all.index == gdf_all['candidate_id']) & (~gdf_all['candidate_id'].isin(already_labeled_ids))].sort_values('dataset')
    click.echo(f"Starting labeling of {len(candidates)} buildings...")

    create_html(0)
    atexit.register(_store_results)

    click.echo("Opening browser...")
    webbrowser.open('http://127.0.0.1:5001/show_candidate/0')
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

    next_html = maps_dir / f"candidate_{i+1}.html"
    if not next_html.is_file():
        app.logger.debug(f"Pre-generating HTML map for candidate {i+1}")
        executor.submit(create_html, i+1)

    return render_template("show_candidate.html", label_function_script=_js_labeling_func(), i=i)


def create_html(i):
    if i >= len(candidates):
        app.logger.info(f"HTML maps have already been generated for all candidates.")
        return

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

    m.save(maps_dir / f"candidate_{i}.html")


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
    click.echo(f"All buildings successfully labled. Results stored in {RESULTS_FILE}.")


def _store_progress():
    pd.DataFrame(results).to_pickle(PROGRESS_FILE)


def _load_progress():
    if os.path.exists(PROGRESS_FILE):
        return pd.read_pickle(PROGRESS_FILE).to_dict('records')

    return []

if __name__ == "__main__":
    main()
