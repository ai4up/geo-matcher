import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from eubucco_conflator import app
from eubucco_conflator.state import State

if __name__ == '__main__':

	file_path = '<path of parquet file>'
	# Initialize the state with your custom input file
	State.init(file_path,fix_id=None, logger=print)
	app.start()

