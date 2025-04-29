import os
import sys

from geo_matcher.app import create_app

try:
    data_path = os.environ["DATA_PATH"]
    results_path = os.environ["RESULTS_PATH"]
    annotation_redundancy = int(os.environ["ANNOTATION_REDUNDANCY"])

except KeyError:
    print("Environment variables DATA_PATH, RESULTS_PATH, ANNOTATION_REDUNDANCY and must be set.", file=sys.stderr)
    sys.exit(1)
except ValueError:
    print("Environment variable ANNOTATION_REDUNDANCY must be an integer.", file=sys.stderr)
    sys.exit(1)

app = create_app(data_path, results_path, annotation_redundancy)
