import os
import re
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from flask import Blueprint, Flask, Response, abort, current_app, g, jsonify, redirect, render_template, request, send_file, session, url_for
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

class ActionConverter(BaseConverter):
    regex = r"(label|label-neighborhood|review)"

class MissingDataset(Exception):
    """Raised when no dataset is selected in the session."""
    pass

@dataclass(frozen=True)
class TutorialSpec:
    make_map: callable
    template: str

SPECS: dict[tuple[str, str], TutorialSpec] = {
    ("buildings", "label"): TutorialSpec(map.create_buildings_pair_tutorial_html, "tutorial_buildings_pair.html"),
    ("buildings", "label-batch"): TutorialSpec(map.create_buildings_neighborhood_tutorial_html, "tutorial_buildings_batch.html"),
    ("buildings", "review"): TutorialSpec(map.create_buildings_pair_tutorial_html, "tutorial_review.html"),
    ("places", "label"): TutorialSpec(map.create_places_pair_tutorial_html, "tutorial_places_pair.html"),
    ("places", "review"): TutorialSpec(map.create_places_pair_tutorial_html, "tutorial_review.html"),
}


def create_app(data_path: str, annotation_redundancy: int, consensus_margin: int, feature_type: str = None) -> Flask:
    """
    Create and configure the Flask app.
    """
    app = Flask(__name__)
    app.secret_key = os.getenv("SECRET_KEY") or "dev-mode"
    app.maps_dir = Path(app.static_folder) / "maps"
    app.url_map.strict_slashes = False
    app.url_map.converters["ft"] = FeatureTypeConverter
    app.url_map.converters["action"] = ActionConverter
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
    session.setdefault("label_mode", "remaining")
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
def stash_url_args(endpoint, values):
    # Extract /<ft>/ and /<action>/ from the URL and stash on g
    if values is None:
        return
    g.ft = values.pop("ft", None)
    g.action = values.pop("action", None)


@bp.url_defaults
def inject_url_args(endpoint, values):
    # Auto-inject feature type and action into url_for when already in a scoped request
    if "ft" not in values and getattr(g, "ft", None):
        values["ft"] = g.ft
    if "action" not in values and getattr(g, "action", None):
        values["action"] = g.action


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
        return redirect(url_for("labeling.home_tutorial", ft=ft, action="label"), code=302)

    return render_template("index.html"), 200

@bp.get("/<ft:ft>")
def home_default() -> Response:
    return redirect(url_for("labeling.home_tutorial", action="label"), code=302)


@bp.get("/<ft:ft>/<action:action>/tutorial")
def home_tutorial() -> Response:
    datasets = current_app.state_handler.datasets
    spec = SPECS.get((g.ft, g.action))
    if not spec:
        current_app.logger.warning("Unsupported tutorial ft=%s action=%s", g.ft, g.action)
        return render_template("info.html", message=f"Not (yet) supported for {g.ft}."), 404

    fp = current_app.maps_dir / f"{g.ft}_{g.action}_demo.html"
    spec.make_map(fp)

    return render_template(spec.template, map_file=fp.name, datasets=datasets), 200


# @bp.get("/<ft:ft>/<action:action>/tutorial")
# def home_pair() -> Response:
#     datasets = current_app.state_handler.datasets
#     if g.ft == "buildings":
#         fp = current_app.maps_dir / "buildings_demo.html"
#         map.create_buildings_pair_tutorial_html(fp)
#     elif g.ft == "places":
#         fp = current_app.maps_dir / "places_demo.html"
#         map.create_places_pair_tutorial_html(fp)

#     if g.action == "review":
#         return render_template("tutorial_review.html", map_file=fp.name, datasets=datasets), 200
    
#     if g.ft == "buildings":
#         return render_template("tutorial_buildings_pair.html", map_file=fp.name, datasets=datasets), 200
    
#     return render_template("tutorial_places_pair.html", map_file=fp.name, datasets=datasets), 200


# @bp.get("/<ft:ft>/batch/tutorial")
# def home_batch() -> Response:
#     """
#     Display the home page for neighborhood-wise labeling including a tutorial and a username prompt.
#     """
#     if g.ft != "buildings":
#         return render_template("info.html", message=f"Not (yet) supported for {g.ft}."), 404

#     fp = current_app.maps_dir / "neighborhood_demo.html"
#     datasets = current_app.state_handler.datasets
#     map.create_buildings_neighborhood_tutorial_html(fp)

#     return render_template("tutorial_buildings_batch.html", map_file=fp.name, datasets=datasets), 200


@bp.post("/start-session")
def start_session():
    username = request.form.get("username", "").strip()
    label_mode = request.form.get("labelmode")
    dataset = request.form.get("dataset")

    if not username or not re.match(r"^[a-zA-Z0-9_-]+$", username):
        return "Invalid username", 400

    if label_mode not in ["all", "remaining", "cross-validate", "resolve-inconsistencies"]:
        return "Invalid labeling mode", 400

    if dataset not in current_app.state_handler.datasets:
        return "Invalid dataset", 400

    session["username"] = username
    session["label_mode"] = label_mode
    session["dataset"] = dataset

    current_app.logger.debug(f"Session started for user {username} with mode {label_mode} on dataset {dataset}.")

    return "", 200


