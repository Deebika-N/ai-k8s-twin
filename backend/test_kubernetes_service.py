import os
import sys

sys.path.append(
    os.path.dirname(__file__)
)

from services.kubernetes_service import (
    deploy_yaml,
    get_pods,
    get_deployments
)


yaml_file = input(
    "Enter Kubernetes YAML path: "
).strip()

namespace = input(
    "Enter namespace: "
).strip()


try:

    print("\n===== DEPLOYING APPLICATION =====")

    result = deploy_yaml(
        yaml_file,
        namespace
    )

    print(result)


    print("\n===== DEPLOYMENTS =====")

    deployments = get_deployments(
        namespace
    )

    print(deployments)


    print("\n===== PODS =====")

    pods = get_pods(
        namespace
    )

    print(pods)


except Exception as e:

    print("\nDeployment failed.")

    print(
        "Error:",
        e
    )
