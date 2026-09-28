import os
import sys

sys.path.append(
    os.path.dirname(__file__)
)

from services.chaos_template_service import (
    generate_chaos_yaml
)


parameters = {

    "chaos_name": "test-cpu-stress",

    "namespace": "simulation-resource",

    "mode": "one",

    "target_app": "simulation-payment",

    "workers": 1,

    "cpu_load": 50,

    "duration": "60s"
}


yaml_content = generate_chaos_yaml(
    "CPU Stress",
    parameters
)


print(
    "\n===== GENERATED CHAOS YAML =====\n"
)

print(
    yaml_content
)