@bp.get("/<ft:ft>/<action:action>")
@bp.get("/<ft:ft>/<action:action>/<int:idx>")
@bp.get("/<ft:ft>/<action:action>/<id_existing>/<id_new>")
@bp.get("/<ft:ft>/<action:action>/<neighborhood_id>")
def pair(idx: int = None, id_existing: str = None, id_new: str = None, neighborhood_id: str = None):
    if g.action == "label-neighborhood":
        return _render_neighborhood(neighborhood_id)

    if id_existing is None or id_new is None:
        return _resolve_pair(idx)

    return _render_pair(id_existing, id_new)


def _resolve_pair(idx: int = 0) -> Response:
    S = _get_state()
    username = session.get("username")
    mode = session.get("label_mode")

    if idx:
        eid, nid = S.get_pair_by_index(idx)
        if eid is None:
            return render_template("error.html", message="Candidate pair not found"), 404

    else:
        eid, nid = S.get_next_pair(mode, username)
        if eid is None:
            S.store_results()
            return render_template("success.html")

    return redirect(url_for("labeling.pair", id_existing=eid, id_new=nid), code=303)


def _render_pair(id_existing: str, id_new: str) -> Response:
    """
    Display a map of a candidate building pair for manual labeling.
    """
    S = _get_state()
    username = session.get("username")
    mode = session.get("label_mode")
    dataset = session.get("dataset")

    if g.ft != S.data.feature_type:
        return render_template("error.html", message=f"Dataset does not conform to {g.ft} schema"), 404

    if not S.valid_pair(id_existing, id_new):
        return render_template("error.html", message=f"Candidate pair ({id_existing}, {id_new}) not found"), 404

    if g.ft == "buildings":
        map_creation_func = map.create_buildings_pair_html
        attr = None
    elif g.ft == "places":
        map_creation_func = map.create_places_pair_html
        attr = S.get_candidate_attr(id_existing, id_new)
    else:
        return render_template("info.html", message=f"Not (yet) supported for {g.ft}."), 404

    subsequent_pair = S.get_next_pair(mode, username, id_existing, id_new)
    fp = _write_map_html(S, map_creation_func, dataset, id_existing, id_new)
    _write_map_html(S, map_creation_func, dataset, *subsequent_pair, run_async=True)

    template_context = dict(
        id_existing=id_existing,
        id_new=id_new,
        attr=attr,
        map_file=fp.name,
        user_stats=S.get_top_labelers(),
        n_left=S.get_n_left(mode, username),
    )

    if g.action == "review":
        label, counts, notes = S.get_labeling_details(id_existing, id_new)
        return render_template(
            "labeling_review_pair.html",
            **template_context,
            label_code=label,
            label_text=_summarize_labels(counts),
            label_notes=_format_notes(notes),
            next_id_existing=subsequent_pair[0],
            next_id_new=subsequent_pair[1],
        ), 200

    return render_template("labeling_show_pair.html", **template_context), 200


def _render_neighborhood(id: Optional[str] = None) -> Response:
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

    if g.ft != S.data.feature_type:
        return render_template("error.html", message=f"Dataset does not conform to {g.ft} schema"), 404

    subsequent_id = S.get_next_neighborhood(mode, username, i=1)
    fp = _write_map_html(S, map.create_buildings_neighborhood_html, dataset, id)
    _write_map_html(S, map.create_buildings_neighborhood_html, dataset, subsequent_id, run_async=True)

    return render_template(
        "labeling_show_neighborhood.html",
        id=id,
        map_file=fp.name,
        user_stats=S.get_top_labelers(),
        n_left=S.get_n_left(mode, username),
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
    notes = data.get("notes")

    S = _get_state()
    S.add_result(id_existing, id_new, match, username, notes)
    next_pair = S.get_next_pair(mode, username)
    next_url = url_for("labeling.pair", action="label", id_existing=next_pair[0], id_new=next_pair[1])

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
    next_url = url_for("labeling.label_neighborhood", id=next_id)

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


def _update_added_matches(candidate_pairs: DataFrame, added: list[dict]) -> DataFrame:
    return _update_matches(candidate_pairs, added, label="yes", add_if_missing=True)


def _update_removed_matches(candidate_pairs: DataFrame, removed: list[dict]) -> DataFrame:
    return _update_matches(candidate_pairs, removed, label="no", add_if_missing=False)


def _update_matches(candidate_pairs: DataFrame, matches: list[dict], label: str, add_if_missing: bool) -> DataFrame:
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


def _write_map_html(
    S,
    create_map_html: callable,
    dataset: str,
    *ids: str,
    run_async: bool = False,
) -> Optional[Path]:
    """
    Create HTML map file (sync or async) and return its path.
    """
    if not ids or ids[0] is None:
        return None

    key = "_".join(str(i) for i in ids if i is not None)
    fname = f"{g.ft}_{g.action}_{dataset}_{key}.html"
    fp = current_app.maps_dir / fname

    if run_async:
        executor.submit(create_map_html, S, *ids, fp)
    else:
        create_map_html(S, *ids, fp)

    return fp


def _summarize_labels(counts: dict) -> str:
    """
    Summarize the label counts into a human-readable string.
    """
    yes = counts.get("yes", 0)
    no = counts.get("no", 0)

    if yes > no:
        majority = "Match"
    elif no > yes:
        majority = "No Match"
    else:
        majority = "Unsure"

    return f"{majority} ({yes}:{no})"


def _format_notes(notes: dict) -> str:
    """
    Format labeling notes for display.
    """
    return "\n".join(f"{u}: {n}" for u, n in notes.items())
