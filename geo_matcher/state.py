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


class State:
    """
    Manage the state of the candidate building pair labeling process.

    Handles loading data, tracking progress, and storing results for
    individual candidate pairs and neighborhood-level matches.
    """

    def __init__(self, data_path: str, results_path: str, annotation_redundancy: int, consensus_margin: int, logger: Callable[[str], None] = print, random_state: int = 42) -> None:
        """
        Initialize the labeling state for a given candidate pair dataset, taking into account previously labeled pairs.
        """
        self.logger = logger
        self.random_state = random_state
        self.annotation_redundancy = annotation_redundancy
        self.consensus_margin = consensus_margin
        self.results_path = Path(results_path)
        self.data = CandidatePairs.load(data_path)
        self.data.preliminary_matching_estimate()
        self.results = self._load_results()

        # Add pointers to improve readability
        self.data_a = self.data.dataset_a
        self.data_b = self.data.dataset_b
        self.pairs = self.data.pairs

    def get_existing_buildings(self, neighborhood: str) -> GeoDataFrame:
        """
        Return existing buildings in or linked to the given neighborhood.
        """
        nbh_a = self.data_a[self.data_a["neighborhood"] == neighborhood]

        # Edge case: also get the existing buildings of candidate pairs, where only the new building is in the neighborhood of interest
        nbh_b = self.data_b[self.data_b["neighborhood"] == neighborhood]
        candidate_ids = self.pairs[self.pairs["id_new"].isin(nbh_b.index)]["id_existing"]
        candidates = self.data_a.loc[candidate_ids]

        return pd.concat([nbh_a, candidates]).drop_duplicates()

    def get_new_buildings(self, iteration: str) -> GeoDataFrame:
        """
        Return new buildings in the given neighborhood.
        """
        return self.data_b[self.data_b["neighborhood"] == iteration]

    def get_existing_buildings_at(self, loc: Point) -> GeoDataFrame:
        """
        Return existing buildings within 150 meters of the given location.
        """
        return spatial.within(self.data_a, loc, dis=150)

    def get_new_building_at(self, loc: Point) -> GeoDataFrame:
        """
        Return new buildings within 150 meters of the given location.
        """
        return spatial.within(self.data_b, loc, dis=150)

    def get_candidate_pair(self, id_existing: str, id_new: str) -> Series:
        """
        Return a candidate pair including the geometry of both buildings.
        """
        return Series({
            "id_existing": id_existing,
            "id_new": id_new,
            "geometry_existing": self.data_a.geometry[id_existing],
            "geometry_new": self.data_b.geometry[id_new],
        })

    def get_candidate_pairs(self, neighborhood: str) -> Union[DataFrame, GeoDataFrame]:
        """
        Return all candidate pairs in the given neighborhood including their geometries.
        """
        new = self.get_new_buildings(neighborhood)
        pairs = self.pairs[self.pairs["id_new"].isin(new.index)]

        pairs = GeoDataFrame(pairs)
        pairs["geometry_existing"] = pairs["id_existing"].map(self.data_a.geometry)
        pairs["geometry_new"] = pairs["id_new"].map(self.data_b.geometry)

        return pairs

    def add_result(self, id_existing: str, id_new: str, match: str, username: str) -> None:
        """
        Store a labeling decision for a candidate pair.
        """
        if match not in ["yes", "no", "unsure"]:
            raise ValueError(f"Match label '{match}' must be one of: 'yes', 'no', 'unsure'.")

        self.results.append({
            "neighborhood": None,
            "id_existing": id_existing,
            "id_new": id_new,
            "match": match,
            "username": username,
            "time": datetime.now().isoformat(timespec="milliseconds")
        })
        self.store_results()

        if len(self.results) % 10 == 0:
            frequency = dict(Counter([e["match"] for e in self.results]))
            self.logger(f"Progress: {len(self.results)} buildings labeled ({frequency})")

    def add_bulk_results(self, df: DataFrame) -> None:
        """
        Store multiple labeling decisions from a DataFrame.
        """
        if not df["match"].isin(["yes", "no", "unsure"]).all():
            raise ValueError("Match label must be one of: 'yes', 'no', 'unsure'.")

        results = df[["neighborhood", "id_existing", "id_new", "match", "username"]]
        results["time"] = datetime.now().isoformat(timespec="milliseconds")
        self.results.extend(results.to_dict(orient="records"))
        self.store_results()

    def valid_pair(self, id_existing: str, id_new: str) -> Series:
        """
        Check whether a given ID pair exists in the candidate pairs.
        """
        return id_existing in self.pairs["id_existing"].values and id_new in self.pairs["id_new"].values

    def get_next_pair(self, label_mode: str, user: str = None) -> Optional[tuple[str, str]]:
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
            return self._next_pairs(label_mode, user)[0]

        except IndexError:
            return None, None

    def get_pair_after_next(self, label_mode: str, user: str = None) -> Optional[tuple[str, str]]:
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
            return self._next_pairs(label_mode, user)[1]

        except IndexError:
            return None, None

    def get_all_neighborhoods(self) -> Index:
        """
        Return the unique list of neighborhoods in the dataset.
        """
        return Index(self.pairs["id_new"].map(self.data_b["neighborhood"]).unique())

    def get_next_neighborhood(self, label_mode: str, user: str = None) -> Optional[str]:
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
            return self._next_neighborhoods(label_mode, user)[0]

        except IndexError:
            return None

    def get_neighborhood_after_next(self, label_mode: str, user: str = None) -> Optional[str]:
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
            return self._next_neighborhoods(label_mode, user)[1]

        except IndexError:
            return None

    def get_top_labelers(self) -> List[Dict[str, any]]:
        """
        Return a dictionary with the number of labeled pairs per user and their inter-annotator agreement score (Cohen's kappa).
        """
        results = self._unique_results(include_unsure=True)
        user_counts = results["username"].value_counts(ascending=False).to_frame()[:5]
        user_counts["kappa"] = self._inter_annotator_agreement()

        return user_counts.reset_index().to_dict(orient="records")

    def store_results(self) -> None:
        """
        Save all labeled candidate pairs to disk as a CSV file.
        """
        self._unique_results(include_unsure=True).to_csv(self.results_path, index=False)
        self.logger(
            f"Labeled building pairs stored in {self.results_path}."
        )

    def store_aggregated_results(self, path: str) -> None:
        """
        Summarize sufficiently labeled pairs with majority vote and label count, then write to CSV.
        """
        results = self._unique_results(include_unsure=True)
        unlabeled = self._next_pairs("unlabeled")
        labeled_mask = ~results[["id_existing", "id_new"]].apply(tuple, axis=1).isin(unlabeled)

        label_counts = (
            results[labeled_mask]
            .groupby(["id_existing", "id_new"])["match"]
            .value_counts()
            .unstack(fill_value=0)
            .reindex(columns=["yes", "no", "unsure"], fill_value=0)
        )

        label_counts["match"] = label_counts[["yes", "no", "unsure"]].idxmax(axis=1)
        label_counts = label_counts.rename(columns={"yes": "count_match", "no": "count_no_match", "unsure": "count_unsure"})

        label_counts.reset_index().to_csv(path, index=False)

    def _load_results(self) -> List[Dict[str, any]]:
        if self.results_path.exists():
            return pd.read_csv(self.results_path).to_dict("records")

        self.results_path.parent.mkdir(parents=True, exist_ok=True)
        return []

    def _unique_results(self, include_unsure: bool = False) -> DataFrame:
        if len(self.results) == 0:
            return pd.DataFrame(columns=["neighborhood", "id_existing", "id_new", "match", "username", "time"])

        results = pd.DataFrame(self.results).drop_duplicates(subset=["id_existing", "id_new", "username"], keep="last")
        if not include_unsure:
            results = results[results["match"] != "unsure"]

        return results

    def _next_pairs(self, label_mode: str, user: str = None) -> List[Optional[tuple[str, str]]]:
        if label_mode == "all":
            remaining = self._all_pairs()
        elif label_mode == "unlabeled":
            remaining = self._ambiguously_labeled_pairs().union(self._insufficiently_labeled_pairs(), sort=False).union(self._unlabeled_pairs(), sort=False)
        elif label_mode == "cross-validate":
            remaining = self._ambiguously_labeled_pairs().union(self._insufficiently_labeled_pairs(), sort=False)
        else:
            raise ValueError(f"Labeling mode '{label_mode}' is not supported.")

        remaining = remaining.drop(self._labeled_pairs(user), errors="ignore").to_list()

        return remaining

    def _next_neighborhoods(self, label_mode: str, user: str = None) -> List[Optional[str]]:
        if label_mode == "all":
            remaining = self.get_all_neighborhoods()
        elif label_mode == "unlabeled":
            remaining = self._insufficiently_labeled_neighborhoods().union(self._unlabeled_neighborhoods())
        elif label_mode == "cross-validate":
            remaining = self._insufficiently_labeled_neighborhoods()
        else:
            raise ValueError(f"Labeling mode '{label_mode}' is not supported.")

        remaining = remaining.drop(self._labeled_neighborhoods(user), errors="ignore")

        return remaining

    def _unlabeled_pairs(self) -> Index:
        labeled_pairs = set((result["id_existing"], result["id_new"]) for result in self.results if result["match"] != "unsure")
        all_pairs = self._all_pairs()
        unlabeled = all_pairs.drop(labeled_pairs, errors="ignore")

        return unlabeled

    def _unlabeled_neighborhoods(self) -> Index:
        labeled_nbh = set(result["neighborhood"] for result in self.results if result["match"] != "unsure")
        all_nbh = set(self.get_all_neighborhoods())
        unlabeled = Index(all_nbh - labeled_nbh)

        return unlabeled

    def _insufficiently_labeled_pairs(self) -> Index:
        labeling_count = self._unique_results().groupby(["id_existing", "id_new"])["username"].nunique()
        insufficiently_labeled = labeling_count[labeling_count < self.annotation_redundancy + 1].index

        return insufficiently_labeled

    def _insufficiently_labeled_neighborhoods(self) -> Index:
        labeling_count = self._unique_results().groupby("neighborhood")["username"].nunique()
        insufficiently_labeled = labeling_count[labeling_count < self.annotation_redundancy + 1].index

        return insufficiently_labeled

    def _ambiguously_labeled_pairs(self) -> Index:
        label_counts = (
            self._unique_results()
            .groupby(["id_existing", "id_new"])["match"]
            .value_counts()
            .unstack(fill_value=0)
            .reindex(columns=["yes", "no"], fill_value=0)
        )

        # Select pairs where the difference in votes is below an ambiguity threshold
        ambiguous_mask = (label_counts["yes"] - label_counts["no"]).abs() < self.consensus_margin
        ambiguous_pairs = label_counts[ambiguous_mask].index

        return ambiguous_pairs

    def _all_pairs(self) -> Index:
        return self._shuffled(pd.MultiIndex.from_frame(self.pairs[["id_existing", "id_new"]]))

    def _labeled_pairs(self, user: str) -> Index:
        results = self._unique_results(include_unsure=True)
        user_results = results[results["username"] == user]
        user_pairs = pd.MultiIndex.from_frame(user_results[["id_existing", "id_new"]])

        return user_pairs

    def _labeled_neighborhoods(self, user: str) -> Index:
        results = self._unique_results(include_unsure=True)
        user_results = results[results["username"] == user]
        user_neighborhoods = pd.Index(user_results["neighborhood"].unique())

        return user_neighborhoods

    def _inter_annotator_agreement(self) -> Dict[str, float]:
        """
        Calculate Cohen's Kappa score for each user compared to the consensus of all other users.

        Returns:
            A dictionary mapping username to Cohen's Kappa score.
        """
        def majority_vote(x):
            return x.mode().iloc[0] if len(x.mode()) == 1 else None

        # Only consider pairs labeled by more than one user
        multi_labeled = self._unique_results().groupby(["id_existing", "id_new"]).filter(lambda g: g["username"].nunique() > 1)

        kappas = {}
        for user in multi_labeled["username"].unique():
            user_df = multi_labeled[multi_labeled["username"] == user]
            other_df = multi_labeled[multi_labeled["username"] != user]

            consensus = other_df.groupby(["id_existing", "id_new"])["match"].agg(
                majority_vote).dropna().rename("consensus")
            merged = user_df.merge(consensus, on=["id_existing", "id_new"], how="inner")

            if merged.empty:
                kappas[user] = float("nan")
            else:
                kappas[user] = metrics.cohen_kappa_score(merged["match"].values, merged["consensus"].values, labels=["yes", "no"])

        return kappas

    def _shuffled(self, index: Index) -> Index:
        return index.to_series().sample(frac=1, random_state=self.random_state).index
