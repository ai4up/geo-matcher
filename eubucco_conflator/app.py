import atexit
import shutil
import webbrowser
from pathlib import Path
from typing import Optional

from flask import Flask, Response, jsonify, render_template, request
from flask_executor import Executor
from folium.plugins import Draw
from geopandas import GeoDataFrame
from pandas import DataFrame
from shapely.geometry import Point, LineString
from waitress import serve
import folium
import geopandas as gpd
import pandas as pd

from eubucco_conflator.state import RESULTS_FILE
from eubucco_conflator.state import State as S
from eubucco_conflator import spatial

app = Flask(__name__)
app.url_map.strict_slashes = False
executor = Executor(app)
maps_dir = Path(app.static_folder) / "maps"


def start() -> None:
    _clean_maps_dir()
    atexit.register(S.store_results)

    webbrowser.open("http://127.0.0.1:5001/")
    serve(app, host="127.0.0.1", port=5001)


@app.route("/")
def home() -> Response:
    _create_tutorial_html()
    return render_template("index.html"), 200


@app.route("/show-pair")
@app.route("/show-pair/<id_existing>/<id_new>")
def show_candidate(id_existing: str = None, id_new: str = None) -> Response:
    if id_existing is None or id_new is None:
        id_existing, id_new = S.current_pair()

    if id_existing is None:
        S.store_results()
        return f"All buildings labeled! Results stored in {RESULTS_FILE}", 200

    if not S.valid_pair(id_existing, id_new):
        return "Candidate pairs not found", 404

    filename = _create_candidate_pair_html(id_existing, id_new)

    if next_pair := S.next_pair():
        app.logger.debug(f"Pre-generating HTML map for candidate pair {next_pair}")
        executor.submit(_create_candidate_pair_html, *next_pair)

    return render_template(
        "show_candidate.html", id_existing=id_existing, id_new=id_new, map_file=filename
    ), 200

@app.route("/show-neighborhood")
@app.route("/show-neighborhood/<id>")
def show_neighborhood(id: Optional[str] = None) -> Response:
    if id is None:
        id = S.current_neighborhood()

    if id is None:
        S.store_results()
        return f"All buildings labeled! Results stored in {RESULTS_FILE}", 200

    if id not in S.data.candidate_pairs.index:
        return "Neighborhood not found", 404

    filename = _create_neighborhood_html(id)

    if next_id := S.next_neighborhood():
        app.logger.debug(f"Pre-generating HTML map for neighborhood {next_id}")
        executor.submit(_create_neighborhood_html, next_id)

    return render_template("show_neighborhood.html", id=id, map_file=filename), 200


@app.route("/store-label", methods=["POST"])
def store_label() -> Response:
    data = request.json

    id_existing = data.get("id_existing")
    id_new = data.get("id_new")
    match = data.get("match")
    S.add_result(id_existing, id_new, match)

    return jsonify({"status": "ok", "message": "Label stored"}), 200


@app.route("/store-neighborhood", methods=["POST"])
def store_neighborhood() -> Response:
    data = request.json
    id = data.get("id")
    added = data.get("added")
    removed = data.get("removed")

    added = gpd.GeoDataFrame.from_features(added, columns=["geometry"], crs="EPSG:4326")
    removed = gpd.GeoDataFrame.from_features(removed, columns=["geometry"], crs="EPSG:4326")

    app.logger.info(f"Adding {len(added)} additional matches in neighborhood {id}.")
    app.logger.info(f"Removing {len(removed)} matches in neighborhood {id}.")

    results = S.get_candidate_pairs(id).reset_index(names="neighborhood")
    results["match"] = results["match"].replace({True: "yes", False: "no"})
    results = _set_to_no_match(results, removed)
    results = _add_to_candidate_pairs(results, added)

    S.add_bulk_results(results)

    return jsonify({"status": "ok", "message": "Neighborhood labels stored"}), 200


def _create_tutorial_html() -> None:
    # Load demo data
    demo_data_path = Path(__file__).parent / "data" / "tutorial-candidate.parquet"
    gdf = gpd.read_parquet(demo_data_path)
    candidate_existing = gdf.loc["A_candidate"].geometry
    candidate_new = gdf.loc["B_candidate"].geometry
    existing_buildings = gdf.loc["A"]
    new_buildings = gdf.loc["B"]

    c = candidate_new.centroid
    lat, lon = spatial.to_lat_lon(c.x, c.y, existing_buildings.crs)

    # Initialize map and add demo buildings
    m = _initialize_map(lat, lon, 20)
    _create_existing_buildings_layer(existing_buildings).add_to(m)
    _create_new_buildings_layer(new_buildings).add_to(m)
    _create_candidate_pair_layer({"geometry_existing": candidate_existing, "geometry_new": candidate_new}).add_to(m)
    _add_tutorial_markers(lat, lon).add_to(m)

    m.save(maps_dir / "candidate_demo.html")


