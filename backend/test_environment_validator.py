import os
import sys

sys.path.append(
    os.path.dirname(__file__)
)

from services.environment_validator import (
    validate_environment_parameters
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


result = {
    "environments": {

        "CPU Stress": {
            "value": "200m"
        },

        "Memory Stress": {
            "value": "64Mi"
        },

        "Network Delay": {
            "value": "200ms"
        },

        "Network Loss": {
            "value": "5%"
        }
    }
}


valid, message = validate_environment_parameters(
    result,
    application
)


print("\n===== VALIDATION RESULT =====")

print("Valid:", valid)

print("Message:", message)
