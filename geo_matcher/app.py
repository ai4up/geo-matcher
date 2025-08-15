import os
import re
import webbrowser
from pathlib import Path
from typing import Dict, Optional, List

from flask import Blueprint, Flask, Response, current_app, g, jsonify, redirect, render_template, request, send_file, session, url_for
from flask_executor import Executor
from pandas import DataFrame
import pandas as pd
import waitress
from werkzeug.routing import BaseConverter


from geo_matcher.state import State
from geo_matcher.state_handler import StateHandler
from geo_matcher.utils import force_empty_dir
from geo_matcher import map

bp = Blueprint("labeling", __name__)
executor = Executor()

class FeatureTypeConverter(BaseConverter):
    regex = r"(buildings|places)"

class MissingDataset(Exception):
    """Raised when no dataset is selected in the session."""
    pass


def create_app(data_path: str, annotation_redundancy: int, consensus_margin: int, feature_type: str = None) -> Flask:
    """
    Create and configure the Flask app.
    """
    app = Flask(__name__)
    app.secret_key = os.getenv("SECRET_KEY") or "dev-mode"
    app.maps_dir = Path(app.static_folder) / "maps"
    app.url_map.strict_slashes = False
    app.url_map.converters["ft"] = FeatureTypeConverter
    app.config["DEFAULT_FEATURE_TYPE"] = feature_type

    force_empty_dir(app.maps_dir)

    app.register_blueprint(bp)
    executor.init_app(app)

    app.state_handler = StateHandler(data_path, annotation_redundancy, consensus_margin)

    return app


def start_locally(*args, **kwargs) -> None:
    """
    Start the Flask app locally in the browser. Ensures that results are persisted on exit.
    """
    app = create_app(*args, **kwargs)
    webbrowser.open("http://127.0.0.1:5001/")
    waitress.serve(app, host="127.0.0.1", port=5001)


@bp.before_request
def ensure_session_defaults() -> None:
    """
    Sets labeling mode and username if not already set.
    """
    session.setdefault("label_mode", "unlabeled")
    session.setdefault("username", "unknown")


@bp.app_errorhandler(MissingDataset)
def handle_missing_dataset(error):
    current_app.logger.info("No dataset selected. Redirecting to landing page.")
    return redirect("/")


@bp.app_errorhandler(404)
def page_not_found(e):
    return render_template("error.html", message="Page not found."), 404


@bp.app_errorhandler(Exception)
def handle_exception(e):
    current_app.logger.error("Unhandled Exception", exc_info=e)

    return render_template("error.html"), 500


@bp.url_value_preprocessor
def pull_ft(endpoint, values):
    # Extract /<ft>/ from the URL and stash on g
    if values is None:
        return
    g.ft = values.pop("ft", None)


@bp.url_defaults
def add_ft(endpoint, values):
    # Auto-inject feature type into url_for when already in a scoped request
    if "ft" not in values and getattr(g, "ft", None):
        values["ft"] = g.ft


@bp.app_context_processor
def inject_helpers():
    # Helper func for templates
    def feature_url(endpoint, **kwargs):
        return url_for(endpoint, **kwargs)

    return dict(feature_url=feature_url)


@bp.get("/")
def root():
    ft = current_app.config.get("DEFAULT_FEATURE_TYPE")
    if ft in ["buildings", "places"]:
        return redirect(url_for("labeling.home_pair", ft=ft), code=302)

    return render_template("index.html"), 200


@bp.get("/<ft:ft>/")
def home_pair():
    datasets = current_app.state_handler.datasets
    if g.ft == "buildings":
        fp = current_app.maps_dir / "buildings_demo.html"
        map.create_buildings_pair_tutorial_html(fp)
        return render_template("tutorial_buildings_pair.html", map_file=fp.name, datasets=datasets), 200
    else:  # places
        fp = current_app.maps_dir / "places_demo.html"
        map.create_places_pair_tutorial_html(fp)
        return render_template("tutorial_places_pair.html", map_file=fp.name, datasets=datasets), 200


@bp.get("/<ft:ft>/batch")
def home_batch() -> Response:
    """
    Display the home page for neighborhood-wise labeling including a tutorial and a username prompt.
    """
    if g.ft != "buildings":
        return render_template("info.html", message=f"Not (yet) supported for {g.ft}."), 404

    fp = current_app.maps_dir / "neighborhood_demo.html"
    datasets = current_app.state_handler.datasets
    map.create_buildings_neighborhood_tutorial_html(fp)

    return render_template("tutorial_buildings_batch.html", map_file=fp.name, datasets=datasets), 200


@bp.post("/start-session")
def start_session():
    username = request.form.get("username", "").strip()
    label_mode = request.form.get("labelmode")
    dataset = request.form.get("dataset")

    if not username or not re.match(r"^[a-zA-Z0-9_-]+$", username):
        return "Invalid username", 400

    if label_mode not in ["all", "unlabeled", "cross-validate"]:
        return "Invalid labeling mode", 400

    if dataset not in current_app.state_handler.datasets:
        return "Invalid dataset", 400

    session["username"] = username
    session["label_mode"] = label_mode
    session["dataset"] = dataset

    current_app.logger.debug(f"Session started for user {username} with mode {label_mode} on dataset {dataset}.")

    return "", 200


