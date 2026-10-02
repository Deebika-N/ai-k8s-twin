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
    ) -> dict[str, Any]:
        payload = {
            "current_configuration": dict(current_configuration),
            "experiment_result": dict(result),
            "features": dict(features),
            "constraints": dict(constraints),
            "optimization_history": [dict(item) for item in history],
        }
        prompt = (
            "Return JSON only. Propose exactly one bounded Kubernetes resource configuration. "
            "Change only replicas, cpu_request, cpu_limit, memory_request, and memory_limit. "
            "Never return image, ports, probes, namespaces, services, commands, YAML, or kubectl. "
            "Use only the supplied experiment data.\n\n"
            + json.dumps(payload, indent=2)
            + '\n\nRequired schema: {"proposed_configuration": {}, "reasoning_summary": "", "targeted_problem": [], "expected_effect": []}'
        )
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        return parse_proposal(response.choices[0].message.content.strip(), current_configuration)