import os
import sys

from eubucco_conflator.app import create_app

try:
    data_path = os.environ["DATA_PATH"]
    results_path = os.environ["RESULTS_PATH"]

except KeyError:
    print("Environment variables DATA_PATH and RESULTS_PATH must be set.", file=sys.stderr)
    sys.exit(1)

app = create_app(data_path, results_path)
