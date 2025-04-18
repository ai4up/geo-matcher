from typing import Tuple
import logging
import warnings

from geopandas import GeoDataFrame
from pandas import DataFrame
import geopandas as gpd
import numpy as np
import pandas as pd

from eubucco_conflator.labeling_dataset import LabelingDataset
from eubucco_conflator import spatial

warnings.simplefilter(action="ignore", category=pd.errors.SettingWithCopyWarning)
warnings.simplefilter(action="ignore", category=FutureWarning)

logger = logging.getLogger(__name__)
log = logger.info

def create_candidate_pairs_dataset(
    gdf_path1: str,
    gdf_path2: str,
    id_col: str,
    overlap_range: Tuple[float, float] = None,
    similarity_range: Tuple[float, float] = None,
    max_distance: float = None,
    one_to_one_matches_only: bool = False,
    n: int = None,
    n_neighborhoods: int = None,
    h3_res: int = 9,
) -> None:
    """
    Identifies pairs of potentially matching buildings from two datasets and stores them in a new dataset.
    Pairs are determined based on spatial proximity, topological overlap, and shape similarity.
    """
    cols = ["geometry", id_col] if id_col else ["geometry"]
    gdf1 = gpd.read_parquet(gdf_path1, columns=cols).to_crs(3035)
    gdf2 = gpd.read_parquet(gdf_path2, columns=cols).to_crs(3035)

    gdf1, gdf2 = _ensure_unique_index(gdf1, gdf2, id_col)

    gdf1["neighborhood"] = spatial.h3_index(gdf1, h3_res)
    gdf2["neighborhood"] = spatial.h3_index(gdf2, h3_res)

    pairs = _identify_candidate_pairs(gdf1, gdf2, max_distance)

    if overlap_range:
        pairs = _filter_candidate_pairs_by_overlap(pairs, gdf1, gdf2, overlap_range)

    if similarity_range:
        pairs = _filter_candidate_pairs_by_shape_similarity(pairs, gdf1, gdf2, similarity_range)

    if one_to_one_matches_only:
        pairs = _filter_one_to_one_correspondences(pairs, gdf1, gdf2)

    if n_neighborhoods:
        pairs = _sample_neighborhoods(pairs, gdf2, n_neighborhoods)

    if n:
        pairs = _sample_candidate_pairs(pairs, n)

    gdf1, gdf2 = _drop_buildings_elsewhere(gdf1, gdf2, pairs)

    LabelingDataset(
        dataset_a=gdf1,
        dataset_b=gdf2,
        candidate_pairs=pairs,
    ).save()


def _ensure_unique_index(
    gdf1: GeoDataFrame, gdf2: GeoDataFrame, id_col: str
) -> Tuple[GeoDataFrame, GeoDataFrame]:
    if id_col:
        gdf1 = gdf1.set_index(id_col)
        gdf2 = gdf2.set_index(id_col)

    if not gdf1.index.is_unique or not gdf2.index.is_unique:
        gdf1 = gdf1.reset_index()
        gdf2 = gdf2.reset_index()
        log("Unique index is required. Creating new, numerical index.")

    if _indices_overlap(gdf1, gdf2):
        gdf1.index = "A-" + gdf1.index.astype(str)
        gdf2.index = "B-" + gdf2.index.astype(str)
        log(
            "Indices of both datasets overlap. Adding the prefixes 'A-' and 'B-' to "
            + "avoid ambiguities. Alternatively, consider specifying an unique ID column."
        )

    return gdf1, gdf2


def _drop_buildings_elsewhere(
    gdf1: GeoDataFrame, gdf2: GeoDataFrame, pairs: DataFrame
) -> Tuple[GeoDataFrame, GeoDataFrame]:
    """To reduce dataset size, remove buildings in different neighborhoods than candidate pairs."""
    nbh1 = pairs["id_existing"].map(gdf1["neighborhood"])
    nbh2 = pairs["id_new"].map(gdf2["neighborhood"])
    nbh = pd.concat([nbh1, nbh2]).unique()
    nbh_w_neighbors = spatial.h3_disk(nbh, k=1)

    gdf1 = gdf1[gdf1["neighborhood"].isin(nbh_w_neighbors)]
    gdf2 = gdf2[gdf2["neighborhood"].isin(nbh_w_neighbors)]

    return gdf1, gdf2


def _identify_candidate_pairs(
    gdf1: GeoDataFrame, gdf2: GeoDataFrame, max_distance: float
) -> DataFrame:
    """
    Determine the best match for each building in gdf1 and gdf2 by identifying overlaps
    or, if none, the nearest building in the other GeoDataFrame.
    """
    # Determine pairs of overlapping buildings
    idx1, idx2 = _determine_overlapping_candidate_pairs(gdf1, gdf2)

    # For non-overlapping buildings, determine the respective nearest building
    gdf1_non_intersect = gdf1.drop(idx1)
    gdf2_non_intersect = gdf2.drop(idx2)

    log(f"For {len(gdf1_non_intersect) / len(gdf1) * 100:.1f}% and {len(gdf2_non_intersect) / len(gdf2) * 100:.1f}% non-overlapping buildings in gdf1 and gdf2, respectively, find the nearest building in the other GeoDataFrame.")
    idx1_nearest_a, idx2_nearest_a = spatial.nearest_neighbor(gdf1_non_intersect, gdf2, max_distance)
    idx2_nearest_b, idx1_nearest_b = spatial.nearest_neighbor(gdf2_non_intersect, gdf1, max_distance)

    idx1 = np.concatenate([idx1, idx1_nearest_a, idx1_nearest_b])
    idx2 = np.concatenate([idx2, idx2_nearest_a, idx2_nearest_b])

    pairs = DataFrame({"id_existing": idx1, "id_new": idx2})

    # Drop duplicate pairs which were introduced because the two buildings are both nearest to each other
    pairs = pairs.drop_duplicates()

    return pairs


