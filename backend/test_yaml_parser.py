import os
import sys

sys.path.append(
    os.path.dirname(
        os.path.dirname(__file__)
    )
)

from services.yaml_parser import (
    find_yaml_files,
    parse_yaml_file
)


repository_path = input(
    "Enter repository path: "
).strip()


yaml_files = find_yaml_files(
    repository_path
)

print("\nYAML files found:")

for file in yaml_files:
    print(" -", file)


print("\nKubernetes resources:")

for file in yaml_files:

    resources = parse_yaml_file(file)

    for resource in resources:

        print(
            f"\nKind: {resource['kind']}"
        )

        print(
            f"File: {resource['file']}"
        )

        metadata = resource[
            "resource"
        ].get("metadata", {})

        print(
            f"Name: {metadata.get('name')}"
        )
