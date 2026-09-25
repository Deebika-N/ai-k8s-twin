import os
import sys

sys.path.append(
    os.path.dirname(__file__)
)

from services.llm_service import (
    generate_environment_parameters
)

from services.environment_validator import (
    validate_environment_parameters
)

from services.chaos_template_service import (
    generate_chaos_yaml
)


application = {
    "kind": "Deployment",
    "name": "simulation-payment",
    "namespace": "simulation-resource",
    "replicas": 1,

    "containers": [
        {
            "name": "payment",
            "image": "nginx:latest",

            "ports": [
                {
                    "containerPort": 80
                }
            ],

            "resources": {
                "requests": {
                    "cpu": "100m",
                    "memory": "64Mi"
                },
                "limits": {
                    "cpu": "500m",
                    "memory": "128Mi"
                }
            }
        }
    ]
}


environments = [
    "CPU Stress",
    "Memory Stress",
    "Network Delay",
    "Network Loss"
]


cpu = "2 cores"
memory = "4Gi"
vus = 10
duration = 60


# --------------------------------
# Generate LLM parameters
# --------------------------------

result = generate_environment_parameters(
    application,
    environments,
    cpu,
    memory,
    vus,
    duration
)


# --------------------------------
# Validate
# --------------------------------

valid, message = validate_environment_parameters(
    result,
    application
)

print("\n===== VALIDATION =====")
print("Valid:", valid)
print("Message:", message)

if not valid:
    raise RuntimeError(message)


# --------------------------------
# Output directory
# --------------------------------

output_directory = os.path.join(
    os.path.dirname(__file__),
    "generated"
)

os.makedirs(
    output_directory,
    exist_ok=True
)


output_file = os.path.join(
    output_directory,
    "compound-chaos.yaml"
)


# --------------------------------
# Generate YAML documents
# --------------------------------

generated = result["environments"]

yaml_documents = []


for environment in environments:

    parameters = generated[environment].copy()


    if environment == "CPU Stress":

        parameters.update({
            "chaos_name": "compound-cpu-stress",
            "namespace": "simulation-resource",
            "mode": "one",
            "target_app": "simulation-payment",
            "workers": 1,
            "cpu_load": parameters.get("value"),
            "duration": f"{duration}s"
        })


    elif environment == "Memory Stress":

        parameters.update({
            "chaos_name": "compound-memory-stress",
            "namespace": "simulation-resource",
            "mode": "one",
            "target_app": "simulation-payment",
            "workers": 1,
            "memory_size": parameters.get("value"),
            "duration": f"{duration}s"
        })


    elif environment == "Network Delay":

        parameters.update({
            "chaos_name": "compound-network-delay",
            "namespace": "simulation-resource",
            "mode": "one",
            "target_app": "simulation-payment",
            "jitter": "10ms",
            "correlation": "0",
            "latency": parameters.get("value"),
            "duration": f"{duration}s"
        })


    elif environment == "Network Loss":

        parameters.update({
            "chaos_name": "compound-network-loss",
            "namespace": "simulation-resource",
            "mode": "one",
            "target_app": "simulation-payment",
            "correlation": "0",
            "loss_percent": parameters.get("value"),
            "duration": f"{duration}s"
        })


    yaml_content = generate_chaos_yaml(
        environment,
        parameters
    )

    yaml_documents.append(
        yaml_content.strip()
    )


# --------------------------------
# Save compound manifest
# --------------------------------

with open(
    output_file,
    "w",
    encoding="utf-8"
) as file:

    file.write(
        "\n---\n".join(
            yaml_documents
        )
    )


print("\n===== COMPOUND CHAOS FILE =====")
print(output_file)
