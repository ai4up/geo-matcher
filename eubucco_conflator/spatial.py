from typing import Tuple, List

from pyproj import Transformer
from geopandas import GeoDataFrame
from pandas import DataFrame, Series, Index, MultiIndex
from shapely.geometry import LineString, Point, Polygon
import h3
import numpy as np
import momepy


def relative_overlap(gdf1: GeoDataFrame, gdf2: GeoDataFrame) -> Series:
    """
    Calculate the share of each building's footprint overlapped by buildings in another GeoDataFrame.
    """
    geoms1 = gdf1.geometry
    geoms2 = gdf2.geometry

    idx1, idx2 = gdf2.sindex.query(geoms1, predicate="intersects")

    intersection = geoms1.iloc[idx1].intersection(geoms2.iloc[idx2], align=False)
    intersection_area = intersection.area.groupby(level=0).sum()
    area = geoms1.area.loc[intersection_area.index]

    return intersection_area / area


def pairwise_relative_overlap(gdf1: GeoDataFrame, gdf2: GeoDataFrame) -> np.ndarray:
    """
    Calculate the share of each building's footprint overlapped by its pair in another GeoDataFrame.
    """
    geoms1 = gdf1.geometry.reset_index()
    geoms2 = gdf2.geometry.reset_index()

    intersection_area = geoms1.intersection(geoms2).area
    area = geoms1.area

    return (intersection_area / area).values


def symmetrical_pairwise_relative_overlap(gdf1: GeoDataFrame, gdf2: GeoDataFrame) -> np.ndarray:
    """
    Calculate Two-Way Area Overlap (TWAO) between building pairs.
    """
    geoms1 = gdf1.geometry.reset_index()
    geoms2 = gdf2.geometry.reset_index()

    intersection_area = geoms1.intersection(geoms2).area
    area = np.minimum(geoms1.area, geoms2.area)

    return (intersection_area / area).values


def corresponding(gdf1: GeoDataFrame, gdf2: GeoDataFrame) -> np.array:
    """
    Estimate whether pairs of buildings match based on their intersection area.
    """
    overlap = symmetrical_pairwise_relative_overlap(gdf1, gdf2)

    return overlap > 0.1


def overlapping(
    gdf1: GeoDataFrame, gdf2: GeoDataFrame
) -> Tuple[Index, Index]:
    """
    Find all overlapping building pairs between two GeoDataFrames.
    """
    idx2, idx1 = gdf1.sindex.query(gdf2.geometry, predicate="intersects")

    return gdf1.index[idx1], gdf2.index[idx2]


def within(gdf: GeoDataFrame, loc: Point, dis: float) -> GeoDataFrame:
    """
    Find all buildings within a certain distance from a given point.
    """
    idx = gdf.sindex.query(loc, predicate="dwithin", distance=dis)

    return gdf.iloc[idx]


def nearest_neighbor(gdf1: GeoDataFrame, gdf2: GeoDataFrame, max_distance: float = None) -> Tuple[Index, Index]:
    """
    For each building in gdf1, find the nearest building in gdf2 and return its index.
    """
    idx1, idx2 = gdf2.sindex.nearest(gdf1.geometry, return_all=False, max_distance=max_distance)

    return gdf1.index[idx1], gdf2.index[idx2]


def h3_index(gdf: GeoDataFrame, res: int) -> List[str]:
    """
    Generate H3 indexes for the geometries in a GeoDataFrame.
    """
    # H3 operations require a lat/lon point geometry
    centroids = gdf.centroid.to_crs("EPSG:4326")
    lngs = centroids.x
    lats = centroids.y
    h3_idx = [h3.latlng_to_cell(lat, lng, res) for lat, lng in zip(lats, lngs)]

    return h3_idx


def h3_disk(h3_indices: List[str], k: int) -> List[str]:
    """
    Determines all nearby cells for a list of h3 indices within k grid distance.
    """
    return np.unique(np.ravel([h3.grid_disk(idx, k) for idx in h3_indices]))


def center_lat_lon(gdf: GeoDataFrame) -> Point:
    """
    Determine the longitude and latitude of the center of the total bounds of a GeoDataFrame.
    """
    minx, miny, maxx, maxy = gdf.total_bounds
    x = (minx + maxx) / 2
    y = (miny + maxy) / 2

    return to_lat_lon(x, y, gdf.crs)


def to_lat_lon(x: float, y: float, crs: str) -> Tuple[float, float]:
    """
    Convert coordinates to latitude and longitude.
    """
    transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    lon, lat = transformer.transform(x, y)

    return lat, lon


def connect_with_lines(gdf1: GeoDataFrame, gdf2: GeoDataFrame) -> GeoDataFrame:
    """
    Row-wise connect centroids of two GeoDataFrames with lines.
    """
    edges = [
        LineString([c1, c2])
        for c1, c2 in zip(gdf1.centroid, gdf2.centroid)
    ]

    return GeoDataFrame(geometry=edges, index=MultiIndex.from_tuples(zip(gdf1.index, gdf2.index)), crs=gdf1.crs)


def line_connects_two_polygons(line: LineString, poly1: Polygon, poly2: Polygon) -> bool:
    """
    Check if a line connects two polygons.
    """
    start = Point(line.coords[0])
    end = Point(line.coords[-1])
    return (poly1.contains(start) and poly2.contains(end)) or (poly1.contains(end) and poly2.contains(start))


def shape_similarity(gdf1: GeoDataFrame, gdf2: GeoDataFrame) -> Series:
    """
    Calculate the shape similarity between building pairs.
    The shape similarity of two polygons is defined as the average
    percentage difference across a selection of shape features.
    """
    fts1 = shape_characteristics(gdf1)
    fts2 = shape_characteristics(gdf2)
    similarity = 1 - _average_percentage_diff(fts1, fts2)

    return similarity


def shape_characteristics(gdf: GeoDataFrame) -> DataFrame:
    """
    Calculate shape characteristics of buildings in a GeoDataFrame, namely:
    - footprint area
    - longest axis length
    - elongation
    - orientation
    """
    orig_index = gdf.index
    gdf = gdf[~orig_index.duplicated()]

    fts = DataFrame(index=gdf.index)
    fts["bldg_footprint_area"] = gdf.area
    fts["bldg_longest_axis_length"] = momepy.longest_axis_length(gdf)
    fts["bldg_elongation"] = momepy.elongation(gdf)
    fts["bldg_orientation"] = momepy.orientation(gdf)

    return fts.loc[orig_index]


def _average_percentage_diff(gdf1: GeoDataFrame, gdf2: GeoDataFrame) -> Series:
    p_diff = (gdf1.values - gdf2.values) / gdf2.values
    avg_p_diff = np.abs(p_diff).mean(axis=1)

    return Series(avg_p_diff, index=MultiIndex.from_tuples(zip(gdf1.index, gdf2.index)))
