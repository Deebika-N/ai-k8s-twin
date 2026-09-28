import os
import sys

sys.path.append(
    os.path.dirname(__file__)
)

from services.llm_service import (
    generate_environment_parameters
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


try:

    print(
        "\n===== CALLING GROQ ====="
    )

    result = generate_environment_parameters(
        application,
        environments,
        cpu,
        memory,
        vus,
        duration
    )

    print(
        "\n===== LLM RESULT ====="
    )

    print(result)


except Exception as e:

    print(
        "\nLLM test failed."
    )

    print(
        "Error:",
        e
    )     