def _create_candidate_pair_html(id_existing: str, id_new: str) -> str:
    id = _unq_id(id_existing, id_new)
    path = maps_dir / f"candidate_{id}.html"

    if path.is_file():
        return path.name

    candidate_pair = S.get_candidate_pair(id_existing, id_new)

    c = candidate_pair["geometry_new"].centroid
    existing_buildings = S.get_existing_buildings_at(c)
    new_buildings = S.get_new_building_at(c)

    lat, lon = spatial.to_lat_lon(c.x, c.y, existing_buildings.crs)
    m = _initialize_map(lat, lon, 20)

    _create_existing_buildings_layer(existing_buildings).add_to(m)
    _create_new_buildings_layer(new_buildings).add_to(m)
    _create_candidate_pair_layer(candidate_pair).add_to(m)
    _add_legend(m, candidates_highlighted=True)

    folium.LayerControl(collapsed=True).add_to(m)

    m.save(path)

    return path.name


def _create_neighborhood_html(id: str) -> str:
    path = maps_dir / f"neighborhood_{id}.html"

    if path.is_file():
        return path.name

    existing_buildings = S.get_existing_buildings(id)
    new_buildings = S.get_new_buildings(id)
    candidate_pairs = S.get_candidate_pairs(id, geometry=True)

    lat, lon = spatial.center_lat_lon(candidate_pairs["geometry_new"])
    m = _initialize_map(lat, lon, 19)

    _create_existing_buildings_layer(existing_buildings).add_to(m)
    _create_new_buildings_layer(new_buildings).add_to(m)
    _add_matching_layer(m, candidate_pairs)
    _add_legend(m)

    folium.LayerControl(collapsed=True).add_to(m)

    m.save(path)

    return path.name


def _initialize_map(lat: float, lon: float, zoom_level: int) -> folium.Map:
    m = folium.Map(location=[lat, lon], zoom_start=zoom_level, tiles=None)
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


def _add_tutorial_markers(lat: float, lon: float) -> folium.Marker:
    return folium.Marker(
        location=[lat, lon],
        tooltip="Building pair to be labeled as 'Match' / 'No Match' / 'Unsure'.",
        icon=folium.Icon(color="lightred", icon="info-sign"),
    )


def _create_existing_buildings_layer(gdf: GeoDataFrame) -> folium.FeatureGroup:
    existing_buildings = folium.FeatureGroup(name="Existing Buildings")
    folium.GeoJson(
        gdf, style_function=lambda _: {"color": "skyblue", "fillOpacity": 0.5}
    ).add_to(existing_buildings)

    return existing_buildings


def _create_new_buildings_layer(gdf: GeoDataFrame) -> folium.FeatureGroup:
    new_buildings = folium.FeatureGroup(name="New Buildings")
    folium.GeoJson(
        gdf, style_function=lambda _: {"color": "coral", "fillOpacity": 0.2}
    ).add_to(new_buildings)

    return new_buildings


def _create_candidate_pair_layer(candidate_pair: GeoDataFrame) -> folium.FeatureGroup:
    candidates = folium.FeatureGroup(name="Candidate Pair")
    gdf1 = GeoDataFrame(geometry=[candidate_pair["geometry_existing"]], crs=3035)
    gdf2 = GeoDataFrame(geometry=[candidate_pair["geometry_new"]], crs=3035)
    folium.GeoJson(gdf1, style_function=lambda _: {"color": "steelblue", "weight": 5, "fillColor": "skyblue", "fillOpacity": 0.5}).add_to(candidates)
    folium.GeoJson(gdf2, style_function=lambda _: {"color": "orangered", "weight": 5, "fillColor": "coral", "fillOpacity": 0.2}).add_to(candidates)

    return candidates


def _add_matching_layer(m: folium.Map, candidate_pairs: GeoDataFrame) -> None:
    matches = candidate_pairs[candidate_pairs["match"]]
    matching_edges = spatial.connect_with_lines(matches["geometry_existing"], matches["geometry_new"])
    _add_matching_edges(matching_edges, m)
    _add_drawing_layer(m)


