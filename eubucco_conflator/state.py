from collections import Counter
from pathlib import Path
from typing import List, Dict, Optional, Callable

import pandas as pd
import geopandas as gpd
from geopandas import GeoDataFrame

CANDIDATES_FILE = 'candidates.parquet'
RESULTS_FILE = 'results.csv'
_PROGRESS_FILEPATH = Path('.progress', 'labeling-progress.pickle')

class State:
    gdf: Optional[GeoDataFrame] = None
    candidates: Optional[GeoDataFrame] = None
    results: List[Dict[str, any]] = []

    @classmethod
    def init(cls, geodata_path: str, logger: Callable[[str], None] = print) -> None:
        cls.logger = logger
        cls.gdf = cls._read_geodata(geodata_path)
        cls.results = cls._load_progress()
        cls.candidates = cls._determine_candidates()

    @classmethod
    def add_result(cls, id: str, label: str, existing_id: str) -> None:
        cls.results.append({"id": id, "duplicate": label, "existing_id": existing_id})
        cls._store_progress()

        if len(cls.results) % 10 == 0:
            frequency = dict(Counter([e["duplicate"] for e in cls.results]))
            cls.logger(f"Progress: {len(cls.results)} buildings labeled ({frequency})")

    @classmethod
    def current_candidate_id(cls) -> Optional[str]:
        try:
            ids = cls.candidates.index
            return ids[~ids.isin(cls._already_labeled_id())][0]

        except IndexError:
            return None

    @classmethod
    def next_candidate_id(cls) -> Optional[str]:
        try:
            ids = cls.candidates.index
            return ids[~ids.isin(cls._already_labeled_id())][1]

        except IndexError:
            return None

    @classmethod
    def store_results(cls) -> None:
        pd.DataFrame(cls.results).drop_duplicates(subset=['id'], keep='first').to_csv(RESULTS_FILE, index=False)
        cls.logger(f"All buildings successfully labeled. Results stored in {RESULTS_FILE}.")

    @classmethod
    def _store_progress(cls) -> None:
        pd.DataFrame(cls.results).to_pickle(_PROGRESS_FILEPATH)

    @classmethod
    def _determine_candidates(cls) -> GeoDataFrame:
        candidates = cls.gdf[(cls.gdf.index == cls.gdf["candidate_id"]) & (~cls.gdf["candidate_id"].isin(cls._already_labeled_id()))]
        if candidates.index.has_duplicates:
            msg = "Index of duplicate building candidates must be unique. Please check your dataset."
            cls.logger(msg)
            raise ValueError(msg)

        return candidates

    @classmethod
    def _already_labeled_id(cls) -> List[str]:
        return [duplicate["id"] for duplicate in cls.results]

    @staticmethod
    def _load_progress() -> List[Dict[str, any]]:
        if _PROGRESS_FILEPATH.exists():
            return pd.read_pickle(_PROGRESS_FILEPATH).to_dict("records")

        _PROGRESS_FILEPATH.parent.mkdir(parents=True, exist_ok=True)
        return []

    @staticmethod
    def _read_geodata(filepath: str) -> GeoDataFrame:
        return gpd.read_parquet(filepath).to_crs("EPSG:4326")
