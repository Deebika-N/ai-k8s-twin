"""Collect and evaluate the current paymentservice baseline."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "environment-generator"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(GENERATOR))

from config import EnvironmentConfig  # noqa: E402
from constraints.engine import ConstraintRequirements, evaluate_constraints  # noqa: E402
from metrics.features import collect_features  # noqa: E402
from metrics.prometheus_client import PrometheusClient  # noqa: E402


def main() -> None:
    with (GENERATOR / "generated" / "environment.json").open(encoding="utf-8") as file:
        configuration = EnvironmentConfig.from_mapping(json.load(file)).as_mapping()
    metrics = collect_features(PrometheusClient())
    requirements = ConstraintRequirements(expected_replicas=configuration["replicas"])
    evaluation = evaluate_constraints(metrics, requirements)
    print(json.dumps({"metrics": metrics.as_mapping(), "constraints": evaluation.as_mapping()}, indent=2))


if __name__ == "__main__":
    main()