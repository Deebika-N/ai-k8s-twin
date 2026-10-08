"""Groq-backed optimizer for resource configuration candidates."""

import json
import os
from typing import Any, Mapping, Protocol

class OptimizerError(RuntimeError):
    """Raised when Groq cannot produce a usable candidate."""


class ChatClient(Protocol):
    def chat(self) -> Any: ...


class GroqConfigurationOptimizer:
    _mutable_fields = {"replicas", "cpu_request", "cpu_limit", "memory_request", "memory_limit"}

    def __init__(self, client: Any | None = None, model: str = "openai/gpt-oss-120b") -> None:
        if client is None:
            from groq import Groq

            self.client = Groq(api_key=os.environ["GROQ_API_KEY"])
        else:
            self.client = client
        self.model = model

    def propose(
        self,
        *,
        current_configuration: Mapping[str, Any],
        initial_configuration: Mapping[str, Any],
        requirements: Mapping[str, Any],
        selected_environments: list[str] | tuple[str, ...],
        chaos_parameters: Mapping[str, Any],
        metrics: Mapping[str, Any],
        evaluation: Mapping[str, Any],
        previous_configurations: list[Mapping[str, Any]],
    ) -> dict[str, Any]:
        payload = {
            "current_configuration": dict(current_configuration),
            "initial_configuration": dict(initial_configuration),
            "requirements": dict(requirements),
            "selected_environments": list(selected_environments),
            "chaos_parameters": dict(chaos_parameters),
            "metrics": dict(metrics),
            "constraint_evaluation": dict(evaluation),
            "previous_configurations": [dict(item) for item in previous_configurations],
        }
        prompt = (
            "Optimize only the Kubernetes resource settings in this JSON. "
            "Return one valid JSON object and no markdown. Preserve workload identity, "
            "image, namespace, container names, ports, and selectors. Change only "
            "replicas, cpu_request, cpu_limit, memory_request, and memory_limit. "
            "Satisfy the failed constraints without extreme resource increases.\n\n"
            + json.dumps(payload, indent=2)
        )
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        content = response.choices[0].message.content.strip()
        try:
            proposed = json.loads(content)
        except json.JSONDecodeError as error:
            raise OptimizerError("Groq returned invalid JSON") from error
        if not isinstance(proposed, dict):
            raise OptimizerError("Groq configuration must be a JSON object")
        unknown = set(proposed) - self._mutable_fields
        if unknown:
            raise OptimizerError(f"Groq returned unsupported fields: {sorted(unknown)}")
        candidate = dict(current_configuration)
        candidate.update(proposed)
        return candidate