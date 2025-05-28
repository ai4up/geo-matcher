import atexit
import os
import re
import shutil
import webbrowser
from pathlib import Path
from typing import Dict, Optional, List

from flask import Blueprint, Flask, Response, jsonify, current_app, render_template, request, send_file, session
from flask_executor import Executor
from pandas import DataFrame
import pandas as pd
import waitress

from geo_matcher.state import State as S
from geo_matcher import map

bp = Blueprint("matching", __name__)
executor = Executor()

def create_app(data_path: str, results_path: str, annotation_redundancy: int, consensus_margin: int) -> Flask:
    """
    Create and configure the Flask app.
    """
    app = Flask(__name__)
    app.secret_key = os.getenv("SECRET_KEY") or "dev-mode"
    app.maps_dir = Path(app.static_folder) / "maps"
    app.url_map.strict_slashes = False
    app.register_blueprint(bp)
    executor.init_app(app)

    _ensure_empty_dir(app.maps_dir)

    S.init(data_path, results_path, annotation_redundancy, consensus_margin)

    return app


def start_locally(*args, **kwargs) -> None:
    """
    Start the Flask app locally in the browser. Ensures that results are persisted on exit.
    """
    app = create_app(*args, **kwargs)
    atexit.register(S.store_results)
    webbrowser.open("http://127.0.0.1:5001/")
    waitress.serve(app, host="127.0.0.1", port=5001)


@bp.before_request
def ensure_session_defaults() -> None:
    """
    Sets label mode and username if not already set.
    """
    session.setdefault("label_mode", "unlabeled")
    session.setdefault("username", "unknown")


@bp.route("/")
def home_pairwise() -> Response:
    """
    Display the home page for pair-wise labeling including a tutorial and a username prompt.
    """
    fp = current_app.maps_dir / "candidate_demo.html"
    map.create_tutorial_html(fp)
    return render_template("index.html"), 200


@bp.route("/batch")
def home_batch() -> Response:
    """
    Display the home page for neighborhood-wise labeling including a tutorial and a username prompt.
    """
    fp = current_app.maps_dir / "neighborhood_demo.html"
    map.create_neighborhood_tutorial_html(fp)
    return render_template("neighborhood_index.html"), 200


@bp.route("/set-username", methods=["POST"])
def set_username():
    """
    Store the username and cross-validation mode in the session.

    The username must be alphanumeric (including underscores and hyphens).
    """
    username = request.form.get("username")
    label_mode = request.form.get("labelmode")

    if not username:
        return "Missing username", 400

    if not re.match(r"^[a-zA-Z0-9_-]+$", username):
        return "Invalid characters in username", 400

    if label_mode not in ["all", "unlabeled", "cross-validate"]:
        return "Invalid labeling mode", 400

    session["username"] = username
    session["label_mode"] = label_mode

    return "", 200


@bp.route("/show-pair")
@bp.route("/show-pair/<id_existing>/<id_new>")
def show_candidate_pair(id_existing: str = None, id_new: str = None) -> Response:
    """
    Display a map of a candidate building pair for manual labeling.
    """
    username = session.get("username")
    mode = session.get("label_mode")

    if id_existing is None or id_new is None:
        id_existing, id_new = S.get_next_pair(mode, username)

    if id_existing is None:
        S.store_results()
        return render_template("success.html")

    if not S.valid_pair(id_existing, id_new):
        return f"Candidate pair ({id_existing}, {id_new}) not found", 404

    name = _unq_name(id_existing, id_new)
    fp = current_app.maps_dir / f"candidate_{name}.html"
    map.create_candidate_pair_html(id_existing, id_new, fp)

    subsequent_pair = S.get_pair_after_next(mode, username)
    if subsequent_pair[0]:
        current_app.logger.debug(f"Pre-generating HTML map for candidate pair {subsequent_pair}")
        next_name = _unq_name(*subsequent_pair)
        next_fp = current_app.maps_dir / f"candidate_{next_name}.html"
        executor.submit(map.create_candidate_pair_html, *subsequent_pair, next_fp)

    return render_template(
        "show_candidate_pair.html",
        id_existing=id_existing,
        id_new=id_new,
        map_file=fp.name,
        user_stats=S.get_top_labelers(),
    ), 200


