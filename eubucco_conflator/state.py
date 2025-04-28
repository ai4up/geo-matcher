from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Union

from geopandas import GeoDataFrame
from pandas import DataFrame, Series, Index
from shapely.geometry import Point
import numpy as np
import pandas as pd

from eubucco_conflator.candidate_pairs import CandidatePairs
from eubucco_conflator import spatial

_PROGRESS_FILEPATH = Path(".progress", "labeling-progress.pickle")


class State:
    """
    Manage the state of the candidate building pair labeling process.

    Handles loading data, tracking progress, and storing results for
    individual candidate pairs and neighborhood-level matches.
    """
    data: Optional[CandidatePairs] = None
    results: List[Dict[str, any]] = []

    @classmethod
    def init(cls, data_path: str, results_path: str, logger: Callable[[str], None] = print) -> None:
        """
        Initialize the labeling state for a given candidate pair dataset, taking into account previously labeled pairs.
        """
        cls.logger = logger
        cls.results_path = Path(results_path)
        cls.data = CandidatePairs.load(data_path)
        cls.data.preliminary_matching_estimate()
        cls.results = cls._load_progress()

        # Add pointers to improve readability
        cls.data_a = cls.data.dataset_a
        cls.data_b = cls.data.dataset_b
        cls.pairs = cls.data.pairs

    @classmethod
    def get_existing_buildings(cls, neighborhood: str) -> GeoDataFrame:
        """
        Return existing buildings in or linked to the given neighborhood.
        """
        nbh_a = cls.data_a[cls.data_a["neighborhood"] == neighborhood]

        # Edge case: also get the existing buildings of candidate pairs, where only the new building is in the neighborhood of interest
        nbh_b = cls.data_b[cls.data_b["neighborhood"] == neighborhood]
        candidate_ids = cls.pairs[cls.pairs["id_new"].isin(nbh_b.index)]["id_existing"]
        candidates = cls.data_a.loc[candidate_ids]

        return pd.concat([nbh_a, candidates]).drop_duplicates()

    @classmethod
    def get_new_buildings(cls, iteration: str) -> GeoDataFrame:
        """
        Return new buildings in the given neighborhood.
        """
        return cls.data_b[cls.data_b["neighborhood"] == iteration]

    @classmethod
    def get_existing_buildings_at(cls, loc: Point) -> GeoDataFrame:
        """
        Return existing buildings within 150 meters of the given location.
        """
        return spatial.within(cls.data_a, loc, dis=150)

    @classmethod
    def get_new_building_at(cls, loc: Point) -> GeoDataFrame:
        """
        Return new buildings within 150 meters of the given location.
        """
        return spatial.within(cls.data_b, loc, dis=150)

    @classmethod
    def get_candidate_pair(cls, id_existing: str, id_new: str) -> Series:
        """
        Return a candidate pair including the geometry of both buildings.
        """
        return Series({
            "id_existing": id_existing,
            "id_new": id_new,
            "geometry_existing": cls.data_a.geometry[id_existing],
            "geometry_new": cls.data_b.geometry[id_new],
        })

    @classmethod
    def get_candidate_pairs(cls, neighborhood: str) -> Union[DataFrame, GeoDataFrame]:
        """
        Return all candidate pairs in the given neighborhood including their geometries.
        """
        new = cls.get_new_buildings(neighborhood)
        pairs = cls.pairs[cls.pairs["id_new"].isin(new.index)]

        pairs = GeoDataFrame(pairs)
        pairs["geometry_existing"] = pairs["id_existing"].map(cls.data_a.geometry)
        pairs["geometry_new"] = pairs["id_new"].map(cls.data_b.geometry)

        return pairs

    @classmethod
    def add_result(cls, id_existing: str, id_new: str, match: str, username: str) -> None:
        """
        Store a labeling decision for a candidate pair.
        """
        if match not in ["yes", "no", "unsure"]:
            raise ValueError(f"Match label '{match}' must be one of: 'yes', 'no', 'unsure'.")

        cls.results.append({
            "neighborhood": None,
            "id_existing": id_existing,
            "id_new": id_new,
            "match": match,
            "username": username,
            "time": datetime.now().isoformat(timespec='milliseconds')
        })
        cls._store_progress()

        if len(cls.results) % 10 == 0:
            frequency = dict(Counter([e["match"] for e in cls.results]))
            cls.logger(f"Progress: {len(cls.results)} buildings labeled ({frequency})")

    @classmethod
    def add_bulk_results(cls, df: DataFrame) -> None:
        """
        Store multiple labeling decisions from a DataFrame.
        """
        if not df["match"].isin(["yes", "no", "unsure"]).all():
            raise ValueError("Match label must be one of: 'yes', 'no', 'unsure'.")

        results = df[["neighborhood", "id_existing", "id_new", "match", "username"]]
        results["time"] = datetime.now().isoformat(timespec='milliseconds')
        cls.results.extend(results.to_dict(orient="records"))
        cls._store_progress()

    @classmethod
    def valid_pair(cls, id_existing: str, id_new: str) -> Series:
        """
        Check whether a given ID pair exists in the candidate pairs.
        """
        return id_existing in cls.pairs["id_existing"].values and id_new in cls.pairs["id_new"].values

    @classmethod
    def get_next_pair(cls, label_mode: str, user: str = None) -> Optional[tuple[str, str]]:
        """
        Return the next candidate pair to be labeled based on the selected labeling mode.

        Args:
            label_mode: Determines the labeling strategy. One of:
                - 'all': Return only pairs that have not yet been labeled by the current user.
                - 'unlabeled': Return only pairs that have not been labeled by any user.
                - 'cross-validate': Return pairs that have either been labeled only once or received conflicting labels, for cross-validation.
            user: Optional. The current user's identifier.

        Returns:
            The next (id_existing, id_new) pair to be labeled, or (None, None) if no suitable pair is found.
        """
        try:
            return cls._next_pairs(label_mode, user)[0]

        except IndexError:
            return None, None

    @classmethod
    def get_pair_after_next(cls, label_mode: str, user: str = None) -> Optional[tuple[str, str]]:
        """
        Return the next but one candidate pair to be labeled based on the selected labeling mode.

        Args:
            label_mode: Determines the labeling strategy. One of:
                - 'all': Return only pairs that have not yet been labeled by the current user.
                - 'unlabeled': Return only pairs that have not been labeled by any user.
                - 'cross-validate': Return pairs that have either been labeled only once or received conflicting labels, for cross-validation.
            user: Optional. The current user's identifier

        Returns:
            The next but one (id_existing, id_new) pair to be labeled, or (None, None) if no suitable pair is found.
        """
        try:
            return cls._next_pairs(label_mode, user)[1]

        except IndexError:
            return None, None

    @classmethod
    def get_neighborhoods(cls) -> np.array:
        """
        Return the unique list of neighborhoods in the dataset.
        """
        return cls.pairs["id_new"].map(cls.data_b["neighborhood"]).unique()

    @classmethod
    def get_next_neighborhood(cls, label_mode: str, user: str = None) -> Optional[str]:
        """
        Return the next neighborhood to be labeled based on the selected labeling mode.

        Args:
            label_mode: Determines the labeling strategy. One of:
                - 'all': Return a neighborhood that has not yet been labeled by the current user.
                - 'unlabeled': Return a neighborhood that has not been labeled by any user.
                - 'cross-validate': Return a neighborhood that has been labeled only once, to allow cross-validation.
            user: Optional. The current user's identifier.

        Returns:
            The ID of the next neighborhood to be labeled, or None if no suitable neighborhood is found.
        """
        try:
            return cls._next_neighborhoods(label_mode, user)[0]

        except IndexError:
            return None

    @classmethod
    def get_neighborhood_after_next(cls, label_mode: str, user: str = None) -> Optional[str]:
        """
        Return the next but one neighborhood to be labeled based on the selected labeling mode.

        Args:
            label_mode: Determines the labeling strategy. One of:
                - 'all': Return a neighborhood that has not yet been labeled by the current user.
                - 'unlabeled': Return a neighborhood that has not been labeled by any user.
                - 'cross-validate': Return a neighborhood that has been labeled only once, to allow cross-validation.
            user: Optional. The current user's identifier.

        Returns:
            The ID of the next but one neighborhood to be labeled, or None if no suitable neighborhood is found.
        """
        try:
            return cls._next_neighborhoods(label_mode, user)[1]

        except IndexError:
            return None

    @classmethod
    def get_top_labelers(cls) -> Dict[str, int]:
        """
        Return a dictionary with the number of labeled pairs per user.
        """
        results = cls._unique_results()
        if results.empty:
            return {}

        user_counts = results["username"].value_counts(ascending=False)
        return user_counts[:5].to_dict()

    @classmethod
    def store_results(cls) -> None:
        """
        Save all labeled candidate pairs to disk as a CSV file.
        """
        cls._unique_results().to_csv(cls.results_path, index=False)
        cls.logger(
            f"Labeled building pairs stored in {cls.results_path}."
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
    def _next_pairs(cls, label_mode: str, user: str = None) -> List[Optional[tuple[str, str]]]:
        if label_mode == "all":
            remaining = cls._unlabeled_candidate_pairs(user)
        elif label_mode == "cross-validate":
            remaining = cls._non_consensus_candidate_pairs()
        else:
            remaining = cls._unlabeled_candidate_pairs()

        return list(zip(remaining["id_existing"], remaining["id_new"]))

    @classmethod
    def _next_neighborhoods(cls, label_mode: str, user: str = None) -> List[Optional[str]]:
        if label_mode == "all":
            return cls._unlabeled_neighborhoods(user)

        elif label_mode == "cross-validate":
            return cls._neighborhoods_labeled_once()

        else:
            return cls._unlabeled_neighborhoods()

    @classmethod
    def _unlabeled_neighborhoods(cls, user: str = None) -> Index:
        labeled_nbh = set(result["neighborhood"] for result in cls.results if user is None or result["username"] == user)
        all_nbh = set(cls.get_neighborhoods())
        unlabeled = list(all_nbh - labeled_nbh)

        return unlabeled

    @classmethod
    def _neighborhoods_labeled_once(cls) -> Index:
        labeling_count = pd.DataFrame(cls.results).groupby("neighborhood")["username"].nunique()
        labeled_once =  labeling_count[labeling_count == 1].index

        return labeled_once

    @classmethod
    def _unlabeled_candidate_pairs(cls, user: str = None) -> DataFrame:
        labeled_pairs = set((result["id_existing"], result["id_new"]) for result in cls.results if user is None or result["username"] == user)
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
        label_counts = cls._unique_results().groupby(["id_existing", "id_new"])[
            "match"].value_counts().unstack().reindex(columns=["yes", "no"], fill_value=0)
        ambiguous_pairs = label_counts[label_counts["yes"] == label_counts["no"]].index

        return ambiguous_pairs
