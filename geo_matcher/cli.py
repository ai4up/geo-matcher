import click

from geo_matcher import app, dataset

DATASET_FILE = "candidate-pairs.pickle"
RESULTS_FILE = "."

@click.group()
def cli() -> None:
    pass


@cli.command()
@click.argument("path", default=DATASET_FILE, type=click.Path(exists=True))
@click.option(
    "--annotation-redundancy",
    "-r",
    default=0,
    type=int,
    help="Number of redundant annotations per building pair."
)
@click.option(
    "--consensus-margin",
    "-m",
    default=1,
    type=int,
    help="Minimum difference between match and no-match labels required to resolve a pair."
)
def label(path: str, annotation_redundancy: int, consensus_margin: int) -> None:
    """
    Start the labeling of building pairs.

    PATH to the dataset of building pairs.
    """
    click.echo("Starting browser app...")
    app.start_locally(path, annotation_redundancy, consensus_margin)


@cli.command()
@click.argument("filepath1", required=True, type=click.Path(exists=True))
@click.argument("filepath2", required=True, type=click.Path(exists=True))
@click.option(
    "--id-col",
    default=None,
    help="Name of the column containing unique building identifiers (default index).",
)
@click.option(
    "--min-intersection",
    "-l",
    default=0.0,
    help="Minimum relative overlap for new buildings to be included in labeling dataset [0,1).",  # noqa: E501
)
@click.option(
    "--max-intersection",
    "-u",
    default=1.0,
    help="Maximum relative overlap for new buildings to be included in labeling dataset (0,1].",  # noqa: E501
)
@click.option(
    "--min-similarity",
    default=0.0,
    help="Minimum shape similarity for a building pair to be included in labeling dataset [0,1).",  # noqa: E501
)
@click.option(
    "--max-similarity",
    default=1.0,
    help="Maximum shape similarity for a building pair to be included in labeling dataset (0,1].",  # noqa: E501
)
@click.option(
    "--max-distance",
    "-d",
    default=None,
    type=int,
    help="Maximum distance between a building pair to be included in labeling dataset [meters].",
)
@click.option(
    "--max-intersection-others",
    default=None,
    type=float,
    help="Maximum relative overlap of a candidate pair by other buildings to be included in labeling dataset (0,1].",  # noqa: E501
)
@click.option(
    "--sample-size",
    "-n",
    default=None,
    type=int,
    help="Sample n candidate pairs.",
)
@click.option(
    "--n-neighborhoods",
    default=None,
    type=int,
    help="Sample candidates in n neighborhoods.",
)
@click.option(
    "--h3-res",
    "-r",
    default=9,
    help="H3 resolution for neighborhood grouping [0-15].",
)
def create_labeling_dataset(
    filepath1: str,
    filepath2: str,
    id_col: str,
    min_intersection: float,
    max_intersection: float,
    min_similarity: float,
    max_similarity: float,
    max_distance: float,
    max_intersection_others: float,
    sample_size: int,
    n_neighborhoods: int,
    h3_res: int,
) -> None:
    """
    Create a dataset of building pairs to be labeled.

    FILEPATH1 to GeoParquet file containing the reference (existing) buildings.
    FILEPATH2 to GeoParquet file containing the new (to be added) buildings.
    """
    click.echo("Loading geodata...")
    dataset.log = click.echo
    dataset.create_candidate_pairs_dataset(
        gdf_path1=filepath1,
        gdf_path2=filepath2,
        id_col=id_col,
        overlap_range=(min_intersection, max_intersection) if min_intersection != 0 or max_intersection != 1 else None,
        similarity_range=(min_similarity, max_similarity) if min_similarity != 0 or max_similarity != 1 else None,
        max_distance=max_distance,
        max_overlap_others=max_intersection_others,
        n=sample_size,
        n_neighborhoods=n_neighborhoods,
        h3_res=h3_res,
    ).save(DATASET_FILE)
    click.echo(
        f"Dataset of candidate pairs created and stored in {DATASET_FILE}"
    )


if __name__ == "__main__":
    cli()
