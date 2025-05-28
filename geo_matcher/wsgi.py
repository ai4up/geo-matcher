import os
import sys

from geo_matcher.app import create_app

try:
    data_path = os.environ["DATA_PATH"]
    annotation_redundancy = int(os.environ["ANNOTATION_REDUNDANCY"])
    consensus_margin = int(os.environ["CONSENSUS_MARGIN"])

except KeyError:
    print("Environment variables DATA_PATH, ANNOTATION_REDUNDANCY and CONSENSUS_MARGIN must be set.", file=sys.stderr)
    sys.exit(1)
except ValueError:
    print("Environment variables ANNOTATION_REDUNDANCY and CONSENSUS_MARGIN must be an integer.", file=sys.stderr)
    sys.exit(1)

app = create_app(data_path, annotation_redundancy, consensus_margin)
