from collections import Counter
from pathlib import Path
from typing import Callable, Dict, List, Optional, Union

from geopandas import GeoDataFrame
from pandas import DataFrame, Series, Index
from shapely.geometry import Point
import pandas as pd

from eubucco_conflator.labeling_dataset import LabelingDataset
from eubucco_conflator import spatial

RESULTS_FILE = "results.csv"
_PROGRESS_FILEPATH = Path(".progress", "labeling-progress.pickle")


class State:
    data: Optional[LabelingDataset] = None
    results: List[Dict[str, any]] = []

    @classmethod
    def init(cls, geodata_path: str, logger: Callable[[str], None] = print) -> None:
        cls.logger = logger
        cls.data = LabelingDataset.load(geodata_path)
        cls.data.preliminary_matching_estimate()
        cls.results = cls._load_progress()

    @classmethod
    def get_existing_buildings(cls, iteration: str) -> GeoDataFrame:
        mask = cls.data.dataset_a["neighborhood"] == iteration
        nbh = cls.data.dataset_a[mask]

        # Edge case: the existing building of a candidate pair can be in a different neighborhood
        candidates = cls.get_candidates_existing(iteration)
        return pd.concat([candidates, nbh]).drop_duplicates()

    @classmethod
    def get_new_buildings(cls, iteration: str) -> GeoDataFrame:
        mask = cls.data.dataset_b["neighborhood"] == iteration

        return cls.data.dataset_b[mask]

    @classmethod
    def get_existing_buildings_at(cls, loc: Point) -> GeoDataFrame:
        return spatial.within(cls.data.dataset_a, loc, dis=150)

    @classmethod
    def get_new_building_at(cls, loc: Point) -> GeoDataFrame:
        return spatial.within(cls.data.dataset_b, loc, dis=150)

    @classmethod
    def get_candidates_existing(cls, iteration: str) -> DataFrame:
        pairs = cls.data.candidate_pairs.loc[[iteration]]
        existing = cls.data.dataset_a.loc[pairs["id_existing"]]

        return existing

    @classmethod
    def get_candidates_new(cls, iteration: str) -> DataFrame:
        pairs = cls.data.candidate_pairs.loc[[iteration]]
        new = cls.data.dataset_b.loc[pairs["id_new"]]

        return new

    @classmethod
    def get_candidate_pair(cls, id_existing: str, id_new: str) -> Series:
        return Series({
            "id_existing": id_existing,
            "id_new": id_new,
            "geometry_existing": cls.data.dataset_a.geometry[id_existing],
            "geometry_new": cls.data.dataset_b.geometry[id_new],
        }, name=cls.data.dataset_b.loc[id_new]["neighborhood"])

    @classmethod
    def valid_pair(cls, id_existing: str, id_new: str) -> Series:
        return id_existing in cls.data.candidate_pairs["id_existing"].values and id_new in cls.data.candidate_pairs["id_new"].values

    @classmethod
    def get_candidate_pairs(cls, neighborhood: str, geometry=False) -> Union[DataFrame, GeoDataFrame]:
        pairs = cls.data.candidate_pairs.loc[[neighborhood]]

        if geometry:
            pairs = GeoDataFrame(pairs)
            pairs["geometry_existing"] = pairs["id_existing"].map(cls.data.dataset_a.geometry)
            pairs["geometry_new"] = pairs["id_new"].map(cls.data.dataset_b.geometry)

        return pairs

    @classmethod
    def add_result(cls, id_existing: str, id_new: str, match: str) -> None:
        if match not in ["yes", "no", "unsure"]:
            raise ValueError(f"Match label '{match}' must be one of: 'yes', 'no', 'unsure'.")

        cls.results.append({"neighborhood": None, "id_existing": id_existing, "id_new": id_new, "match": match})
        cls._store_progress()

        if len(cls.results) % 10 == 0:
            frequency = dict(Counter([e["match"] for e in cls.results]))
            cls.logger(f"Progress: {len(cls.results)} buildings labeled ({frequency})")

    @classmethod
    def add_bulk_results(cls, df: DataFrame) -> None:
        if not df["match"].isin(["yes", "no", "unsure"]).all():
            raise ValueError("Match label must be one of: 'yes', 'no', 'unsure'.")
        
        results = df[["neighborhood", "id_existing", "id_new", "match"]]
        cls.results.extend(results.to_dict(orient="records"))
        cls._store_progress()

    @classmethod
    def current_pair(cls) -> Optional[tuple[str, str]]:
        try:
            unlabeled = cls._unlabeled_candidate_pairs()
            return tuple(unlabeled[["id_existing", "id_new"]].values[0])

        except IndexError:
            return None, None

    @classmethod
    def next_pair(cls) -> Optional[tuple[str, str]]:
        try:
            unlabeled = cls._unlabeled_candidate_pairs()
            return tuple(unlabeled[["id_existing", "id_new"]].values[1])

        except IndexError:
            return None, None

    @classmethod
    def current_neighborhood(cls) -> Optional[str]:
        try:
            return cls._unlabeled_neighborhoods()[0]

        except IndexError:
            return None

    @classmethod
    def next_neighborhood(cls) -> Optional[str]:
        try:
            return cls._unlabeled_neighborhoods()[1]

        except IndexError:
            return None

    @classmethod
    def store_results(cls) -> None:
        pd.DataFrame(cls.results).drop_duplicates(subset=["id_existing", "id_new"], keep="last").to_csv(
            RESULTS_FILE, index=False
        )
        cls.logger(
            f"All buildings successfully labeled. Results stored in {RESULTS_FILE}."
        )

    @classmethod
    def _store_progress(cls) -> None:
        pd.DataFrame(cls.results).to_pickle(_PROGRESS_FILEPATH)

    @classmethod
    def _unlabeled_neighborhoods(cls) -> Index:
        labeled_nbh = set(result["neighborhood"] for result in cls.results)
        all_nbh = cls.data.candidate_pairs.index
        unlabeled = all_nbh[~all_nbh.isin(labeled_nbh)]

        return unlabeled

    @classmethod
    def _unlabeled_candidate_pairs(cls) -> DataFrame:
        labeled_pairs = set((result["id_existing"], result["id_new"]) for result in cls.results)
        all_pairs = cls.data.candidate_pairs[["id_existing", "id_new"]].apply(tuple, axis=1)
        unlabeled = cls.data.candidate_pairs[~all_pairs.isin(labeled_pairs)]

        return unlabeled
    
    @staticmethod
    def _load_progress() -> List[Dict[str, any]]:
        if _PROGRESS_FILEPATH.exists():
            return pd.read_pickle(_PROGRESS_FILEPATH).to_dict("records")

        _PROGRESS_FILEPATH.parent.mkdir(parents=True, exist_ok=True)
        return []
