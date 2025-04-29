from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Union

from geopandas import GeoDataFrame
from pandas import DataFrame, Series, Index
from shapely.geometry import Point
from sklearn import metrics
import pandas as pd

from geo_matcher.candidate_pairs import CandidatePairs
from geo_matcher import spatial

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
    def init(cls, data_path: str, results_path: str, annotation_redundancy: int, logger: Callable[[str], None] = print) -> None:
        """
        Initialize the labeling state for a given candidate pair dataset, taking into account previously labeled pairs.
        """
        cls.logger = logger
        cls.annotation_redundancy = annotation_redundancy
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
            "time": datetime.now().isoformat(timespec="milliseconds")
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
        results["time"] = datetime.now().isoformat(timespec="milliseconds")
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
                - 'unlabeled': Return only pairs that have not been at all or not enough times.
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
                - 'unlabeled': Return only pairs that have not been at all or not enough times.
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
    def get_all_neighborhoods(cls) -> Index:
        """
        Return the unique list of neighborhoods in the dataset.
        """
        return Index(cls.pairs["id_new"].map(cls.data_b["neighborhood"]).unique())

    @classmethod
    def get_next_neighborhood(cls, label_mode: str, user: str = None) -> Optional[str]:
        """
        Return the next neighborhood to be labeled based on the selected labeling mode.

        Args:
            label_mode: Determines the labeling strategy. One of:
                - 'all': Return a neighborhood that has not yet been labeled by the current user.
                - 'unlabeled': Return a neighborhood that has not been labeled at all or not enough times.
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
                - 'unlabeled': Return a neighborhood that has not been labeled at all or not enough times.
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
    def get_top_labelers(cls) -> List[Dict[str, any]]:
        """
        Return a dictionary with the number of labeled pairs per user and their inter-annotator agreement score (Cohen's kappa).
        """
        results = cls._unique_results()
        user_counts = results["username"].value_counts(ascending=False).to_frame()[:5]
        user_counts["kappa"] = cls._inter_annotator_agreement()

        return user_counts.reset_index().to_dict(orient="records")

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
        cls._unique_results().to_pickle(_PROGRESS_FILEPATH)

    @staticmethod
    def _load_progress() -> List[Dict[str, any]]:
        if _PROGRESS_FILEPATH.exists():
            return pd.read_pickle(_PROGRESS_FILEPATH).to_dict("records")

        _PROGRESS_FILEPATH.parent.mkdir(parents=True, exist_ok=True)
        return []

    @classmethod
    def _unique_results(cls) -> DataFrame:
        if len(cls.results) == 0:
            return pd.DataFrame(columns=["neighborhood", "id_existing", "id_new", "match", "username", "time"])

        return pd.DataFrame(cls.results).drop_duplicates(subset=["id_existing", "id_new", "username"], keep="last")

    @classmethod
    def _next_pairs(cls, label_mode: str, user: str = None) -> List[Optional[tuple[str, str]]]:
        if label_mode == "all":
            remaining = cls._all_pairs()
        elif label_mode == "unlabeled":
            remaining = cls._insufficiently_labeled_pairs().union(cls._unlabeled_pairs())
        elif label_mode == "cross-validate":
            remaining = cls._insufficiently_labeled_pairs().union(cls._ambiguously_labeled_pairs())
        else:
            raise ValueError(f"Labeling mode '{label_mode}' is not supported.")

        remaining = remaining.drop(cls._labeled_pairs(user), errors="ignore")

        return remaining.to_list()

    @classmethod
    def _next_neighborhoods(cls, label_mode: str, user: str = None) -> List[Optional[str]]:
        if label_mode == "all":
            remaining = cls.get_all_neighborhoods()
        elif label_mode == "unlabeled":
            remaining = cls._insufficiently_labeled_neighborhoods().union(cls._unlabeled_neighborhoods())
        elif label_mode == "cross-validate":
            remaining = cls._insufficiently_labeled_neighborhoods()
        else:
            raise ValueError(f"Labeling mode '{label_mode}' is not supported.")

        remaining = remaining.drop(cls._labeled_neighborhoods(user), errors="ignore")

        return remaining

    @classmethod
    def _unlabeled_pairs(cls) -> Index:
        labeled_pairs = set((result["id_existing"], result["id_new"]) for result in cls.results)
        all_pairs = cls._all_pairs()
        unlabeled = all_pairs.drop(labeled_pairs, errors="ignore")

        return unlabeled

    @classmethod
    def _unlabeled_neighborhoods(cls) -> Index:
        labeled_nbh = set(result["neighborhood"] for result in cls.results)
        all_nbh = set(cls.get_all_neighborhoods())
        unlabeled = Index(all_nbh - labeled_nbh)

        return unlabeled

    @classmethod
    def _insufficiently_labeled_pairs(cls) -> Index:
        labeling_count = cls._unique_results().groupby(["id_existing", "id_new"])["username"].nunique()
        insufficiently_labeled = labeling_count[labeling_count < cls.annotation_redundancy + 1].index

        return insufficiently_labeled

    @classmethod
    def _insufficiently_labeled_neighborhoods(cls) -> Index:
        labeling_count = cls._unique_results().groupby("neighborhood")["username"].nunique()
        insufficiently_labeled = labeling_count[labeling_count < cls.annotation_redundancy + 1].index

        return insufficiently_labeled

    @classmethod
    def _ambiguously_labeled_pairs(cls) -> Index:
        label_counts = cls._unique_results().groupby(["id_existing", "id_new"])[
            "match"].value_counts().unstack().reindex(columns=["yes", "no"], fill_value=0)
        ambiguous_pairs = label_counts[label_counts["yes"] == label_counts["no"]].index

        return ambiguous_pairs

    @classmethod
    def _all_pairs(cls) -> Index:
        return pd.MultiIndex.from_frame(cls.pairs[["id_existing", "id_new"]])

    @classmethod
    def _labeled_pairs(cls, user: str) -> Index:
        results = cls._unique_results()
        user_results = results[results["username"] == user]
        user_pairs = pd.MultiIndex.from_frame(user_results[["id_existing", "id_new"]])

        return user_pairs

    @classmethod
    def _labeled_neighborhoods(cls, user: str) -> Index:
        results = cls._unique_results()
        user_results = results[results["username"] == user]
        user_neighborhoods = pd.Index(user_results["neighborhood"].unique())

        return user_neighborhoods

    @classmethod
    def _inter_annotator_agreement(cls) -> Dict[str, float]:
        """
        Calculate Cohen's Kappa score for each user compared to the consensus of all other users.

        Returns:
            A dictionary mapping username to Cohen's Kappa score.
        """
        # Only consider pairs labeled by more than one user
        multi_labeled = cls._unique_results().groupby(["id_existing", "id_new"]).filter(lambda g: g["username"].nunique() > 1)

        kappas = {}
        for user in multi_labeled["username"].unique():
            user_df = multi_labeled[multi_labeled["username"] == user]
            other_df = multi_labeled[multi_labeled["username"] != user]

            consensus = other_df.groupby(["id_existing", "id_new"])["match"].agg(lambda x: x.mode().iloc[0]).rename("consensus")
            merged = user_df.merge(consensus, on=["id_existing", "id_new"], how="inner")

            kappas[user] = metrics.cohen_kappa_score(merged["match"].values, merged["consensus"].values, labels=["yes", "no", "unsure"])

        return kappas
