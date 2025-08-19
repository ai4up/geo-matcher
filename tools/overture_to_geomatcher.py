#!/usr/bin/env python3
from pathlib import Path

import click
import pandas as pd
import geopandas as gpd

from geo_matcher.candidate_pairs import CandidatePairs


@click.command()
@click.argument("input_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--feature-type",
    "-t",
    required=True,
    type=click.Choice(["places", "buildings"]),
    help="Feature type to use.",
)
def main(input_path: Path, feature_type: str) -> None:
    """
    Convert INPUT_PATH (Parquet file with base_* columns)
    into a CandidatePairs ZIP saved next to the input.
    """
    df = pd.read_parquet(input_path)

    if feature_type == "places":
        gdf_a, gdf_b, df_pairs = format_places_data(df)
    else:
        click.echo("Feature type 'buildings' is not yet supported.", err=True)
        return

    out_zip = input_path.with_suffix(".zip")
    CandidatePairs(gdf_a, gdf_b, df_pairs, feature_type).save(out_zip)
    click.echo(
        f"✅ Wrote {out_zip}  (feature_type={feature_type}, A={len(gdf_a):,}, B={len(gdf_b):,}, pairs={len(df_pairs):,})")


def format_places_data(df: pd.DataFrame) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, pd.DataFrame]:
    return _split_df(_ensure_consistent_columns(df))


def _ensure_consistent_columns(df: pd.DataFrame) -> pd.DataFrame:
    df[['neighborhood', 'base_neighborhood']] = None
    df["id_new"] = "B-" + df.groupby('id').ngroup().astype(str)
    df["id_existing"] = "A-" + df.groupby('base_id').ngroup().astype(str)

    return df


def _lat_lng_to_gdf(df: pd.DataFrame) -> gpd.GeoDataFrame:
    if not {"longitude", "latitude"}.issubset(df.columns):
        missing = {"longitude", "latitude"} - set(df.columns)
        raise click.UsageError(f"Missing required columns: {sorted(missing)}")

    return gpd.GeoDataFrame(
        df.drop(columns=["longitude", "latitude"]),
        geometry=gpd.points_from_xy(df["longitude"], df["latitude"]),
        crs="EPSG:4326",
    )


def _split_df(df: pd.DataFrame) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, pd.DataFrame]:
    data_cols = [c for c in df.columns if f"base_{c}" in df.columns]
    base_cols = [f"base_{c}" for c in data_cols]

    # Pairs
    df_pairs = df[["id_existing", "id_new"]]

    # A (baseline) – strip 'base_'
    df_a = df.set_index("id_existing")[base_cols] \
        .rename_axis(None) \
        .rename(columns=lambda x: x[5:]) \
        .drop_duplicates()

    gdf_a = _lat_lng_to_gdf(df_a)

    # B (candidate)
    df_b = df.set_index("id_new")[data_cols] \
        .rename_axis(None) \
        .drop_duplicates()

    gdf_b = _lat_lng_to_gdf(df_b)

    return gdf_a, gdf_b, df_pairs


if __name__ == "__main__":
    main()
