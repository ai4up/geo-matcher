from pathlib import Path

from geo_matcher.state import State

class StateHandler:
    """
    Singleton-style handler for managing multiple State instances keyed by dataset name.
    """
    def __init__(self, data_path: str, annotation_redundancy: int, consensus_margin: int):
        p = Path(data_path).resolve()
        if p.is_file():
            self.data_dir = p.parent
            self.datasets = [p.stem]
        else:
            self.data_dir = p
            self.datasets = [f.stem for f in p.glob("*.pickle")]

        self.annotation_redundancy = annotation_redundancy
        self.consensus_margin = consensus_margin
        self._states: dict[str, State] = {}

    def register(self, dataset: str) -> None:
        """
        Create and register a State instance for the specified dataset.
        """
        data_path = self.data_dir / f"{dataset}.pickle"
        results_path = self.data_dir / f"labels-{dataset}.csv"

        self._states[dataset] = State(
            data_path=data_path,
            results_path=results_path,
            annotation_redundancy=self.annotation_redundancy,
            consensus_margin=self.consensus_margin,
        )

    def get(self, dataset: str) -> State:
        """
        Get the State instance for the specified dataset.
        """
        if dataset not in self._states:
            self.register(dataset)

        return self._states[dataset]
