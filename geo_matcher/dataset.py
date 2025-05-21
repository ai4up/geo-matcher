from typing import Tuple
import logging
import warnings

from geopandas import GeoDataFrame
from pandas import DataFrame
import geopandas as gpd
import numpy as np
import pandas as pd

from geo_matcher.candidate_pairs import CandidatePairs
from geo_matcher import spatial

warnings.simplefilter(action="ignore", category=pd.errors.SettingWithCopyWarning)
warnings.simplefilter(action="ignore", category=FutureWarning)

logger = logging.getLogger(__name__)
log = logger.info

def create_candidate_pairs_dataset(
    gdf1: GeoDataFrame = None,
    gdf2: GeoDataFrame = None,
    gdf_path1: str = None,
    gdf_path2: str = None,
    id_col: str = None,
    overlap_range: Tuple[float, float] = None,
    similarity_range: Tuple[float, float] = None,
    max_distance: float = None,
    max_overlap_others: float = None,
    n: int = None,
    n_neighborhoods: int = None,
    h3_res: int = 9,
) -> CandidatePairs:
    """
    Identifies pairs of potentially matching buildings from two datasets and stores them in a new dataset.
    Pairs are determined based on spatial proximity, topological overlap, and shape similarity.
    """
    cols = ["geometry", id_col] if id_col else ["geometry"]

    if gdf1 is None:
        gdf1 = gpd.read_parquet(gdf_path1, columns=cols).to_crs(3035)

    if gdf2 is None:
        gdf2 = gpd.read_parquet(gdf_path2, columns=cols).to_crs(3035)

    gdf1, gdf2 = _ensure_unique_index(gdf1, gdf2, id_col)

    gdf1["neighborhood"] = spatial.h3_index(gdf1, h3_res)
    gdf2["neighborhood"] = spatial.h3_index(gdf2, h3_res)

    if n_neighborhoods:
        neighborhoods = _sample_neighborhoods(gdf2, n_neighborhoods)
        pairs = _identify_candidate_pairs_in_neighborhoods(gdf1, gdf2, neighborhoods, max_distance)
        gdf1, gdf2 = _remove_non_candidates(pairs, gdf1, gdf2)
    else:
        pairs = _identify_candidate_pairs(gdf1, gdf2, max_distance)

    if max_distance is None:
        _verify_exhaustive_pairs(gdf1, gdf2, pairs)

    if overlap_range:
        pairs = _filter_candidate_pairs_by_overlap(pairs, gdf1, gdf2, overlap_range)

    if similarity_range:
        pairs = _filter_candidate_pairs_by_shape_similarity(pairs, gdf1, gdf2, similarity_range)

    if max_overlap_others:
        pairs = _filter_candidate_pairs_by_overlap_of_others(pairs, gdf1, gdf2, max_overlap_others)

    if n:
        pairs = _sample_candidate_pairs(pairs, n)
        gdf1, gdf2 = _drop_buildings_elsewhere(gdf1, gdf2, pairs)

    return CandidatePairs(
        dataset_a=gdf1,
        dataset_b=gdf2,
        pairs=pairs,
    )


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


def _verify_exhaustive_pairs(
    gdf1: GeoDataFrame, gdf2: GeoDataFrame, pairs: DataFrame
) -> None:
    """
    Raise an error if the candidate pairs do not cover all buildings in gdf1 and gdf2.
    """
    if len(gdf1) != len(pairs["id_existing"].unique()):
        raise ValueError(
            "Candidate pairs do not cover all buildings in gdf1. "
            f"Expected {len(gdf1)}, but got {pairs['id_existing'].nunique()}."
        )

    if len(gdf2) != len(pairs["id_new"].unique()):
        raise ValueError(
            "Candidate pairs do not cover all buildings in gdf2. "
            f"Expected {len(gdf2)}, but got {pairs['id_new'].nunique()}."
        )


