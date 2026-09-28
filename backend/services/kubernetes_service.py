import os
import subprocess
import tempfile

import yaml


def run_kubectl(command):

    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:

        raise RuntimeError(
            result.stderr.strip()
            or "kubectl command failed."
        )

    return result.stdout.strip()


def deploy_yaml(yaml_file, namespace=None):

    command = [
        "kubectl",
        "apply",
        "-f",
        yaml_file
    ]

    if namespace:
        command.extend([
            "-n",
            namespace
        ])

    return run_kubectl(command)


def get_pods(namespace=None):

    command = [
        "kubectl",
        "get",
        "pods"
    ]

    if namespace:
        command.extend([
            "-n",
            namespace
        ])

    return run_kubectl(command)


def get_deployments(namespace=None):

    command = [
        "kubectl",
        "get",
        "deployments"
    ]

    if namespace:
        command.extend([
            "-n",
            namespace
        ])

    return run_kubectl(command)


def find_application_resource(application):

    file_path = application.get("file")

    if not file_path:

        raise RuntimeError(
            "Application YAML file path is missing."
        )

    if not os.path.isfile(file_path):

        raise FileNotFoundError(
            f"Application YAML file not found: {file_path}"
        )

    expected_kind = application.get("kind")
    expected_name = application.get("name")
    expected_namespace = application.get(
        "namespace",
        "default"
    )

    release_file = os.path.join(
        os.path.dirname(
            os.path.dirname(file_path)
        ),
        "release",
        "kubernetes-manifests.yaml"
    )

    files_to_check = [
        release_file,
        file_path
    ]

    for candidate_file in files_to_check:

        if not os.path.isfile(candidate_file):
            continue

        with open(
            candidate_file,
            "r",
            encoding="utf-8"
        ) as file:

            documents = yaml.safe_load_all(file)

            for document in documents:

                if not isinstance(
                    document,
                    dict
                ):
                    continue

                metadata = document.get(
                    "metadata",
                    {}
                )

                kind = document.get("kind")

                name = metadata.get("name")

                namespace = metadata.get(
                    "namespace",
                    "default"
                )

                if (
                    kind == expected_kind
                    and name == expected_name
                    and namespace == expected_namespace
                ):

                    return document

    raise RuntimeError(
        f"Could not find {expected_kind} "
        f"{expected_name} in repository manifests."
    )


def find_service_account(
    application,
    service_account_name
):

    file_path = application.get("file")

    if not file_path:

        raise RuntimeError(
            "Application YAML file path is missing."
        )

    release_file = os.path.join(
        os.path.dirname(
            os.path.dirname(file_path)
        ),
        "release",
        "kubernetes-manifests.yaml"
    )

    files_to_check = [
        release_file,
        file_path
    ]

    for candidate_file in files_to_check:

        if not os.path.isfile(candidate_file):
            continue

        with open(
            candidate_file,
            "r",
            encoding="utf-8"
        ) as file:

            documents = yaml.safe_load_all(file)

            for document in documents:

                if not isinstance(
                    document,
                    dict
                ):
                    continue

                if document.get(
                    "kind"
                ) != "ServiceAccount":

                    continue

                metadata = document.get(
                    "metadata",
                    {}
                )

                name = metadata.get(
                    "name"
                )

                namespace = metadata.get(
                    "namespace",
                    "default"
                )

                expected_namespace = application.get(
                    "namespace",
                    "default"
                )

                if (
                    name == service_account_name
                    and namespace == expected_namespace
                ):

                    return document

    return None


def deploy_application(application):

    name = application.get("name")

    kind = application.get("kind")

    namespace = application.get(
        "namespace",
        "default"
    )

    if kind not in {
        "Deployment",
        "StatefulSet",
        "DaemonSet"
    }:

        raise RuntimeError(
            f"Unsupported workload kind: {kind}"
        )

    print(
        f"\nDeploying {kind} {name} "
        f"to namespace {namespace}..."
    )

    resource = find_application_resource(
        application
    )

    pod_template = (
        resource
        .get("spec", {})
        .get("template", {})
    )

    pod_spec = pod_template.get(
        "spec",
        {}
    )

    service_account_name = pod_spec.get(
        "serviceAccountName"
    )

    documents = []

    if service_account_name:

        service_account = find_service_account(
            application,
            service_account_name
        )

        if service_account:

            documents.append(
                service_account
            )

            print(
                f"ServiceAccount found: "
                f"{service_account_name}"
            )

        else:

            raise RuntimeError(
                f"ServiceAccount "
                f"{service_account_name} "
                f"was not found in repository manifests."
            )

    if kind in {
        "Deployment",
        "StatefulSet"
    }:

        spec = resource.setdefault(
            "spec",
            {}
        )

        replicas = spec.get(
            "replicas",
            1
        )

        if replicas < 1:

            spec["replicas"] = 1

    documents.append(
        resource
    )

    temporary_file_path = None

    try:

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".yaml",
            delete=False,
            encoding="utf-8"
        ) as temporary_file:

            yaml.safe_dump_all(
                documents,
                temporary_file,
                sort_keys=False
            )

            temporary_file_path = (
                temporary_file.name
            )

        print(
            "\nApplying Kubernetes resources..."
        )

        apply_output = deploy_yaml(
            temporary_file_path
        )

        print(apply_output)

    finally:

        if (
            temporary_file_path
            and os.path.exists(
                temporary_file_path
            )
        ):

            os.remove(
                temporary_file_path
            )

    workload_type = kind.lower()

    print(
        f"\nWaiting for {kind} {name} "
        "to become ready..."
    )

    rollout_output = run_kubectl([
        "kubectl",
        "rollout",
        "status",
        f"{workload_type}/{name}",
        "-n",
        namespace,
        "--timeout=180s"
    ])

    print(rollout_output)

    print(
        f"\n{kind} {name} is ready."
    )

    return {
        "name": name,
        "kind": kind,
        "namespace": namespace,
        "status": "deployed_and_ready"
    }