@bp.route("/show-neighborhood")
@bp.route("/show-neighborhood/<id>")
def show_neighborhood(id: Optional[str] = None) -> Response:
    """
    Display a map of all candidate building pairs in a neighborhood for bulk labeling.
    """
    username = session.get("username")
    mode = session.get("label_mode")

    if id is None:
        id = S.get_next_neighborhood(mode, username)

    if id is None:
        S.store_results()
        return render_template("success.html")

    if id not in S.get_all_neighborhoods():
        return "Neighborhood not found", 404

    fp = current_app.maps_dir / f"neighborhood_{id}.html"
    map.create_neighborhood_html(id, fp)

    if subsequent_id := S.get_neighborhood_after_next(mode, username):
        current_app.logger.debug(f"Pre-generating HTML map for neighborhood {subsequent_id}")
        next_fp = current_app.maps_dir / f"neighborhood_{subsequent_id}.html"
        executor.submit(map.create_neighborhood_html, subsequent_id, next_fp)

    return render_template(
        "show_neighborhood.html",
        id=id,
        map_file=fp.name,
        user_stats=S.get_top_labelers(),
    ), 200


@bp.route("/store-label", methods=["POST"])
def store_label() -> Response:
    """
    Store the labeling decision for a candidate pair and return the next one.
    """
    data = request.json

    username = session.get("username")
    mode = session.get("label_mode")
    id_existing = data.get("id_existing")
    id_new = data.get("id_new")
    match = data.get("match")

    S.add_result(id_existing, id_new, match, username)
    next_pair = S.get_next_pair(mode, username)

    return jsonify({"status": "ok", "next_existing_id": next_pair[0] or "", "next_new_id": next_pair[1] or ""}), 200


@bp.route("/store-neighborhood", methods=["POST"])
def store_neighborhood() -> Response:
    """
    Stores the labeling decisions for all candidate pairs in a neighborhood and returns the next neighborhood ID.

    Accepts label adjustments (added and removed matches) and updates candidate pairs accordingly.
    """
    data = request.json

    username = session.get("username")
    mode = session.get("label_mode")

    id = data.get("id")
    pairs = data.get("pairs")
    added = data.get("added", [])
    removed = data.get("removed", [])

    current_app.logger.info(f"Adding {len(added)} matches, removing {len(removed)} in neighborhood {id}.")

    results = DataFrame(pairs, columns=["id_existing", "id_new", "match"])
    results = _update_removed_matches(results, removed)
    results = _update_added_matches(results, added)

    results["username"] = username
    results["neighborhood"] = id
    results["match"] = results["match"].replace({True: "yes", False: "no"})

    S.add_bulk_results(results)
    next_id = S.get_next_neighborhood(mode, username)

    return jsonify({"status": "ok", "next_id": next_id or ""}), 200


@bp.route("/download-results")
def download_results() -> Response:
    """
    Download the results of the labeling process as a CSV file.
    """
    path = S.results_path.with_name("labeled-pairs.csv").absolute()
    S.store_aggregated_results(path)

    return send_file(path, as_attachment=True)


def _update_added_matches(candidate_pairs: DataFrame, added: List[Dict]) -> DataFrame:
    return _update_matches(candidate_pairs, added, label="yes", add_if_missing=True)


def _update_removed_matches(candidate_pairs: DataFrame, removed: List[Dict]) -> DataFrame:
    return _update_matches(candidate_pairs, removed, label="no", add_if_missing=False)


def _update_matches(candidate_pairs: DataFrame, matches: List[Dict], label: str, add_if_missing: bool) -> DataFrame:
    new_candidate_pairs = []
    for match in matches:
        id_existing = match.get("id_existing")
        id_new = match.get("id_new")
        if not id_existing or not id_new:
            continue

        mask = (candidate_pairs["id_existing"] == id_existing) & (candidate_pairs["id_new"] == id_new)
        if mask.any():
            candidate_pairs.loc[mask, "match"] = label
        elif add_if_missing:
            new_candidate_pairs.append({
                "id_existing": id_existing,
                "id_new": id_new,
                "match": label
            })

    candidate_pairs = pd.concat([candidate_pairs, DataFrame(new_candidate_pairs)], ignore_index=True)

    return candidate_pairs


def _unq_name(id_existing: str, id_new: str) -> str:
    return f"{id_existing}--{id_new}"


def _ensure_empty_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
