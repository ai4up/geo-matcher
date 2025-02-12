import os

import pandas as pd
import geopandas as gpd

CANDIDATES_FILE = 'candidates.parquet'
RESULTS_FILE = 'results.csv'
_PROGRESS_FILE = 'data/labeling-progress.pickle'

class State:
    gdf = None
    candidates = None
    results = None

    @classmethod
    def init(cls, geodata_path):
        cls.gdf = _read_geodata(geodata_path)
        cls.results = _load_progress()
        cls.candidates = cls._determine_candidates()

    @classmethod
    def add_result(cls, id, label, existing_id):
        cls.results.append({"id": id, "duplicate": label, "existing_id": existing_id})
        cls._store_progress()

    @classmethod
    def store_results(cls):
        pd.DataFrame(cls.results).to_csv(RESULTS_FILE, index=False)

    @classmethod
    def _store_progress(cls):
        pd.DataFrame(cls.results).to_pickle(_PROGRESS_FILE)

    @classmethod
    def _determine_candidates(cls):
        already_labeled_ids = [duplicate["id"] for duplicate in cls.results]
        return cls.gdf[(cls.gdf.index == cls.gdf["candidate_id"]) & (~cls.gdf["candidate_id"].isin(already_labeled_ids))]


def _load_progress():
    if os.path.exists(_PROGRESS_FILE):
        return pd.read_pickle(_PROGRESS_FILE).to_dict("records")

    return []


def _read_geodata(filepath):
    return gpd.read_parquet(filepath).to_crs("EPSG:4326")
