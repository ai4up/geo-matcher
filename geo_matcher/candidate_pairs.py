from pathlib import Path
import logging
import tempfile
import zipfile

from geopandas import GeoDataFrame
from pandas import DataFrame
import pandas as pd
import geopandas as gpd

from geo_matcher import spatial

logger = logging.getLogger(__name__)


class CandidatePairs:
    """
    Class to store and persist potential matching pairs of buildings or places from two datasets.
    """

    def __init__(
        self,
        dataset_a: GeoDataFrame,
        dataset_b: GeoDataFrame,
        pairs: DataFrame,
        feature_type: str,
    ):
        self._validate_inputs(dataset_a, dataset_b, pairs, feature_type)

        self.dataset_a = dataset_a
        self.dataset_b = dataset_b
        self.pairs = pairs
        self.feature_type = feature_type

        if self.feature_type == "places":
            self.dataset_a = self.dataset_a.to_crs("EPSG:4326")
            self.dataset_b = self.dataset_b.to_crs("EPSG:4326")

    @staticmethod
    def load(filepath: str) -> "CandidatePairs":
        """
        Load an instance from a zip file containing the datasets and candidate pairs as Parquet files.
        """
        filepath = Path(filepath)
        if not filepath.exists():
            raise FileNotFoundError(f"File not found: {filepath}")

        with tempfile.TemporaryDirectory() as tmpdir:
            with zipfile.ZipFile(filepath, "r") as zipf:
                zipf.extractall(tmpdir)

            tmpdir = Path(tmpdir)
            dataset_a = gpd.read_parquet(tmpdir / "dataset_a.parquet")
            dataset_b = gpd.read_parquet(tmpdir / "dataset_b.parquet")
            pairs = pd.read_parquet(tmpdir / "pairs.parquet")
            feature_type = "buildings" if dataset_a.geometry.geom_type.isin({"Polygon", "MultiPolygon"}).all() else "places"

        return CandidatePairs(dataset_a, dataset_b, pairs, feature_type)

    def save(self, filepath: str) -> None:
        """
        Save the instance as a zip file containing the datasets and candidate pairs as Parquet files.
        """
        filepath = Path(filepath)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            self.dataset_a.to_parquet(tmpdir / "dataset_a.parquet")
            self.dataset_b.to_parquet(tmpdir / "dataset_b.parquet")
            self.pairs.to_parquet(tmpdir / "pairs.parquet")

            with zipfile.ZipFile(filepath, "w", zipfile.ZIP_DEFLATED) as zipf:
                zipf.write(tmpdir / "dataset_a.parquet", arcname="dataset_a.parquet")
                zipf.write(tmpdir / "dataset_b.parquet", arcname="dataset_b.parquet")
                zipf.write(tmpdir / "pairs.parquet", arcname="pairs.parquet")

    def pairs_to_gdf(self) -> GeoDataFrame:
        """
        Create a GeoDataFrame of potential matching pairs.
        """
        gdf = GeoDataFrame(self.pairs)
        gdf["geometry_existing"] = gdf["id_existing"].map(self.dataset_a.geometry)
        gdf["geometry_new"] = gdf["id_new"].map(self.dataset_b.geometry)

        return gdf

    def preliminary_matching_estimate(self) -> None:
        """
        Estimate the matching between buildings in dataset_a and dataset_b.
        """
        if self.feature_type != "buildings":
            logger.warning("Preliminary matching estimation is only supported for buildings. Skipping.")
            return

        if "match" in self.pairs.columns:
            logger.info("Matching has already been performed.")
            return

        existing_geom = self.dataset_a.loc[self.pairs["id_existing"]]
        new_geom = self.dataset_b.loc[self.pairs["id_new"]]
        self.pairs["match"] = spatial.corresponding(existing_geom, new_geom)

    def _validate_inputs(self, dataset_a: GeoDataFrame, dataset_b: GeoDataFrame, pairs: DataFrame, feature_type: str) -> None:
        if not dataset_a.index.is_unique:
            raise ValueError("Dataset A must have a unique index.")

        if not dataset_b.index.is_unique:
            raise ValueError("Dataset B must have a unique index.")

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

        if feature_type == "buildings" and not dataset_a.geometry.geom_type.isin({"Polygon", "MultiPolygon"}).all():
            raise ValueError("Dataset A must only contain Polygon or MultiPolygon geometries representing buildings.")

        if feature_type == "buildings" and not dataset_b.geometry.geom_type.isin({"Polygon", "MultiPolygon"}).all():
            raise ValueError("Dataset B must only contain Polygon or MultiPolygon geometries representing buildings.")

        if not "neighborhood" in dataset_a.columns:
            raise ValueError("Dataset A must contain a 'neighborhood' column.")

        if not "neighborhood" in dataset_b.columns:
            raise ValueError("Dataset B must contain a 'neighborhood' column.")

        if not isinstance(pairs, pd.DataFrame):
            raise TypeError("Candidate pairs must be a DataFrame.")

        required_cols = {"id_existing", "id_new"}
        if not required_cols.issubset(pairs.columns):
            raise ValueError(f"Candidate pairs must contain columns: {required_cols}")

        invalid_existing = ~pairs["id_existing"].isin(dataset_a.index)
        if invalid_existing.any():
            raise ValueError(f"Candidate pairs contain IDs not included in Dataset A: {pairs['id_existing'][invalid_existing].tolist()}")

        invalid_new = ~pairs["id_new"].isin(dataset_b.index)
        if invalid_new.any():
            raise ValueError(f"Candidate pairs contain IDs not included in Dataset B: {pairs['id_new'][invalid_new].tolist()}")
