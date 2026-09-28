import os
import sys

sys.path.append(
    os.path.dirname(__file__)
)

from services.yaml_parser import (
    find_yaml_files,
    parse_yaml_file
)

from services.project_analyzer import (
    analyze_resources
)


repository_path = input(
    "Enter repository path: "
).strip()


# --------------------------------
# Find YAML files
# --------------------------------

yaml_files = find_yaml_files(
    repository_path
)


# --------------------------------
# Parse Kubernetes resources
# --------------------------------

all_resources = []

for file in yaml_files:

    resources = parse_yaml_file(
        file
    )

    all_resources.extend(
        resources
    )


# --------------------------------
# Analyze resources
# --------------------------------

applications = analyze_resources(
    all_resources
)


# --------------------------------
# Display results
# --------------------------------

print("\n===== APPLICATION ANALYSIS =====")


for app in applications:

    print("\nKind:", app["kind"])

    print(
        "Name:",
        app["name"]
    )

    print(
        "Namespace:",
        app["namespace"]
    )

    print(
        "File:",
        app["file"]
    )


    # --------------------------------
    # Deployment / StatefulSet / DaemonSet
    # --------------------------------

    if "replicas" in app:

        print(
            "Replicas:",
            app["replicas"]
        )

        print(
            "Selector:",
            app["selector"]
        )

        print(
            "Labels:",
            app["labels"]
        )

        print("Containers:")

        for container in app["containers"]:

            print(
                "  Name:",
                container["name"]
            )

            print(
                "  Image:",
                container["image"]
            )

            print(
                "  Ports:",
                container["ports"]
            )

            print(
                "  Resources:",
                container["resources"]
            )


    # --------------------------------
    # Service
    # --------------------------------

    if app["kind"] == "Service":

        print(
            "Selector:",
            app["selector"]
        )

        print(
            "Ports:",
            app["ports"]
        )