def _determine_overlapping_candidate_pairs(
    gdf1: GeoDataFrame, gdf2: GeoDataFrame, tolerance: float = 0.01
) -> Tuple[pd.Index, pd.Index]:
    idx1, idx2 = spatial.overlapping(gdf1, gdf2)

    # Filter slightly overlapping buildings
    overlap = spatial.symmetrical_pairwise_relative_overlap(gdf1.loc[idx1], gdf2.loc[idx2])
    mask = overlap > tolerance

    return idx1[mask], idx2[mask]


def _filter_candidate_pairs_by_overlap(
        pairs: DataFrame, gdf1: GeoDataFrame, gdf2: GeoDataFrame, overlap_range: Tuple[float, float]
    ) -> DataFrame:
    """Filter candidate pairs based on their degree of overlap, i.e. their Two-Way Area Overlap (TWAO)."""
    gdf1_can = gdf1.loc[pairs["id_existing"]]
    gdf2_can = gdf2.loc[pairs["id_new"]]

    overlap = spatial.symmetrical_pairwise_relative_overlap(gdf1_can, gdf2_can)
    mask = (overlap >= overlap_range[0]) & (overlap <= overlap_range[1])

    return pairs[mask]


def _filter_candidate_pairs_by_shape_similarity(
        candidate_pairs: DataFrame, gdf1: GeoDataFrame, gdf2: GeoDataFrame, similarity_range: Tuple[float, float]
    ) -> DataFrame:
    """Filter candidate pairs based on their shape similarity."""
    gdf1_can = gdf1.loc[candidate_pairs["id_existing"]]
    gdf2_can = gdf2.loc[candidate_pairs["id_new"]]

    similarity = spatial.shape_similarity(gdf1_can, gdf2_can)
    mask = ((similarity >= similarity_range[0]) & (similarity <= similarity_range[1])).values

    return candidate_pairs[mask]


def _filter_one_to_one_correspondences(
    pairs: DataFrame, gdf1: GeoDataFrame, gdf2: GeoDataFrame
) -> DataFrame:
    """
    Keep only candidate pairs that are likely one-to-one match.
    """
    gdf1_can = gdf1[gdf1.index.isin(pairs["id_existing"])]
    gdf2_can = gdf2[gdf2.index.isin(pairs["id_new"])]

    # Calculate relative overlap between candidates and all other buildings
    gdf1_can["overlap"] = spatial.relative_overlap(gdf1_can, gdf2)
    gdf2_can["overlap"] = spatial.relative_overlap(gdf2_can, gdf1)

    pairs["overlap_existing"] = pairs["id_existing"].map(gdf1_can["overlap"].fillna(0))
    pairs["overlap_new"] = pairs["id_new"].map(gdf2_can["overlap"].fillna(0))

    # Calculate relative overlap between candidate pair buildings
    pair_geom_existing = gdf1.loc[pairs["id_existing"]]
    pair_geom_new = gdf2.loc[pairs["id_new"]]
    pairs["overlap_pair_existing"] = spatial.pairwise_relative_overlap(pair_geom_existing, pair_geom_new)
    pairs["overlap_pair_new"] = spatial.pairwise_relative_overlap(pair_geom_new, pair_geom_existing)

    # Calculate relative overlap with other buildings only (apart from the candidate pair)
    pairs["overlap_others_existing"] = pairs["overlap_existing"] - pairs["overlap_pair_existing"]
    pairs["overlap_others_new"] = pairs["overlap_new"] - pairs["overlap_pair_new"]

    # Identify candidate pairs with low overlap with other buildings suggesting a one-to-one match
    mask = (pairs["overlap_others_existing"] < 0.2) & (pairs["overlap_others_new"] < 0.2)

    return pairs[mask][["id_existing", "id_new"]]


def _sample_candidate_pairs(
    pairs: DataFrame, n: int
) -> DataFrame:
    return pairs.sample(n=n, random_state=42).reset_index(drop=True)


def _sample_neighborhoods(
    pairs: DataFrame, gdf: GeoDataFrame, n: int
) -> DataFrame:
    """
    Sample candidate pairs from n neighborhoods, with selection weighted by the number of buildings per neighborhood.
    """
    neighborhoods = pairs["id_new"].map(gdf["neighborhood"])
    probs = neighborhoods.value_counts(normalize=True)
    samples = probs.sample(n=n, weights=probs, random_state=42)
    mask = neighborhoods.isin(samples.index)

    return pairs[mask]


def _indices_overlap(gdf1: GeoDataFrame, gdf2: GeoDataFrame) -> bool:
    return not gdf1.index.intersection(gdf2.index).empty
