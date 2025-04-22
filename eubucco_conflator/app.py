import atexit
import os
import shutil
import webbrowser
from pathlib import Path
from typing import Optional

from flask import Blueprint, Flask, Response, jsonify, current_app, render_template, request, session
from flask_executor import Executor
from geopandas import GeoDataFrame
from pandas import DataFrame
from shapely.geometry import Point, LineString
import geopandas as gpd
import pandas as pd
import waitress

from eubucco_conflator.state import State as S
from eubucco_conflator import spatial, map

bp = Blueprint('matching', __name__)
executor = Executor()

def create_app(data_path: str, results_path: str) -> Flask:
    app = Flask(__name__)
    app.secret_key = os.getenv('SECRET_KEY') or 'dev-mode'
    app.maps_dir = Path(app.static_folder) / "maps"
    app.url_map.strict_slashes = False
    app.register_blueprint(bp)
    executor.init_app(app)

    _ensure_empty_dir(app.maps_dir)

    S.init(data_path, results_path)

    return app


def start_locally(*args, **kwargs) -> None:
    app = create_app(*args, **kwargs)
    atexit.register(S.store_results)
    webbrowser.open("http://127.0.0.1:5001/")
    waitress.serve(app, host="127.0.0.1", port=5001)


@bp.route("/")
def home() -> Response:
    fp = current_app.maps_dir / "candidate_demo.html"
    map.create_tutorial_html(fp)
    return render_template("index.html"), 200


@bp.route('/set-username', methods=['POST'])
def set_username():
    username = request.form.get('username')
    cross_validate = request.form.get('cross_validate') == 'true'

    if username:
        session['username'] = username
        session['cross_validate'] = cross_validate
        return '', 200

    return 'Missing username', 400


@bp.route("/show-pair")
@bp.route("/show-pair/<id_existing>/<id_new>")
def show_candidate_pair(id_existing: str = None, id_new: str = None) -> Response:
    cv = session.get('cross_validate', False)

    if id_existing is None or id_new is None:
        id_existing, id_new = S.current_pair(cv)

    if id_existing is None:
        S.store_results()
        return f"All buildings labeled! Results stored in {S.results_path}", 200

    if not S.valid_pair(id_existing, id_new):
        return "Candidate pairs not found", 404

    name = _unq_name(id_existing, id_new)
    fp = current_app.maps_dir / f"candidate_{name}.html"
    map.create_candidate_pair_html(id_existing, id_new, fp)

    next_pair = S.next_pair(cv)
    if next_pair[0]:
        current_app.logger.debug(f"Pre-generating HTML map for candidate pair {next_pair}")
        next_name = _unq_name(*next_pair)
        next_fp = current_app.maps_dir / f"candidate_{next_name}.html"
        executor.submit(map.create_candidate_pair_html, *next_pair, next_fp)

    return render_template(
        "show_candidate_pair.html", id_existing=id_existing, id_new=id_new, map_file=fp.name
    ), 200


@bp.route("/show-neighborhood")
@bp.route("/show-neighborhood/<id>")
def show_neighborhood(id: Optional[str] = None) -> Response:
    cv = session.get('cross_validate', False)

    if id is None:
        id = S.current_neighborhood(cv)

    if id is None:
        S.store_results()
        return f"All buildings labeled! Results stored in {S.results_path}", 200

    if id not in S.neighborhoods():
        return "Neighborhood not found", 404

    fp = current_app.maps_dir / f"neighborhood_{id}.html"
    map.create_neighborhood_html(id, fp)

    if next_id := S.next_neighborhood(cv):
        current_app.logger.debug(f"Pre-generating HTML map for neighborhood {next_id}")
        next_fp = current_app.maps_dir / f"neighborhood_{next_id}.html"
        executor.submit(map.create_neighborhood_html, next_id, next_fp)

    return render_template("show_neighborhood.html", id=id, map_file=fp.name), 200


@bp.route("/store-label", methods=["POST"])
def store_label() -> Response:
    data = request.json

    username = session.get('username', 'unknown')
    cv = session.get('cross_validate', False)
    id_existing = data.get("id_existing")
    id_new = data.get("id_new")
    match = data.get("match")

    next_pair = S.next_pair(cv)
    S.add_result(id_existing, id_new, match, username)

    return jsonify({"status": "ok", "next_existing_id": next_pair[0] or "", "next_new_id": next_pair[1] or ""}), 200


@bp.route("/store-neighborhood", methods=["POST"])
def store_neighborhood() -> Response:
    data = request.json

    username = session.get('username', 'unknown')
    cv = session.get('cross_validate', False)
    id = data.get("id")
    added = data.get("added")
    removed = data.get("removed")

    added = gpd.GeoDataFrame.from_features(added, columns=["geometry"], crs="EPSG:4326")
    removed = gpd.GeoDataFrame.from_features(removed, columns=["geometry"], crs="EPSG:4326")

    current_app.logger.info(f"Adding {len(added)} additional matches in neighborhood {id}.")
    current_app.logger.info(f"Removing {len(removed)} matches in neighborhood {id}.")

    results = S.get_candidate_pairs(id)
    results["username"] = username
    results["neighborhood"] = id
    results["match"] = results["match"].replace({True: "yes", False: "no"})
    results = _set_to_no_match(results, removed)
    results = _add_to_candidate_pairs(results, added)

    next_id = S.next_neighborhood(cv)
    S.add_bulk_results(results)

    return jsonify({"status": "ok", "next_id": next_id or ""}), 200


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
        current_app.logger.warning(f"Drawn line ({line}) does not connect two buildings.")
    elif len(matches) > 1:
        current_app.logger.warning("End of drawn line is located inside multiple buildings. Added match is ambiguous.")
    else:
        return matches.index[0]


def _unq_name(id_existing: str, id_new: str) -> str:
    return f"{id_existing}--{id_new}"


def _ensure_empty_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
