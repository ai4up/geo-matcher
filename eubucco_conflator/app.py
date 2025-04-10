import atexit
import shutil
import warnings
import webbrowser
from pathlib import Path
from typing import Optional

import folium
import geopandas as gpd
from flask import Flask, Response, jsonify, render_template, request
from flask_executor import Executor
from folium.plugins import Draw
from geopandas import GeoDataFrame
from shapely.geometry import LineString
from waitress import serve

from eubucco_conflator.state import RESULTS_FILE
from eubucco_conflator.state import State as s

app = Flask(__name__)
app.url_map.strict_slashes = False
executor = Executor(app)
maps_dir = Path(app.static_folder) / "maps"


def start() -> None:
    _clean_maps_dir()
    atexit.register(s.store_results)

    webbrowser.open("http://127.0.0.1:5001")
    serve(app, host="127.0.0.1", port=5001)


@app.route("/")
def home() -> str:
    _create_tutorial_html()
    return render_template("index.html")


@app.route("/store-label", methods=["POST"])
def store_label() -> Response:
    data = request.json

    id = data.get("id")
    label = data.get("label")
    existing_id = data.get("existing_id")
    s.add_result(id, label, existing_id)

    return jsonify({"message": "Success", "candidate": s.current_candidate_id() or ""})


@app.route("/store-neighborhood", methods=["POST"])
def store_neighborhood() -> Response:
    data = request.json

    added = data.get("added")
    removed = data.get("removed")

    print("Added geometries:", added)
    print("Removed geometries:", removed)

    return jsonify({"message": "Saved", "candidate": s.current_candidate_id() or ""})


@app.route("/show_candidate")
@app.route("/show_candidate/<id>")
def show_candidate(id: Optional[str] = None) -> str:
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

    return render_template(
        "show_candidate.html", label_function_script=_labeling_func_js(), id=id
    )


def _html_exists(id: str) -> bool:
    return (maps_dir / f"candidate_{id}.html").is_file()


def _create_tutorial_html() -> None:
    # Load demo data
    demo_data_path = Path(__file__).parent / "data" / "tutorial-candidate.parquet"
    gdf = gpd.read_parquet(demo_data_path).to_crs("EPSG:4326")
    candidate = gdf.loc["demo"]

    # Initialize map and add demo buildings
    m = _initialize_map(candidate)
    _create_existing_buildings_layer(gdf, candidate).add_to(m)
    _create_new_buildings_layer(gdf, candidate).add_to(m)

    _add_tutorial_markers(m, gdf, candidate)
    m.get_root().html.add_child(folium.Element(_demo_labeling_func_js()))

    m.save(maps_dir / "candidate_demo.html")


def _create_html(id: str, bulk: bool = True) -> None:
    if _html_exists(id):
        return

    candidate = s.candidates.loc[id]
    gdf = s.gdf[s.gdf["candidate_id"] == id]

    m = _initialize_map(candidate)

    _create_existing_buildings_layer(gdf, candidate).add_to(m)
    _create_new_buildings_layer(gdf, candidate).add_to(m)

    m.get_root().html.add_child(folium.Element(_labeling_func_js()))
    m.get_root().html.add_child(folium.Element(_legend_html()))

    if bulk:
        _create_drawing_layer().add_to(m)
        _add_matching_edges(gdf, candidate, m)

        m.get_root().html.add_child(folium.Element(_store_added_edges_js(m.get_name())))

    folium.LayerControl(collapsed=True).add_to(m)

    m.save(maps_dir / f"candidate_{id}.html")


def _initialize_map(candidate: GeoDataFrame) -> folium.Map:
    with warnings.catch_warnings():
        warnings.simplefilter(action="ignore", category=UserWarning)
        centroid = candidate.geometry.centroid

    m = folium.Map(location=[centroid.y, centroid.x], zoom_start=20, tiles=None)

    folium.TileLayer("CartoDB.Positron", name="CartoDB Positron", show=True).add_to(m)
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap", show=False).add_to(m)
    folium.TileLayer(
        "Esri.WorldTopoMap",
        name="Esri WorldTopoMap",
        show=False,
        max_native_zoom=18,
        max_zoom=19,
    ).add_to(m)

    return m


def _add_tutorial_markers(
    m: folium.Map, gdf: GeoDataFrame, candidate: GeoDataFrame
) -> None:
    gdf_existing = gdf[gdf["dataset"] != candidate.dataset]
    folium.Marker(
        location=[candidate.geometry.centroid.y, candidate.geometry.centroid.x],
        tooltip="Building to be labeled.",
        icon=folium.Icon(color="red", icon="info-sign"),
    ).add_to(m)

    for _, row in gdf_existing.iterrows():
        folium.Marker(
            location=[row.geometry.centroid.y, row.geometry.centroid.x],
            tooltip="Existing building. Click on it to indicate that it is being duplicated by the red building.",
            icon=folium.Icon(color="blue", icon="info-sign"),
        ).add_to(m)


