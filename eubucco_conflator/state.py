from collections import Counter
import os

import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

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
        cls.gdf = cls._read_data(geodata_path)
        cls.results = cls._load_progress()
        cls.candidates = cls._determine_candidates()

    @classmethod
    def add_result(cls, id, label, existing_id):
        cls.results.append({"base_id": id, "duplicate": label, "existing_id": existing_id})
        cls._store_progress()

        if len(cls.results) % 10 == 0:
            frequency = dict(Counter([e["duplicate"] for e in cls.results]))
            cls.logger(f"Progress: {len(cls.results)} buildings labeled ({frequency})")

    @classmethod
    def current_candidate_id(cls):
        if cls._ids_to_be_labeled():
            ids = cls.candidates.index
            return ids[ids.isin(cls._ids_to_be_labeled())][0]

        else:
            return None

    @classmethod
    def next_candidate_id(cls):
        if cls._ids_to_be_labeled():
            ids = cls.candidates.index
            return ids[ids.isin(cls._ids_to_be_labeled())][1]

        else:
            return None

    @classmethod
    def store_results(cls):
        pd.DataFrame(cls.results).drop_duplicates(subset=['base_id'], keep='first').to_csv(RESULTS_FILE, index=False)
        cls.logger(f"All places successfully labled. Results stored in {RESULTS_FILE}.")

    @classmethod
    def _store_progress(cls):
        pd.DataFrame(cls.results).to_pickle(_PROGRESS_FILE)

    @classmethod
    def _determine_candidates(cls):        
        return cls.gdf[cls.gdf.index.isin(cls._ids_to_be_labeled())]


    @classmethod
    def _ids_to_be_labeled(cls):
        df_pairs = set(cls.gdf.reset_index()[['base_id', 'can_id']].apply(tuple, axis=1))
        if cls.results:
            results_pairs = set([(result['base_id'], result['existing_id']) for result in cls.results])
            return list(set([pair[0] for pair in df_pairs.difference(results_pairs)]))
        else:
            return list(set([pair[0] for pair in df_pairs]))
    

    @staticmethod
    def _load_progress():        
        if os.path.exists(_PROGRESS_FILE):
            return pd.read_pickle(_PROGRESS_FILE).to_dict("records")

        return []

    @staticmethod
    def _read_data(filepath):
        return pd.read_parquet(filepath).set_index('base_id')
