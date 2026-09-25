def analyze_resources(resources):

    applications = []

    for item in resources:

        resource = item["resource"]

        kind = item["kind"]

        metadata = resource.get(
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

        application = {
            "kind": kind,
            "name": name,
            "namespace": namespace,
            "file": item["file"]
        }

        # --------------------------------
        # Deployment / StatefulSet / DaemonSet
        # --------------------------------

        if kind in {
            "Deployment",
            "StatefulSet",
            "DaemonSet"
        }:

            spec = resource.get(
                "spec",
                {}
            )

            application["replicas"] = spec.get(
                "replicas",
                1
            )

            application["selector"] = spec.get(
                "selector",
                {}
            )

            pod_template = spec.get(
                "template",
                {}
            )

            pod_metadata = pod_template.get(
                "metadata",
                {}
            )

            application["labels"] = pod_metadata.get(
                "labels",
                {}
            )

            pod_spec = pod_template.get(
                "spec",
                {}
            )

            containers = pod_spec.get(
                "containers",
                []
            )

            application["containers"] = []

            for container in containers:

                container_info = {
                    "name": container.get(
                        "name"
                    ),

                    "image": container.get(
                        "image"
                    ),

                    "ports": container.get(
                        "ports",
                        []
                    ),

                    "resources": container.get(
                        "resources",
                        {}
                    )
                }

                application[
                    "containers"
                ].append(
                    container_info
                )

        # --------------------------------
        # Service
        # --------------------------------

        elif kind == "Service":

            spec = resource.get(
                "spec",
                {}
            )

            application["selector"] = spec.get(
                "selector",
                {}
            )

            application["ports"] = spec.get(
                "ports",
                []
            )

        applications.append(
            application
        )

    return applications
