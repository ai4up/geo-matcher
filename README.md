# Building Footprint Matching Tool

A web-based tool for matching and labeling building footprints from two datasets. Runs locally as a Flask app, displaying Folium-generated maps of potential matching pairs.


## Install
```bash
pip install git+https://github.com/ai4up/eubucco-conflation@main
```

## Usage
Create a dataset of potential building pairs from two datasets:
```bash
conflator create-labeling-dataset dataset1.parquet dataset2.parquet
```

Start browser-based labeling of building pairs:
```bash
conflator label
```

## Demo
Create a dataset of potential matches of [government buildings](https://eubucco.com/data/) and [Microsoft buildings](https://github.com/microsoft/GlobalMLBuildingFootprints) for a small region in France that require manual labeling  using the [demo data](data/) in the repository. Include only buildings which overlap slightly (0-10%).
```bash
conflator create-labeling-dataset \
    --min-intersection=0.1 \ # Minimum relative overlap for new buildings to be included in labeling dataset [0,1)
    --max-intersection=0.2 \ # Maximum relative overlap for new buildings to be included in labeling dataset (0,1]
    data/demo-gov.parquet data/demo-microsoft.parquet
```
The resulting dataset is locally stored as `labeling-dataset.pickle`. To initiate the browser-based labeling, run:
```bash
conflator label
```
![Example of Building Footprint Matching Tool](example.png)


## Development

Install dev dependencies using [poetry](https://python-poetry.org/):
```bash
poetry install --only dev
```

Install git pre-commit hooks:
```bash
pre-commit install
```

Build from source:
```bash
poetry build
pip install dist/eubucco_conflator-*.whl
```
