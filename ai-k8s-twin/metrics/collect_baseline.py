"""Collect one live baseline snapshot from the local Prometheus port-forward."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "environment-generator"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(GENERATOR))

from config import EnvironmentConfig  # noqa: E402
from metrics.prometheus_client import PrometheusClient  # noqa: E402
from metrics.features import baseline_result, collect_features  # noqa: E402


def main() -> None:
    with (GENERATOR / "generated" / "environment.json").open(encoding="utf-8") as file:
        configuration = EnvironmentConfig.from_mapping(json.load(file)).as_mapping()
    features = collect_features(PrometheusClient())
    result = baseline_result("baseline-local", configuration, features)
    print(json.dumps(result.as_mapping(), indent=2))


if __name__ == "__main__":
    main()