@bp.get("/<ft:ft>/show-pair")
@bp.get("/<ft:ft>/show-pair/<id_existing>/<id_new>")
def show_pair(id_existing: str = None, id_new: str = None) -> Response:
    """
    Display a map of a candidate building pair for manual labeling.
    """
    S = _get_state()
    username = session.get("username")
    mode = session.get("label_mode")
    dataset = session.get("dataset")

    if id_existing is None or id_new is None:
        id_existing, id_new = S.get_next_pair(mode, username)

    if id_existing is None:
        S.store_results()
        return render_template("success.html")

    if not S.valid_pair(id_existing, id_new):
        return render_template("error.html", message=f"Candidate pair ({id_existing}, {id_new}) not found"), 404

    if g.ft == "buildings":
        if S.data.feature_type != "buildings":
            return render_template("error.html", message="Dataset does not conform to building schema"), 404

        map_creation_func = map.create_buildings_pair_html
        attr = None
    elif g.ft == "places":
        map_creation_func = map.create_places_pair_html
        attr = S.get_candidate_attr(id_existing, id_new)
    else:
        return render_template("info.html", message=f"Not (yet) supported for {g.ft}."), 404

    name = _unq_name(dataset, id_existing, id_new)
    fp = current_app.maps_dir / f"{g.ft}_pair_{name}.html"
    map_creation_func(S, id_existing, id_new, fp)

    subsequent_pair = S.get_pair_after_next(mode, username)
    if subsequent_pair[0]:
        current_app.logger.debug(f"Pre-generating HTML map for candidate pair {subsequent_pair}")
        next_name = _unq_name(dataset, *subsequent_pair)
        next_fp = current_app.maps_dir / f"{g.ft}_pair_{next_name}.html"
        executor.submit(map_creation_func, S, *subsequent_pair, next_fp)

    return render_template(
        "labeling_show_pair.html",
        id_existing=id_existing,
        id_new=id_new,
        attr=attr,
        map_file=fp.name,
        user_stats=S.get_top_labelers(),
        n_left=S.get_n_left(),
    ), 200


@bp.get("/<ft:ft>/show-neighborhood")
@bp.get("/<ft:ft>/show-neighborhood/<id>")
def show_neighborhood(id: Optional[str] = None) -> Response:
    """
    Display a map of all candidate building pairs in a neighborhood for bulk labeling.
    """
    S = _get_state()
    username = session.get("username")
    mode = session.get("label_mode")
    dataset = session.get("dataset")

    if id is None:
        id = S.get_next_neighborhood(mode, username)

    if id is None:
        S.store_results()
        return render_template("success.html")

    if id not in S.get_all_neighborhoods():
        return render_template("error.html", message="Neighborhood not found"), 404

    if g.ft != "buildings":
        return render_template("info.html", message=f"Not (yet) supported for {g.ft}."), 404

    if S.data.feature_type != "buildings":
        return render_template("error.html", message="Dataset does not conform to building schema"), 404

    fp = current_app.maps_dir / f"{g.ft}_neighborhood_{dataset}_{id}.html"
    map.create_buildings_neighborhood_html(S, id, fp)

    if subsequent_id := S.get_neighborhood_after_next(mode, username):
        current_app.logger.debug(f"Pre-generating HTML map for neighborhood {subsequent_id}")
        next_fp = current_app.maps_dir / f"{g.ft}_neighborhood_{dataset}_{subsequent_id}.html"
        executor.submit(map.create_buildings_neighborhood_html, S, subsequent_id, next_fp)

    return render_template(
        "labeling_show_neighborhood.html",
        id=id,
        map_file=fp.name,
        user_stats=S.get_top_labelers(),
        n_left=S.get_n_left(),
    ), 200


@bp.post("/<ft:ft>/store-label")
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

    S = _get_state()
    S.add_result(id_existing, id_new, match, username)
    next_pair = S.get_next_pair(mode, username)
    next_url = url_for("labeling.show_pair", id_existing=next_pair[0], id_new=next_pair[1])

    return jsonify(status="ok", next_url=next_url), 200


@bp.post("/<ft:ft>/store-neighborhood")
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

    S = _get_state()
    S.add_bulk_results(results)
    next_id = S.get_next_neighborhood(mode, username)
    next_url = url_for("labeling.show_neighborhood", id=next_id)

    return jsonify({"status": "ok", "next_url": next_url}), 200


@bp.route("/download-results")
def download_results() -> Response:
    """
    Download the results of the labeling process as a CSV file.
    """
    S = _get_state()
    path = S.results_path.with_name("labeled-pairs.csv").absolute()
    S.store_aggregated_results(path)

    return send_file(path, as_attachment=True)


def _get_state() -> State:
    dataset = session.get("dataset")
    if not dataset:
        raise MissingDataset("No dataset selected in the session.")

    return current_app.state_handler.get(dataset)


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


def _unq_name(dataset: str, id_existing: str, id_new: str) -> str:
    return f"{dataset}_{id_existing}_{id_new}"
