# Building Footprint Matching Tool

A web-based tool for matching and labeling building footprints from two datasets. Runs locally as a Flask app, displaying Folium-generated maps of potential matching pairs.


## Install
```bash
pip install git+https://github.com/ai4up/eubucco-conflation@main
```

## Usage
Step 1: Create a dataset of potential building pairs from two datasets:
```bash
conflator create-labeling-dataset dataset1.parquet dataset2.parquet
```

Step 2: Start browser-based labeling of building pairs:
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
The resulting dataset is locally stored as `candidate-pairs.pickle`. To initiate the browser-based labeling, run:
```bash
conflator label
```
![Example of Building Footprint Matching Tool](example.png)


## Dockerized approach
> Prerequisites
> * Ensure a dataset of candidate pairs (`candidate-pairs.pickle`) is present in the `data` directory.
> * For production deployment, set a [Flask session](https://flask.palletsprojects.com/en/stable/quickstart/#sessions) `SECRET_KEY` environment variable.

Serve the dockerized Flask app with an Nginx proxy at `0.0.0.0:80`:
```bash
docker-compose up
```

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
