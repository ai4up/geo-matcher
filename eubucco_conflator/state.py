from collections import Counter
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
    def init(cls, geodata_path, logger=print):
        cls.logger = logger
        cls.gdf = cls._read_geodata(geodata_path)
        cls.results = cls._load_progress()
        cls.candidates = cls._determine_candidates()

    @classmethod
    def add_result(cls, id, label, existing_id):
        cls.results.append({"id": id, "duplicate": label, "existing_id": existing_id})
        cls._store_progress()

        if len(cls.results) % 10 == 0:
            frequency = dict(Counter([e["duplicate"] for e in cls.results]))
            cls.logger(f"Progress: {len(cls.results)} buildings labeled ({frequency})")

    @classmethod
    def current_candidate_id(cls):
        try:
            ids = cls.candidates.index
            return ids[~ids.isin(cls._already_labeled_id())][0]

        except IndexError:
            return None

    @classmethod
    def next_candidate_id(cls):
        try:
            ids = cls.candidates.index
            return ids[~ids.isin(cls._already_labeled_id())][1]

        except IndexError:
            return None

    @classmethod
    def store_results(cls):
        pd.DataFrame(cls.results).to_csv(RESULTS_FILE, index=False)
        cls.logger(f"All buildings successfully labled. Results stored in {RESULTS_FILE}.")

    @classmethod
    def _store_progress(cls):
        pd.DataFrame(cls.results).to_pickle(_PROGRESS_FILE)

    @classmethod
    def _determine_candidates(cls):
        candidates = cls.gdf[(cls.gdf.index == cls.gdf["candidate_id"]) & (~cls.gdf["candidate_id"].isin(cls._already_labeled_id()))]
        if candidates.index.has_duplicates:
            msg = "Index of duplicate building candidates must be unique. Please check your dataset."
            cls.logger(msg)
            raise ValueError(msg)

        return candidates

    @classmethod
    def _already_labeled_id(cls):
        return [duplicate["id"] for duplicate in cls.results]

    @staticmethod
    def _load_progress():
        if os.path.exists(_PROGRESS_FILE):
            return pd.read_pickle(_PROGRESS_FILE).to_dict("records")

        return []

    @staticmethod
    def _read_geodata(filepath):
        return gpd.read_parquet(filepath).to_crs("EPSG:4326")
