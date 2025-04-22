from collections import Counter
from pathlib import Path
from typing import Callable, Dict, List, Optional, Union

from geopandas import GeoDataFrame
from pandas import DataFrame, Series, Index
from shapely.geometry import Point
import numpy as np
import pandas as pd

from eubucco_conflator.labeling_dataset import LabelingDataset
from eubucco_conflator import spatial

_PROGRESS_FILEPATH = Path(".progress", "labeling-progress.pickle")


class State:
    data: Optional[LabelingDataset] = None
    results: List[Dict[str, any]] = []

    @classmethod
    def init(cls, data_path: str, results_path: str, logger: Callable[[str], None] = print) -> None:
        cls.logger = logger
        cls.results_path = results_path
        cls.data = LabelingDataset.load(data_path)
        cls.data.preliminary_matching_estimate()
        cls.results = cls._load_progress()

        # Add pointers to improve readability
        cls.data_a = cls.data.dataset_a
        cls.data_b = cls.data.dataset_b
        cls.pairs = cls.data.candidate_pairs

    @classmethod
    def get_existing_buildings(cls, neighborhood: str) -> GeoDataFrame:
        nbh_a = cls.data_a[cls.data_a["neighborhood"] == neighborhood]

        # Edge case: also get the existing buildings of candidate pairs, where only the new building is in the neighborhood of interest
        nbh_b = cls.data_b[cls.data_b["neighborhood"] == neighborhood]
        candidate_ids = cls.pairs[cls.pairs["id_new"].isin(nbh_b.index)]["id_existing"]
        candidates = cls.data_a.loc[candidate_ids]

        return pd.concat([nbh_a, candidates]).drop_duplicates()

    @classmethod
    def get_new_buildings(cls, iteration: str) -> GeoDataFrame:
        return cls.data_b[cls.data_b["neighborhood"] == iteration]

    @classmethod
    def get_existing_buildings_at(cls, loc: Point) -> GeoDataFrame:
        return spatial.within(cls.data_a, loc, dis=150)

    @classmethod
    def get_new_building_at(cls, loc: Point) -> GeoDataFrame:
        return spatial.within(cls.data_b, loc, dis=150)

    @classmethod
    def get_candidate_pair(cls, id_existing: str, id_new: str) -> Series:
        return Series({
            "id_existing": id_existing,
            "id_new": id_new,
            "geometry_existing": cls.data_a.geometry[id_existing],
            "geometry_new": cls.data_b.geometry[id_new],
        })

    @classmethod
    def get_candidate_pairs(cls, neighborhood: str) -> Union[DataFrame, GeoDataFrame]:
        new = cls.get_new_buildings(neighborhood)
        pairs = cls.pairs[cls.pairs["id_new"].isin(new.index)]

        pairs = GeoDataFrame(pairs)
        pairs["geometry_existing"] = pairs["id_existing"].map(cls.data_a.geometry)
        pairs["geometry_new"] = pairs["id_new"].map(cls.data_b.geometry)

        return pairs

    @classmethod
    def add_result(cls, id_existing: str, id_new: str, match: str, username: str) -> None:
        if match not in ["yes", "no", "unsure"]:
            raise ValueError(f"Match label '{match}' must be one of: 'yes', 'no', 'unsure'.")

        cls.results.append({
            "neighborhood": None,
            "id_existing": id_existing,
            "id_new": id_new,
            "match": match,
            "username": username,
        })
        cls._store_progress()

        if len(cls.results) % 10 == 0:
            frequency = dict(Counter([e["match"] for e in cls.results]))
            cls.logger(f"Progress: {len(cls.results)} buildings labeled ({frequency})")

    @classmethod
    def add_bulk_results(cls, df: DataFrame) -> None:
        if not df["match"].isin(["yes", "no", "unsure"]).all():
            raise ValueError("Match label must be one of: 'yes', 'no', 'unsure'.")

        results = df[["neighborhood", "id_existing", "id_new", "match", "username"]]
        cls.results.extend(results.to_dict(orient="records"))
        cls._store_progress()

    @classmethod
    def valid_pair(cls, id_existing: str, id_new: str) -> Series:
        return id_existing in cls.pairs["id_existing"].values and id_new in cls.pairs["id_new"].values

    @classmethod
    def current_pair(cls, cross_validate: bool = False) -> Optional[tuple[str, str]]:
        try:
            if cross_validate:
                remaining = cls._non_consensus_candidate_pairs()
            else:
                remaining = cls._unlabeled_candidate_pairs()

            return tuple(remaining[["id_existing", "id_new"]].values[0])

        except IndexError:
            return None, None

    @classmethod
    def next_pair(cls, cross_validate: bool = False) -> Optional[tuple[str, str]]:
        try:
            if cross_validate:
                remaining = cls._non_consensus_candidate_pairs()
            else:
                remaining = cls._unlabeled_candidate_pairs()

            return tuple(remaining[["id_existing", "id_new"]].values[1])

        except IndexError:
            return None, None

    @classmethod
    def neighborhoods(cls) -> np.array:
        return cls.pairs["id_new"].map(cls.data_b["neighborhood"]).unique()

    @classmethod
    def current_neighborhood(cls, cross_validate: bool = False) -> Optional[str]:
        try:
            if cross_validate:
                return cls._neighborhoods_labeled_once()[0]

            return cls._unlabeled_neighborhoods()[0]

        except IndexError:
            return None

    @classmethod
    def next_neighborhood(cls, cross_validate: bool = False) -> Optional[str]:
        try:
            if cross_validate:
                return cls._neighborhoods_labeled_once()[1]

            return cls._unlabeled_neighborhoods()[1]

        except IndexError:
            return None

    @classmethod
    def store_results(cls) -> None:
        cls._unique_results().to_csv(cls.results_path, index=False)
        cls.logger(
            f"All buildings successfully labeled. Results stored in {cls.results_path}."
        )

    @classmethod
    def _store_progress(cls) -> None:
        pd.DataFrame(cls.results).to_pickle(_PROGRESS_FILEPATH)

    @staticmethod
    def _load_progress() -> List[Dict[str, any]]:
        if _PROGRESS_FILEPATH.exists():
            return pd.read_pickle(_PROGRESS_FILEPATH).to_dict("records")

        _PROGRESS_FILEPATH.parent.mkdir(parents=True, exist_ok=True)
        return []

    @classmethod
    def _unique_results(cls) -> DataFrame:
        return pd.DataFrame(cls.results).drop_duplicates(subset=["id_existing", "id_new", "username"], keep="last")

    @classmethod
    def _unlabeled_neighborhoods(cls) -> Index:
        labeled_nbh = set(result["neighborhood"] for result in cls.results)
        all_nbh = set(cls.neighborhoods())
        unlabeled = list(all_nbh - labeled_nbh)

        return unlabeled

    @classmethod
    def _neighborhoods_labeled_once(cls) -> Index:
        labeling_count = pd.DataFrame(cls.results).groupby("neighborhood")["username"].nunique()
        labeled_once =  labeling_count[labeling_count == 1].index

        return labeled_once

    @classmethod
    def _unlabeled_candidate_pairs(cls) -> DataFrame:
        labeled_pairs = set((result["id_existing"], result["id_new"]) for result in cls.results)
        all_pairs = cls.pairs[["id_existing", "id_new"]].apply(tuple, axis=1)
        unlabeled = cls.pairs[~all_pairs.isin(labeled_pairs)]

        return unlabeled

    @classmethod
    def _non_consensus_candidate_pairs(cls) -> DataFrame:
        return cls._candidate_pairs_labeled_once().union(cls._ambiguously_labeled_candidate_pairs()).to_frame()

    @classmethod
    def _candidate_pairs_labeled_once(cls) -> Index:
        labeling_count = pd.DataFrame(cls.results).groupby(["id_existing", "id_new"])["username"].nunique()
        labeled_once = labeling_count[labeling_count == 1].index

        return labeled_once

    @classmethod
    def _ambiguously_labeled_candidate_pairs(cls) -> Index:
        label_counts = cls._unique_results().groupby(["id_existing", "id_new"])["match"].value_counts().unstack()
        ambiguous_pairs = label_counts[label_counts["yes"] == label_counts["no"]].index

        return ambiguous_pairs
