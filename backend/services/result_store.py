"""Durable JSON artifacts for optimization runs."""

import json
from pathlib import Path
from typing import Any
from uuid import uuid4


class ResultStore:
    def __init__(self, root: str | Path = "backend/results") -> None:
        self.root = Path(root)

    def create_run(self) -> tuple[str, Path]:
        run_id = str(uuid4())
        run_directory = self.root / run_id
        run_directory.mkdir(parents=True, exist_ok=False)
        return run_id, run_directory

    @staticmethod
    def write_json(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")

    def write_input(self, run_directory: Path, value: Any) -> None:
        self.write_json(run_directory / "input.json", value)

    def write_iteration(self, run_directory: Path, iteration: int, value: Any) -> Path:
        path = run_directory / "iterations" / f"{iteration:03d}" / "iteration.json"
        self.write_json(path, value)
        return path

    def write_final(self, run_directory: Path, value: Any) -> None:
        self.write_json(run_directory / "final-result.json", value)