def _create_existing_buildings_layer(
    gdf: GeoDataFrame, candidate: GeoDataFrame
) -> folium.FeatureGroup:
    existing_buildings = folium.FeatureGroup(name="Existing Buildings")
    gdf_existing = gdf[gdf["dataset"] != candidate.dataset]

    folium.GeoJson(
        gdf_existing, style_function=lambda _: {"color": "skyblue", "fillOpacity": 0.5}
    ).add_to(existing_buildings)

    return existing_buildings


def _create_new_buildings_layer(
    gdf: GeoDataFrame, candidate: GeoDataFrame
) -> folium.FeatureGroup:
    new_buildings = folium.FeatureGroup(name="New Buildings")
    gdf_new = gdf[gdf["dataset"] == candidate.dataset]

    folium.GeoJson(
        gdf_new, style_function=lambda _: {"color": "coral", "fillOpacity": 0.2}
    ).add_to(new_buildings)

    return new_buildings


def _add_matching_edges(
    gdf: GeoDataFrame, candidate: GeoDataFrame, m: folium.Map
) -> folium.FeatureGroup:
    gdf_new = gdf[gdf["dataset"] == candidate.dataset]
    gdf_existing = gdf[gdf["dataset"] != candidate.dataset]
    gdf_initial_matches = _connect_nearest_neighbors(gdf_new, gdf_existing)

    map_name = m.get_name()
    m.get_root().html.add_child(
        folium.Element(
            f"""
    <script>
    document.addEventListener("DOMContentLoaded", function () {{
        window.removedMatches = [];

        const map = window.{map_name};
        const initialMatches = {gdf_initial_matches.to_json()};
        const matchLayer = L.geoJSON(initialMatches, {{
            style: function(feature) {{
                return {{ color: 'green', weight: 3 }};
            }},
            onEachFeature: function (feature, layer) {{

                // Add click event to remove the match edge
                layer.on('click', function () {{
                    map.removeLayer(layer);
                    window.removedMatches.push(feature);
                    console.log("Removed match:", feature);
                }});

                // Highlight on hover
                layer.on('mouseover', function () {{
                    layer.setStyle({{
                        color: 'lime',
                        weight: 4
                    }});
                }});

                // Reset style when mouse leaves
                layer.on('mouseout', function () {{
                    layer.setStyle({{
                        color: 'green',
                        weight: 3
                    }});
                }});
            }}
        }}).addTo(map);
    }});
    </script>
    """
        )
    )


def _create_drawing_layer() -> Draw:
    draw = Draw(
        show_geometry_on_click=False,
        draw_options={
            "polyline": True,
            "polygon": False,
            "circle": False,
            "marker": False,
            "rectangle": False,
            "circlemarker": False,
        },
        edit_options={"edit": False, "remove": True},
    )

    return draw


def _store_added_edges_js(map_name: str) -> str:
    return f"""
    <script>
        document.addEventListener("DOMContentLoaded", function () {{
            const map = window.{map_name};
            window.addedMatches = [];

            map.on('draw:created', function (e) {{
                const geojson = e.layer.toGeoJSON();
                window.addedMatches.push({{
                    layer: e.layer,
                    geojson: geojson
                }});
                console.log("Added match edge:", geojson);
            }});

            map.on('draw:deleted', function (e) {{
                e.layers.eachLayer(function (deletedLayer) {{
                    window.addedMatches = window.addedMatches.filter(entry => entry.layer !== deletedLayer);
                    console.log("Deleted layer:", deletedLayer);
                }});
            }});
        }});
    </script>
    """


def _labeling_func_js() -> str:
    return """
    <script>
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


def _demo_labeling_func_js() -> str:
    return """
    <script>
        function labelPair(id, label, existing_id) {
            alert("Demo only. No data will be saved.");
        }
    </script>
    """


def _legend_html() -> str:
    return """
        <div style="position: fixed;
                    bottom: 30px; left: 30px;
                    background: rgba(255, 255, 255, 0.8); border: 1px solid lightgrey;
                    z-index: 9999; font-size: 14px; padding: 10px;">
            <b style="display: block; margin-bottom: 5px;">Building Layers</b>
            <i style="background: transparent; width: 18px; height: 18px; display: inline-block; border: 3px solid red;"></i> Duplicate Candidate<br>
            <i style="background: rgba(255, 127, 80, 0.2); width: 18px; height: 18px; display: inline-block; border: 2px solid coral;"></i> Other New Buildings<br>
            <i style="background: rgba(135, 206, 235, 0.5); width: 18px; height: 18px; display: inline-block; border: 2px solid skyblue;"></i> Existing Buildings
        </div>
    """


def _clean_maps_dir() -> None:
    shutil.rmtree(maps_dir, ignore_errors=True)
    maps_dir.mkdir(parents=True, exist_ok=True)


def _connect_nearest_neighbors(gdf1, gdf2):
    # Use spatial index for nearest neighbor search
    idx_1, idx_2 = gdf2.sindex.nearest(gdf1.geometry)

    # Create lines connecting centroids
    edges = [
        LineString([c1, c2])
        for c1, c2 in zip(gdf1.iloc[idx_1].centroid, gdf2.iloc[idx_2].centroid)
    ]

    return gpd.GeoDataFrame(geometry=edges, crs=gdf1.crs)
