import warnings

import geopandas as gpd
import pandas as pd
import numpy as np

from eubucco_conflator.state import CANDIDATES_FILE

warnings.simplefilter(action='ignore', category=pd.errors.SettingWithCopyWarning)
warnings.simplefilter(action='ignore', category=FutureWarning)


def create_duplicate_candidates_dataset(gdf_path1, gdf_path2, ioa_range, dis):
    gdf1 = gpd.read_parquet(gdf_path1, columns=['geometry']).to_crs(3035)
    gdf2 = gpd.read_parquet(gdf_path2, columns=['geometry']).to_crs(3035)

    candidates = _identify_duplicate_candidates(gdf1, gdf2, ioa_range, dis)
    candidates.to_parquet(CANDIDATES_FILE)


def _identify_duplicate_candidates(gdf1, gdf2, ioa_range, dis):
    candidates = _identify_overlapping_buildings(gdf1, gdf2)
    candidates = candidates[(candidates['ioa'].between(*ioa_range))]

    gdf1_ngbh = _get_candidate_neighbors(candidates, gdf1, dis)
    gdf2_ngbh = _get_candidate_neighbors(candidates, gdf2, dis)

    gdf1_ngbh['dataset'] = 1
    gdf2_ngbh['dataset'] = 2

    return pd.concat([gdf1_ngbh, gdf2_ngbh])


def _identify_overlapping_buildings(gdf1, gdf2):
    # determine intersecting buildings
    int_idx2, int_idx1 = gdf1.sindex.query(gdf2.geometry, predicate='intersects')
    gdf2_int = gdf2.iloc[int_idx2]
    gdf1_int = gdf1.iloc[int_idx1]

    # assess degree of overlap
    gdf2_int['ioa'] = _intersection_to_area_ratio(gdf1_int, gdf2_int)
    gdf2_int = _keep_building_with_largest_intersection(gdf2_int)

    return gdf2_int


def _get_candidate_neighbors(candidates, neighborhood, dis):
    candidate_idx, neighbor_idx = neighborhood.sindex.query(candidates.geometry, predicate="dwithin", distance=dis)
    neighbors = neighborhood.iloc[neighbor_idx]
    neighbors['candidate_id'] = candidates.iloc[candidate_idx].index.values
    return neighbors


def _intersection_to_area_ratio(gdf1, gdf2):
    geoms1 = gdf1.geometry.reset_index()
    geoms2 = gdf2.geometry.reset_index()

    intersection = geoms1.intersection(geoms2).area
    area = np.minimum(geoms1.area, geoms2.area)

    return (intersection / area).values


def _keep_building_with_largest_intersection(gdf):
    gdf = gdf.sort_values("ioa", ascending=False)
    return gdf[~gdf.index.duplicated(keep="first")]
