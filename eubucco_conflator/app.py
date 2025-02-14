import atexit
import warnings
import webbrowser
from pathlib import Path
import shutil

from flask import Flask, render_template, request, jsonify
from flask_executor import Executor
import folium
from waitress import serve

from eubucco_conflator.state import State as s
from eubucco_conflator.state import RESULTS_FILE

app = Flask(__name__)
app.url_map.strict_slashes = False
executor = Executor(app)
maps_dir = Path(app.static_folder) / "maps"


def start():
    _clean_maps_dir()
    atexit.register(s.store_results)

    webbrowser.open(f"http://127.0.0.1:5001/show_candidate")
    serve(app, host="127.0.0.1", port=5001)


@app.route("/")
def home():
    return render_template("index.html")


@app.route('/store-label', methods=['POST'])
def store_label():
    data = request.json

    id = data.get("id")
    label = data.get("label")
    existing_id = data.get("existing_id")
    s.add_result(id, label, existing_id)

    return jsonify({"message": "Success", "candidate": s.current_candidate_id() or ''})


@app.route("/show_candidate")
@app.route("/show_candidate/<id>")
def show_candidate(id=None):
    if id is None:
        id = s.current_candidate_id()

    if id is None:
        s.store_results()
        return f"All buildings labeled! Results stored in {RESULTS_FILE}", 200

    if id not in s.candidates.index:
        return "Candidate not found", 404

    _create_html(id)

    if next_id := s.next_candidate_id():
        app.logger.debug(f"Pre-generating HTML map for candidate {next_id}")
        executor.submit(_create_html, next_id)

    return render_template("show_candidate.html", label_function_script=_labeling_func_js(), id=id)


def _html_exists(id):
    return (maps_dir / f"candidate_{id}.html").is_file()


def _create_html(id):
    if _html_exists(id):
        return

    candidate = s.candidates.loc[id]
    gdf = s.gdf[s.gdf['candidate_id'] == id]

    # Create base map centered on candidate building
    m = _initialize_map(candidate)

    # Add layers
    _create_existing_buildings_layer(gdf, candidate).add_to(m)
    _create_new_buildings_layer(gdf, candidate).add_to(m)
    _create_candidate_building_layer(candidate).add_to(m)

    # Add JavaScript labeling function and legend
    m.get_root().html.add_child(folium.Element(_labeling_func_js()))
    m.get_root().html.add_child(folium.Element(_legend_html()))

    # Add LayerControl for toggling layers
    folium.LayerControl(collapsed=True).add_to(m)

    # Save the map
    m.save(maps_dir / f"candidate_{id}.html")


def _initialize_map(candidate):
    with warnings.catch_warnings():
        warnings.simplefilter(action='ignore', category=UserWarning)
        centroid = candidate.geometry.centroid

    m = folium.Map(location=[centroid.y, centroid.x], zoom_start=20, tiles=None)

    folium.TileLayer('CartoDB.Positron', name='CartoDB Positron', show=True).add_to(m)
    folium.TileLayer('OpenStreetMap', name='OpenStreetMap', show=False).add_to(m)
    folium.TileLayer('Esri.WorldTopoMap', name='Esri WorldTopoMap', show=False, max_native_zoom=18, max_zoom=19).add_to(m)

    return m


def _create_existing_buildings_layer(gdf, candidate):
    existing_buildings = folium.FeatureGroup(name="Existing Buildings")
    gdf_existing = gdf[gdf['dataset'] != candidate.dataset]

    for _, row in gdf_existing.iterrows():
        html_str = f"<button onclick=\"labelPair('{candidate.name}', 'yes', '{row.name}')\">Duplicated</button>"
        html = folium.Html(html_str, script=True)
        popup = folium.Popup(html, max_width=300)
        coords = _lat_lon(row.geometry)
        folium.Polygon(coords, popup=popup, color='skyblue', fill=True, fill_opacity=0.5).add_to(existing_buildings)

    return existing_buildings


def _create_new_buildings_layer(gdf, candidate):
    new_buildings = folium.FeatureGroup(name="New Buildings")
    gdf_new = gdf[(gdf['dataset'] == candidate.dataset) & (gdf.index != candidate.name)]

    folium.GeoJson(
        gdf_new, style_function=lambda _: {'color': 'coral', 'fillOpacity': 0.2}
    ).add_to(new_buildings)

    return new_buildings


def _create_candidate_building_layer(candidate):
    candidate_building = folium.FeatureGroup(name="Duplicate Candidate")
    coords = _lat_lon(candidate.geometry)

    folium.Polygon(coords, color='red', weight=3, fill=False).add_to(candidate_building)

    return candidate_building


def _labeling_func_js():
    return """
    <script>
        // Global function accessible from popups
        function labelPair(id, label, existing_id) {
            fetch('/store-label', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    id: id,
                    label: label,
                    existing_id: existing_id
                })
            })
            .then(response => response.json())
            .then(data => {
                console.log("Saved:", data);
                window.location.href = `/show_candidate/${data.candidate}`;
            });
        }
    </script>
    """


def _legend_html():
    return """
        <div style="position: fixed;
                    bottom: 30px; left: 30px;
                    background: rgba(255, 255, 255, 0.8); border: 1px solid lightgrey;
                    z-index: 9999; font-size: 14px; padding: 10px;">
            <b style="display: block; margin-bottom: 5px;">Building Layers</b>
            <i style="background: transparent; width: 18px; height: 18px; display: inline-block; border: 3px solid red;"></i> Duplicate Candidate<br>
            <i style="background: rgba(255, 127, 80, 0.2); width: 18px; height: 18px; display: inline-block; border: 2px solid coral;"></i> New Building<br>
            <i style="background: rgba(135, 206, 235, 0.5); width: 18px; height: 18px; display: inline-block; border: 2px solid skyblue;"></i> Existing Building
        </div>
    """

def _lat_lon(geom):
    return [(lat, lon) for lon, lat in geom.exterior.coords]


def _clean_maps_dir():
    shutil.rmtree(maps_dir, ignore_errors=True)
    maps_dir.mkdir(parents=True, exist_ok=True)