def _drop_buildings_elsewhere(
    gdf1: GeoDataFrame, gdf2: GeoDataFrame, pairs: DataFrame
) -> Tuple[GeoDataFrame, GeoDataFrame]:
    """
    To reduce dataset size, remove buildings away from the candidate pairs.
    """
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
    Determine the best match for each building in gdf1 and gdf2 by identifying the largest overlap
    or, if none, the nearest building in the other GeoDataFrame.
    """
    # Determine pairs of overlapping buildings
    idx1, idx2 = _determine_overlapping_candidate_pairs(gdf1, gdf2)

    # For non-overlapping buildings, determine the respective nearest building
    gdf1_non_intersect = gdf1.drop(idx1)
    gdf2_non_intersect = gdf2.drop(idx2)

    if max_distance != 0 and len(gdf1) > 0 and len(gdf2) > 0:
        log(f"For {len(gdf1_non_intersect) / len(gdf1) * 100:.1f}% and {len(gdf2_non_intersect) / len(gdf2) * 100:.1f}% non-overlapping buildings in gdf1 and gdf2, respectively, find the nearest building in the other GeoDataFrame.")
        idx1_nearest_a, idx2_nearest_a = spatial.nearest_neighbor(gdf1_non_intersect, gdf2, max_distance)
        idx2_nearest_b, idx1_nearest_b = spatial.nearest_neighbor(gdf2_non_intersect, gdf1, max_distance)

        idx1 = np.concatenate([idx1, idx1_nearest_a, idx1_nearest_b])
        idx2 = np.concatenate([idx2, idx2_nearest_a, idx2_nearest_b])

    pairs = DataFrame({"id_existing": idx1, "id_new": idx2})

    # Drop duplicate pairs which were introduced because the two buildings are both nearest to each other
    pairs = pairs.drop_duplicates()

    return pairs


def _identify_candidate_pairs_in_neighborhoods(
    gdf1: GeoDataFrame, gdf2: GeoDataFrame, neighborhoods: np.ndarray, max_distance: float
) -> DataFrame:
    """
    Identify candidate pairs for a set of neighborhoods.

    The new building is required to be in the neighborhood, while the existing building
    can also be in a nearby neighborhood.
    """
    nearby_neighborhoods = spatial.h3_disk(neighborhoods, k=1)
    gdf1_w_neighbors = gdf1[gdf1["neighborhood"].isin(nearby_neighborhoods)]
    gdf2_w_neighbors = gdf2[gdf2["neighborhood"].isin(nearby_neighborhoods)]

    pairs = _identify_candidate_pairs(gdf1_w_neighbors, gdf2_w_neighbors, max_distance)
    pairs = _filter_candidate_pairs_by_neighborhood(pairs, gdf2, neighborhoods)

    return pairs


def _determine_overlapping_candidate_pairs(
    gdf1: GeoDataFrame, gdf2: GeoDataFrame, tolerance: float = 0.01
) -> Tuple[pd.Index, pd.Index]:
    """
    Identify candidate pairs of overlapping buildings footprints,
    retaining only the most overlapping pair for each building.
    """
    idx1, idx2 = spatial.overlapping(gdf1, gdf2)

    # Filter slightly overlapping buildings
    overlap = spatial.symmetrical_pairwise_relative_overlap(gdf1.loc[idx1], gdf2.loc[idx2])
    mask = overlap > tolerance

    pairs = pd.DataFrame({
        "idx1": idx1[mask],
        "idx2": idx2[mask],
        "overlap": overlap[mask]
    })

    # For n:m relationships, keep only pair with the largest overlap
    max_idx1 = pairs.sort_values("overlap", ascending=False).drop_duplicates(subset=["idx1"])
    # Ensure symmetry, meaning that for each building in both gdf1 and gdf2 the pair with the respective largest overlap is kept
    max_idx2 = pairs.sort_values("overlap", ascending=False).drop_duplicates(subset=["idx2"])
    max_pairs = pd.concat([max_idx1, max_idx2])[["idx1", "idx2"]].drop_duplicates()

    return pd.Index(max_pairs["idx1"]), pd.Index(max_pairs["idx2"])


def _filter_candidate_pairs_by_overlap(
        pairs: DataFrame, gdf1: GeoDataFrame, gdf2: GeoDataFrame, overlap_range: Tuple[float, float]
) -> DataFrame:
    """
    Filter candidate pairs based on their degree of overlap, i.e. their Two-Way Area Overlap (TWAO).
    """
    gdf1_can = gdf1.loc[pairs["id_existing"]]
    gdf2_can = gdf2.loc[pairs["id_new"]]

    overlap = spatial.symmetrical_pairwise_relative_overlap(gdf1_can, gdf2_can)
    mask = (overlap >= overlap_range[0]) & (overlap <= overlap_range[1])

    return pairs[mask]


def _filter_candidate_pairs_by_shape_similarity(
        candidate_pairs: DataFrame, gdf1: GeoDataFrame, gdf2: GeoDataFrame, similarity_range: Tuple[float, float]
) -> DataFrame:
    """
    Filter candidate pairs based on their shape similarity.
    """
    gdf1_can = gdf1.loc[candidate_pairs["id_existing"]]
    gdf2_can = gdf2.loc[candidate_pairs["id_new"]]

    similarity = spatial.shape_similarity(gdf1_can, gdf2_can)
    mask = ((similarity >= similarity_range[0]) & (similarity <= similarity_range[1])).values

    return candidate_pairs[mask]


def _filter_candidate_pairs_by_overlap_of_others(
    pairs: DataFrame, gdf1: GeoDataFrame, gdf2: GeoDataFrame, max_overlap_others: float
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
    mask = (pairs["overlap_others_existing"] < max_overlap_others) & (pairs["overlap_others_new"] < max_overlap_others)

    return pairs[mask][["id_existing", "id_new"]]


def _sample_candidate_pairs(
    pairs: DataFrame, n: int
) -> DataFrame:
    if n > len(pairs):
        log(f"Sampling size n ({n}) is larger than the number of candidate pairs ({len(pairs)}). Reducing n to {len(pairs)}.")
        n = len(pairs)

    sample = pairs.sample(n=n, random_state=42).reset_index(drop=True)

    return sample


def _sample_neighborhoods(
    gdf: GeoDataFrame, n: int
) -> np.ndarray:
    """
    Sample n neighborhoods, with selection weighted by the number of buildings per neighborhood.
    """
    probs = gdf["neighborhood"].value_counts(normalize=True)
    if n > len(probs):
        log(f"Sampling size n ({n}) is larger than the number of neighborhoods ({len(probs)}). Reducing n to {len(probs)}.")
        n = len(probs)

    nbh = probs.sample(n=n, weights=probs, random_state=42).index.values

    return nbh


def _remove_non_candidates(
    pairs: DataFrame, gdf1: GeoDataFrame, gdf2: GeoDataFrame
) -> Tuple[GeoDataFrame, GeoDataFrame]:
    """
    Remove buildings that are not candidate pairs.
    """
    gdf1 = gdf1.loc[pairs["id_existing"].unique()]
    gdf2 = gdf2.loc[pairs["id_new"].unique()]

    return gdf1, gdf2


def _filter_candidate_pairs_by_neighborhood(
    pairs: DataFrame, gdf: GeoDataFrame, neighborhoods: np.ndarray
) -> DataFrame:
    """
    Drop candidate pairs where the new building is not in a set of neighborhoods.
    """
    pair_nbh = pairs["id_new"].map(gdf["neighborhood"])
    pairs = pairs[pair_nbh.isin(neighborhoods)]

    return pairs


def _indices_overlap(gdf1: GeoDataFrame, gdf2: GeoDataFrame) -> bool:
    return not gdf1.index.intersection(gdf2.index).empty
