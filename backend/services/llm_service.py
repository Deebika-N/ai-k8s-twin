import os
import json

from groq import Groq


ALLOWED_ENVIRONMENTS = {
    "Pod Kill",
    "CPU Stress",
    "Memory Stress",
    "Network Delay",
    "Network Loss",
    "Network Partition",
    "Node Failure"
}


def generate_environment_parameters(
    application,
    environments,
    cpu,
    memory,
    vus,
    duration
):

    # -----------------------------
    # Validate environments
    # -----------------------------

    if not isinstance(environments, list):
        raise ValueError(
            "Environments must be a list."
        )

    for environment in environments:

        if environment not in ALLOWED_ENVIRONMENTS:

            raise ValueError(
                f"Invalid environment: {environment}"
            )

    # -----------------------------
    # Get Groq API key
    # -----------------------------

    api_key = os.getenv(
        "GROQ_API_KEY"
    )

    if not api_key:

        raise RuntimeError(
            "GROQ_API_KEY is not set"
        )

    client = Groq(
        api_key=api_key
    )

    # -----------------------------
    # Prompt
    # -----------------------------

    prompt = f"""
You are a Kubernetes chaos engineering
parameter planner.

The chaos environments have ALREADY been selected.

You must NOT add, remove, or change
the selected environments.

Your task is ONLY to generate safe
parameters for each selected environment.

Selected environments:
{json.dumps(environments, indent=2)}

Application information:
{json.dumps(application, indent=2)}

User constraints:
CPU available: {cpu}
Memory available: {memory}
Virtual Users: {vus}
Duration: {duration} seconds


IMPORTANT RULES:

1. Do not choose environments.
2. Do not add new environments.
3. Do not remove selected environments.
4. Do not generate Kubernetes YAML.
5. Do not generate kubectl commands.
6. Generate parameters only.
7. Respect CPU and memory constraints.
8. Consider application container limits.
9. Consider application ports and protocol.
10. Keep parameters suitable for a controlled experiment.
11. Return ONLY valid JSON.
12. Do not use markdown.


PARAMETER REQUIREMENTS:


CPU Stress:
- Generate a CPU stress load percentage.
- Return ONLY a numeric value.
- The value must be between 1 and 100.
- Consider the application's CPU limit.
- Keep it suitable for a controlled experiment.
- Example: "60"
- Return:
  "value"


Memory Stress:
- Generate a memory stress amount.
- It must not exceed the application's memory limit.
- Use megabytes with a capital M.
- Do NOT use Mi, Gi, Ki, or other binary units.
- Example: "96M"
- Return:
  "value"


Network Delay:
- Generate a reasonable network delay.
- Use milliseconds.
- Example: "150ms"
- Return:
  "value"


Network Loss:
- Generate a reasonable packet loss value.
- Return ONLY the numeric value.
- Do NOT include the % symbol.
- The value must be greater than 0 and at most 100.
- Example: "3"
- Return:
  "value"


Pod Kill:
- Identify the appropriate application target.
- Return:
  "target"


Network Partition:
- Identify the appropriate application/network target.
- Return:
  "target"


Node Failure:
- Only return a node if node information
  is actually available.
- Never invent a node name.
- If node information is unavailable,
  return:
  "reason": "Node information required"


Return exactly this JSON structure:

{{
    "environments": {{}}
}}

Inside "environments", create one entry
for every selected environment.

Example:

{{
    "environments": {{
        "CPU Stress": {{
            "value": "60"
        }},
        "Memory Stress": {{
            "value": "96M"
        }},
        "Network Delay": {{
            "value": "150ms"
        }},
        "Network Loss": {{
            "value": "3"
        }}
    }}
}}
"""

    # -----------------------------
    # Call Groq
    # -----------------------------

    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",

        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ],

        temperature=0
    )

    content = (
        response
        .choices[0]
        .message
        .content
        .strip()
    )

    # -----------------------------
    # Parse JSON
    # -----------------------------

    try:

        result = json.loads(
            content
        )

    except json.JSONDecodeError:

        raise RuntimeError(
            "LLM returned invalid JSON:\n"
            + content
        )

    # -----------------------------
    # Validate response structure
    # -----------------------------

    if not isinstance(
        result,
        dict
    ):

        raise RuntimeError(
            "LLM result must be a JSON object."
        )

    generated = result.get(
        "environments"
    )

    if not isinstance(
        generated,
        dict
    ):

        raise RuntimeError(
            "LLM environments must be a JSON object."
        )

    # -----------------------------
    # Make sure LLM did not
    # change the selected environments
    # -----------------------------

    generated_names = set(
        generated.keys()
    )

    selected_names = set(
        environments
    )

    if generated_names != selected_names:

        raise RuntimeError(
            "LLM changed the selected environments."
        )

    return result