def _add_matching_edges(gdf_initial_matches: GeoDataFrame, m: folium.Map) -> None:
    map_name = m.get_name()
    m.get_root().html.add_child(folium.Element(f"""
    <script>
    document.addEventListener("DOMContentLoaded", function () {{
        window.removedMatches = [];

        const map = window.{map_name};
        const initialMatches = {gdf_initial_matches.to_json(to_wgs84=True)};
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
    ))


def _add_drawing_layer(m: folium.Map) -> None:
    Draw(
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
    ).add_to(m)
    _store_added_edges_js(m)


def _store_added_edges_js(m: folium.Map) -> None:
    m.get_root().html.add_child(folium.Element(f"""
    <script>
        document.addEventListener("DOMContentLoaded", function () {{
            const map = window.{m.get_name()};
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
    ))


def _add_legend(m: folium.Map, candidates_highlighted=False) -> None:
    candidates_entry = """
    <div>
        <span style="display: inline-block; width: 26px; height: 18px; position: relative; vertical-align: middle;">
            <i style="background: rgba(255, 127, 80, 0.2); width: 18px; height: 18px; display: inline-block;
                border: 3px solid orangered; position: absolute; top: 0; left: 0; z-index: 2;"></i>
            <i style="background: rgba(135, 206, 235, 0.5); width: 18px; height: 18px; display: inline-block;\
                border: 3px solid steelblue; position: absolute; top: 0; left: 6px; z-index: 1;"></i>
        </span>
        Candidate Pair
    </div>
    """

    matching_edges_entry = """
    <div>
        <i style="border-top: 3px solid green; width: 18px;
            display: inline-block; margin-right: 6px; vertical-align: middle;"></i>
        Matching Relationships
    </div>
    """

    m.get_root().html.add_child(folium.Element(f"""
    <div style="position: fixed; bottom: 30px; left: 30px; background: rgba(255, 255, 255, 0.8);
            border: 1px solid lightgrey; z-index: 9999; font-size: 14px; padding: 10px; line-height: 18px;">
        <b style="display: block; margin-bottom: 6px;">Building Layers</b>

        <div style="margin-bottom: 6px;">
            <i style="background: rgba(255, 127, 80, 0.2); width: 18px; height: 18px; display: inline-block;
                border: 2px solid coral; margin-right: 6px; vertical-align: middle;"></i>
            New Buildings
        </div>

        <div style="margin-bottom: 6px;">
            <i style="background: rgba(135, 206, 235, 0.5); width: 18px; height: 18px; display: inline-block;
                border: 2px solid skyblue; margin-right: 6px; vertical-align: middle;"></i>
            Existing Buildings
        </div>

        {candidates_entry if candidates_highlighted else matching_edges_entry}
    </div>
    """
    ))


def _unq_id(id_existing: str, id_new: str) -> str:
    return f"{id_existing}--{id_new}"


def _clean_maps_dir() -> None:
    shutil.rmtree(maps_dir, ignore_errors=True)
    maps_dir.mkdir(parents=True, exist_ok=True)


def _add_to_candidate_pairs(candidate_pairs: DataFrame, added: GeoDataFrame) -> DataFrame:
    """
    Add geometries to candidate pairs.
    """
    if added.empty:
        return candidate_pairs

    neighborhood = candidate_pairs["neighborhood"].iloc[0]
    existing = S.get_existing_buildings(neighborhood)
    new = S.get_new_buildings(neighborhood)

    new_candidate_pairs = []
    for line in added.to_crs(existing.crs).geometry:
        id_existing = _find_matching_building(line, existing)
        id_new = _find_matching_building(line, new)
        if not id_existing or not id_new:
            continue

        mask = (candidate_pairs["id_existing"] == id_existing) & (candidate_pairs["id_new"] == id_new)
        if mask.any():
            candidate_pairs.loc[mask, "match"] = "yes"
        else:
            new_candidate_pairs.append(
                {
                    "id_existing": id_existing,
                    "id_new": id_new,
                    "neighborhood": neighborhood,
                    "match": "yes",
                }
            )

    candidate_pairs = pd.concat([candidate_pairs, DataFrame(new_candidate_pairs)], ignore_index=True)

    return candidate_pairs


def _set_to_no_match(candidate_pairs: DataFrame, removed: GeoDataFrame) -> DataFrame:
    """
    Remove geometries from candidate pairs.
    """
    if removed.empty:
        return candidate_pairs

    existing = S.data.dataset_a.loc[candidate_pairs["id_existing"]]
    new = S.data.dataset_b.loc[candidate_pairs["id_new"]]

    for line in removed.to_crs(existing.crs).geometry:
        idx = _find_matching_building_pair(line, existing, new)
        if idx:
            col_idx = candidate_pairs.columns.get_loc("match")
            candidate_pairs.iloc[idx, col_idx] = "no"

    return candidate_pairs


def _find_matching_building_pair(line: LineString, gdf1: GeoDataFrame, gdf2: GeoDataFrame) -> Optional[int]:
    for i, (g1, g2) in enumerate(zip(gdf1.geometry, gdf2.geometry)):
        if spatial.line_connects_two_polygons(line, g1, g2):
            return i

    return None


def _find_matching_building(line: LineString, gdf: GeoDataFrame) -> Optional[str]:
    start = Point(line.coords[0])
    end = Point(line.coords[-1])
    matches = gdf[gdf.contains(start) | gdf.contains(end)]

    if matches.empty:
        app.logger.warning(f"Drawn line ({line}) does not connect two buildings.")
    elif len(matches) > 1:
        app.logger.warning("End of drawn line is located inside multiple buildings. Added match is ambiguous.")
    else:
        return matches.index[0]
