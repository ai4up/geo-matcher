import click

from eubucco_conflator import app, dataset
from eubucco_conflator.state import State
from eubucco_conflator.labeling_dataset import DATASET_FILE


@click.group()
def cli() -> None:
    pass


@cli.command()
@click.argument("filepath", default="labeling-dataset.parquet", type=click.Path(exists=True))
def label(filepath: str) -> None:
    """
    Start labeling of building pairs.

    FILEPATH to GeoParquet file containing the building pairs to label.
    """
    State.init(filepath, logger=click.echo)
    click.echo(f"Loaded {len(State.data.candidate_pairs)} candidate pairs")
    click.echo(
        f"Loaded latest labeling state: {len(State.results)} buildings already labeled"
    )

    click.echo("Starting browser app...")
    app.start()


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
    "--sample-size",
    "-n",
    default=None,
    type=int,
    help="Sample n candidate pairs / candidate neighborhoods.",
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
    sample_size: int,
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
        ioa_range=(min_intersection, max_intersection),
        n=sample_size,
        h3_res=h3_res,
    )
    click.echo(
        f"Dataset of candidate pairs created and stored in {DATASET_FILE}"
    )


if __name__ == "__main__":
    cli()
