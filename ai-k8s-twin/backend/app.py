"""HTTP contract for the experiment system.

Long-running Kubernetes and Prometheus operations will be added behind these
endpoints. This first version intentionally keeps state in memory and does not
execute arbitrary commands received from API clients.
"""

import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "environment-generator"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(GENERATOR))

from config import EnvironmentConfig  # noqa: E402
from constraints.engine import ConstraintRequirements, evaluate_constraints  # noqa: E402
from metrics.features import MetricsFeatures  # noqa: E402
from backend.models import ConfigurationRequest, ExperimentRecord, RequirementsRequest  # noqa: E402


app = FastAPI(title="AI Kubernetes Twin", version="0.1.0")
configuration = ConfigurationRequest()
requirements = RequirementsRequest()
experiments: dict[str, ExperimentRecord] = {}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/config", response_model=ConfigurationRequest)
def get_config() -> ConfigurationRequest:
    return configuration


@app.post("/config", response_model=ConfigurationRequest)
def set_config(request: ConfigurationRequest) -> ConfigurationRequest:
    global configuration
    EnvironmentConfig.from_mapping(request.model_dump())
    configuration = request
    return configuration


@app.get("/requirements", response_model=RequirementsRequest)
def get_requirements() -> RequirementsRequest:
    return requirements


@app.post("/requirements", response_model=RequirementsRequest)
def set_requirements(request: RequirementsRequest) -> RequirementsRequest:
    global requirements
    requirements = request
    return requirements


@app.post("/experiments", response_model=ExperimentRecord, status_code=202)
def start_experiment(experiment: str = "baseline") -> ExperimentRecord:
    if experiment not in {"baseline", "cpu-stress", "memory-stress", "pod-kill", "traffic"}:
        raise HTTPException(status_code=400, detail="unsupported experiment")
    record = ExperimentRecord(run_id=str(uuid4()), experiment=experiment, status="QUEUED")
    experiments[record.run_id] = record
    return record


@app.get("/experiments", response_model=list[ExperimentRecord])
def list_experiments() -> list[ExperimentRecord]:
    return list(experiments.values())


@app.get("/experiments/{run_id}", response_model=ExperimentRecord)
def get_experiment(run_id: str) -> ExperimentRecord:
    record = experiments.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="experiment not found")
    return record


@app.post("/evaluate")
def evaluate(metrics: dict[str, Any]) -> dict[str, Any]:
    try:
        features = MetricsFeatures(**metrics)
        evaluated = evaluate_constraints(
            features,
            ConstraintRequirements(**requirements.model_dump()),
        )
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return evaluated.as_mapping()