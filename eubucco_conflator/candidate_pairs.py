import pickle

from geopandas import GeoDataFrame
from pandas import DataFrame
import pandas as pd

from eubucco_conflator import spatial


class CandidatePairs:
    """
    Class to store and persist potential matching pairs of buildings from two datasets.
    """

    def __init__(
        self,
        dataset_a: GeoDataFrame,
        dataset_b: GeoDataFrame,
        pairs: DataFrame,
    ):
        self._validate_inputs(dataset_a, dataset_b, pairs)

        self.dataset_a = dataset_a
        self.dataset_b = dataset_b
        self.pairs = pairs

    @staticmethod
    def load(filepath: str) -> "CandidatePairs":
        """
        Load an instance from a pickle file.
        """
        with open(filepath, 'rb') as f:
            return pickle.load(f)

    def save(self, filepath: str) -> None:
        """
        Save the instance to a file using pickle.
        """
        with open(filepath, 'wb') as f:
            pickle.dump(self, f)

    def preliminary_matching_estimate(self) -> None:
        """
        Estimate the matching between buildings in dataset_a and dataset_b.
        """
        existing_geom = self.dataset_a.loc[self.pairs["id_existing"]]
        new_geom = self.dataset_b.loc[self.pairs["id_new"]]
        self.pairs["match"] = spatial.corresponding(existing_geom, new_geom)

    def _validate_inputs(self, dataset_a: GeoDataFrame, dataset_b: GeoDataFrame, pairs: DataFrame) -> None:
        if not isinstance(dataset_a, GeoDataFrame):
            raise TypeError("Dataset A must be a GeoDataFrame.")

        if not isinstance(dataset_b, GeoDataFrame):
            raise TypeError("Dataset B must be a GeoDataFrame.")

        if dataset_a.active_geometry_name is None:
            raise ValueError("Dataset A must contain an active geometry column.")

        if dataset_b.active_geometry_name is None:
            raise ValueError("Dataset B must contain an active geometry column.")

        if dataset_a.crs != dataset_b.crs:
            raise ValueError("Dataset A and Dataset B must have the same CRS.")

        if not "neighborhood" in dataset_a.columns:
            raise ValueError("Dataset A must contain a 'neighborhood' column.")

        if not "neighborhood" in dataset_b.columns:
            raise ValueError("Dataset B must contain a 'neighborhood' column.")

        if not isinstance(pairs, pd.DataFrame):
            raise TypeError("pairs must be a DataFrame.")

        required_cols = {"id_existing", "id_new"}
        if not required_cols.issubset(pairs.columns):
            raise ValueError(f"Candidate pairs must contain columns: {required_cols}")

        invalid_existing = ~pairs["id_existing"].isin(dataset_a.index)
        if invalid_existing.any():
            raise ValueError(f"Candidate pairs contain IDs not included in Dataset A: {pairs['id_existing'][invalid_existing].tolist()}")

        invalid_new = ~pairs["id_new"].isin(dataset_b.index)
        if invalid_new.any():
            raise ValueError(f"Candidate pairs contain IDs not included in Dataset B: {pairs['id_new'][invalid_new].tolist()}")
