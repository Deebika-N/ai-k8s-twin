"""Strict Groq proposal client for bounded resource optimization."""

import json
import os
from typing import Any, Mapping

from services.optimizer import OptimizerError


ALLOWED_FIELDS = {"replicas", "cpu_request", "cpu_limit", "memory_request", "memory_limit"}


def parse_proposal(content: str, current_configuration: Mapping[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as error:
        raise OptimizerError("Groq returned invalid JSON") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("proposed_configuration"), dict):
        raise OptimizerError("Groq response is missing proposed_configuration")
    proposal = payload["proposed_configuration"]
    candidate = dict(current_configuration)
    candidate.update({key: value for key, value in proposal.items() if key in ALLOWED_FIELDS})
    if not any(key in proposal for key in ALLOWED_FIELDS):
        raise OptimizerError("Groq returned no supported configuration fields")
    return candidate


class GroqConfigurationProposalClient:
    def __init__(self, client: Any | None = None, model: str = "openai/gpt-oss-120b") -> None:
        if client is None:
            from groq import Groq

            client = Groq(api_key=os.environ["GROQ_API_KEY"])
        self.client = client
        self.model = model

    def propose(
        self,
        *,
        current_configuration: Mapping[str, Any],
        result: Mapping[str, Any],
        features: Mapping[str, Any],
        constraints: Mapping[str, Any],
        history: list[Mapping[str, Any]],
        constraint_evaluation: Mapping[str, Any],
        failed_constraints: list[Mapping[str, Any]],
        resource_score: float,
        bounds: Mapping[str, Any],
        proposal_goal: str = "ADDRESS_FAILED_CONSTRAINTS",
    ) -> dict[str, Any]:
        payload = {
            "current_configuration": dict(current_configuration),
            "experiment_result": dict(result),
            "features": dict(features),
            "constraints": dict(constraints),
            "constraint_evaluation": dict(constraint_evaluation),
            "failed_constraints": [dict(item) for item in failed_constraints],
            "resource_score": resource_score,
            "optimization_bounds": dict(bounds),
            "optimization_history": [dict(item) for item in history],
            "proposal_goal": proposal_goal,
        }
        prompt = (
            "You are optimizing a Kubernetes deployment configuration.\n\n"
            "Optimize ONLY these five parameters:\n"
            "- replicas\n"
            "- cpu_request\n"
            "- cpu_limit\n"
            "- memory_request\n"
            "- memory_limit\n\n"

            "The objective is to find the LOWEST-RESOURCE configuration "
            "that satisfies ALL defined constraints.\n\n"

            "You are given the current configuration, current experiment "
            "results, constraint evaluation, and the complete optimization history.\n\n"

            "When proposal_goal is MINIMIZE_RESOURCES, all hard constraints already pass. "
            "Explicitly propose a configuration with a strictly lower resource score "
            "than the current configuration, staying within optimization_bounds. "
            "Do not change a passing candidate to address a constraint that is already passing.\n"
            "When proposal_goal is ADDRESS_FAILED_CONSTRAINTS, propose a bounded configuration "
            "that addresses the listed failed constraints while preserving checks that pass.\n\n"
            "Use the complete history to:\n"
            "- understand what configurations were already tested;\n"
            "- never repeat a previously tested configuration;\n"
            "- identify which constraints are still failing;\n"
            "- avoid increasing resources when already-passing constraints "
            "do not require improvement;\n"
            "- target the remaining failed constraints;\n"
            "- preserve constraints that already pass;\n"
            "- avoid increasing resources unless prior experiment evidence supports "
            "improvement in a currently failed constraint;\n"
            "- prefer lower-resource configurations when constraints are satisfied.\n\n"

            "Do not assume that increasing resources always improves every metric. "
            "Use the experimental results from previous iterations as evidence.\n\n"

            "Only propose the five allowed resource fields. "
            "All other Kubernetes properties are immutable and controlled by the backend.\n"

            "Do not generate Kubernetes YAML or kubectl commands.\n"
            "Return only valid JSON using the required top-level "
            "proposed_configuration schema; place the allowed resource fields "
            "inside proposed_configuration.\n"

            + json.dumps(payload, indent=2)
            + '\n\nRequired schema: '
            '{"proposed_configuration": {}, '
            '"reasoning_summary": "", '
            '"targeted_problem": [], '
            '"expected_effect": []}'
        )
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        return parse_proposal(response.choices[0].message.content.strip(), current_configuration)
