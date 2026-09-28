import os
import yaml


KUBERNETES_KINDS = {
    "Deployment",
    "StatefulSet",
    "DaemonSet",
    "Service",
    "ConfigMap",
    "Secret",
    "Ingress",
    "Namespace"
}


def find_yaml_files(project_path):
    yaml_files = []

    for root, dirs, files in os.walk(project_path):

        # Don't search inside .git
        if ".git" in dirs:
            dirs.remove(".git")

        for file in files:

            if file.endswith(".yaml") or file.endswith(".yml"):
                yaml_files.append(
                    os.path.join(root, file)
                )

    return yaml_files


def parse_yaml_file(file_path):

    resources = []

    with open(
        file_path,
        "r",
        encoding="utf-8"
    ) as file:

        try:

            documents = yaml.safe_load_all(file)

            for document in documents:

                if not isinstance(document, dict):
                    continue

                kind = document.get("kind")

                if kind not in KUBERNETES_KINDS:
                    continue

                resources.append({
                    "file": file_path,
                    "kind": kind,
                    "resource": document
                })

        except yaml.YAMLError:
            pass

    return resources
