from typing import Tuple, List

from pyproj import Transformer
from geopandas import GeoDataFrame
from shapely.geometry import LineString, Point, Polygon
import geopandas as gpd
import h3
import numpy as np
import pandas as pd


def intersection_to_area_ratio(gdf1: GeoDataFrame, gdf2: GeoDataFrame) -> np.ndarray:
    """
    Two-Way Area Overlap (TWAO) between buildings of two GeoDataFrames.
    """
    geoms1 = gdf1.geometry.reset_index()
    geoms2 = gdf2.geometry.reset_index()

    intersection = geoms1.intersection(geoms2).area
    area = np.minimum(geoms1.area, geoms2.area)

    return (intersection / area).values


def corresponding(gdf1: GeoDataFrame, gdf2: GeoDataFrame) -> np.array:
    """
    Estimate whether pairs of buildings match based on their intersection area.
    """
    ioa = intersection_to_area_ratio(gdf1, gdf2)
    return ioa > 0.1


def overlapping(
    gdf1: GeoDataFrame, gdf2: GeoDataFrame
) -> Tuple[pd.Index, pd.Index]:
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


def nearest_neighbor(gdf1: GeoDataFrame, gdf2: GeoDataFrame) -> Tuple[pd.Index, pd.Index]:
    """
    For each building in gdf1, find the nearest building in gdf2 and return its index.
    """
    idx1, idx2 = gdf2.sindex.nearest(gdf1.geometry, return_all=False, max_distance=None)

    return gdf1.index[idx1], gdf2.index[idx2]


def h3_index(gdf: gpd.GeoDataFrame, res: int) -> List[str]:
    """
    Generate H3 indexes for the geometries in a GeoDataFrame.
    """
    # H3 operations require a lat/lon point geometry
    centroids = gdf.centroid.to_crs("EPSG:4326")
    lngs = centroids.x
    lats = centroids.y
    h3_idx = [h3.latlng_to_cell(lat, lng, res) for lat, lng in zip(lats, lngs)]

    return h3_idx


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

    return gpd.GeoDataFrame(geometry=edges, crs=gdf1.crs)


def line_connects_two_polygons(line: LineString, poly1: Polygon, poly2: Polygon) -> bool:
    """
    Check if a line connects two polygons.
    """
    start = Point(line.coords[0])
    end = Point(line.coords[-1])
    return (poly1.contains(start) and poly2.contains(end)) or (poly1.contains(end) and poly2.contains(start))
