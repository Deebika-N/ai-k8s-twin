import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import yaml
from git import Repo
from groq import Groq
from jinja2 import Environment, FileSystemLoader
from dotenv import load_dotenv


load_dotenv(
    os.path.join(
        os.path.dirname(
            os.path.dirname(__file__)
        ),
        ".env"
    )
)


REPOSITORY_DIRECTORY = os.path.expanduser(
    "~/ai-k8s-twin/temp_repository"
)

GENERATED_DIRECTORY = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "generated"
    )
)

CHAOS_DIRECTORY = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "chaos"
    )
)

ALLOWED_KINDS = {
    "Deployment",
    "StatefulSet",
    "DaemonSet"
}

ALLOWED_ENVIRONMENTS = {
    "Pod Kill",
    "CPU Stress",
    "Memory Stress",
    "Network Delay",
    "Network Loss",
    "Network Partition",
    "Node Failure"
}

FIVE_CHAOS_ENVIRONMENTS = [
    "CPU Stress",
    "Memory Stress",
    "Network Delay",
    "Network Loss",
    "Network Partition"
]

CHAOS_TEMPLATES = {
    "CPU Stress": "cpu-stress.yaml",
    "Memory Stress": "memory-stress.yaml",
    "Network Delay": "network-delay.yaml",
    "Network Loss": "network-packet-loss.yaml",
    "Network Partition": "network-partition.yaml",
    "Node Failure": "node-failure.yaml",
    "Pod Kill": "pod-kill.yaml"
}


def run_command(command):

    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:

        raise RuntimeError(
            result.stderr.strip()
            or "Command failed."
        )

    return result.stdout.strip()


def clone_repository(github_url):

    if os.path.exists(
        REPOSITORY_DIRECTORY
    ):

        print(
            "Removing previous temporary repository..."
        )

        shutil.rmtree(
            REPOSITORY_DIRECTORY
        )

    try:

        Repo.clone_from(
            github_url,
            REPOSITORY_DIRECTORY
        )

        return REPOSITORY_DIRECTORY

    except Exception:

        shutil.rmtree(
            REPOSITORY_DIRECTORY,
            ignore_errors=True
        )

        raise


def find_yaml_files(project_path):

    yaml_files = []

    for root, dirs, files in os.walk(
        project_path
    ):

        if ".git" in dirs:

            dirs.remove(".git")

        for file in files:

            if (
                file.endswith(".yaml")
                or file.endswith(".yml")
            ):

                yaml_files.append(
                    os.path.join(
                        root,
                        file
                    )
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

            documents = yaml.safe_load_all(
                file
            )

            for document in documents:

                if not isinstance(
                    document,
                    dict
                ):

                    continue

                kind = document.get(
                    "kind"
                )

                if kind not in {
                    "Deployment",
                    "StatefulSet",
                    "DaemonSet",
                    "Service",
                    "ConfigMap",
                    "Secret",
                    "Ingress",
                    "Namespace"
                }:

                    continue

                resources.append({
                    "file": file_path,
                    "kind": kind,
                    "resource": document
                })

        except yaml.YAMLError:

            pass

    return resources


def analyze_resources(resources):

    applications = []

    for item in resources:

        resource = item[
            "resource"
        ]

        kind = item[
            "kind"
        ]

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

        if kind in ALLOWED_KINDS:

            spec = resource.get(
                "spec",
                {}
            )

            application[
                "replicas"
            ] = spec.get(
                "replicas",
                1
            )

            application[
                "selector"
            ] = spec.get(
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

            application[
                "labels"
            ] = pod_metadata.get(
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

            application[
                "containers"
            ] = []

            for container in containers:

                application[
                    "containers"
                ].append({
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
                })

        elif kind == "Service":

            spec = resource.get(
                "spec",
                {}
            )

            application[
                "selector"
            ] = spec.get(
                "selector"
            )

            application[
                "ports"
            ] = spec.get(
                "ports",
                []
            )

        applications.append(
            application
        )

    return applications


def get_workloads(applications):

    workloads = []

    seen = set()

    for application in applications:

        if application.get(
            "kind"
        ) not in ALLOWED_KINDS:

            continue

        key = (
            application.get("kind"),
            application.get("namespace"),
            application.get("name")
        )

        if key in seen:

            continue

        seen.add(
            key
        )

        workloads.append(
            application
        )

    if not workloads:

        raise RuntimeError(
            "No application workload found."
        )

    return workloads


def find_application_resource(
    application
):

    file_path = application.get(
        "file"
    )

    if not file_path:

        raise RuntimeError(
            "Application YAML file path is missing."
        )

    if not os.path.isfile(
        file_path
    ):

        raise FileNotFoundError(
            f"Application YAML file not found: "
            f"{file_path}"
        )

    expected_kind = application.get(
        "kind"
    )

    expected_name = application.get(
        "name"
    )

    expected_namespace = application.get(
        "namespace",
        "default"
    )

    release_file = os.path.join(
        os.path.dirname(
            os.path.dirname(
                file_path
            )
        ),
        "release",
        "kubernetes-manifests.yaml"
    )

    files_to_check = [
        release_file,
        file_path
    ]

    for candidate_file in files_to_check:

        if not os.path.isfile(
            candidate_file
        ):

            continue

        with open(
            candidate_file,
            "r",
            encoding="utf-8"
        ) as file:

            documents = yaml.safe_load_all(
                file
            )

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

                kind = document.get(
                    "kind"
                )

                name = metadata.get(
                    "name"
                )

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


def deploy_application(
    application
):

    name = application.get(
        "name"
    )

    kind = application.get(
        "kind"
    )

    namespace = application.get(
        "namespace",
        "default"
    )

    if kind not in ALLOWED_KINDS:

        raise RuntimeError(
            f"Unsupported workload kind: {kind}"
        )

    print(
        f"\nDeploying {kind} {name} "
        f"to namespace {namespace}..."
    )

    resource = application.get("resource_override")
    if resource is None:
        resource = find_application_resource(
            application
        )
    else:
        resource = json.loads(json.dumps(resource))

    pod_spec = (
        resource
        .get("spec", {})
        .get("template", {})
        .get("spec", {})
    )

    for container in pod_spec.get(
        "containers",
        []
    ):

        for probe_name in [
            "readinessProbe",
            "livenessProbe"
        ]:

            probe = container.get(
                probe_name
            )

            if isinstance(
                probe,
                dict
            ) and (
                "grpc" in probe
                or "httpGet" in probe
                or "tcpSocket" in probe
            ):

                probe[
                    "timeoutSeconds"
                ] = max(
                    probe.get(
                        "timeoutSeconds",
                        1
                    ),
                    5
                )

    service_account_name = pod_spec.get(
        "serviceAccountName"
    )

    documents = []

    if service_account_name:

        print(
            f"Required ServiceAccount: "
            f"{service_account_name}"
        )

        file_path = application.get(
            "file"
        )

        release_file = os.path.join(
            os.path.dirname(
                os.path.dirname(
                    file_path
                )
            ),
            "release",
            "kubernetes-manifests.yaml"
        )

        service_account = None

        if os.path.isfile(
            release_file
        ):

            with open(
                release_file,
                "r",
                encoding="utf-8"
            ) as file:

                for document in yaml.safe_load_all(
                    file
                ):

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

                    sa_name = metadata.get(
                        "name"
                    )

                    sa_namespace = metadata.get(
                        "namespace",
                        "default"
                    )

                    if (
                        sa_name
                        == service_account_name
                        and sa_namespace
                        == namespace
                    ):

                        service_account = (
                            document
                        )

                        break

        if service_account is None:

            raise RuntimeError(
                f"ServiceAccount "
                f"{service_account_name} "
                f"was not found in "
                f"{release_file}"
            )

        documents.append(
            service_account
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

            spec[
                "replicas"
            ] = 1

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

        output = run_command([
            "kubectl",
            "apply",
            "-f",
            temporary_file_path
        ])

        if output:

            print(output)

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

    print(
        f"\nWaiting for {kind} {name}..."
    )

    run_command([
        "kubectl",
        "rollout",
        "status",
        f"{kind.lower()}/{name}",
        "-n",
        namespace,
        "--timeout=180s"
    ])

    print(
        f"{kind} {name} is ready."
    )

    return {
        "name": name,
        "kind": kind,
        "namespace": namespace,
        "status": "deployed_and_ready"
    }


def prepare_llm_application(
    application
):

    return {
        "kind": application.get(
            "kind"
        ),
        "name": application.get(
            "name"
        ),
        "namespace": application.get(
            "namespace"
        ),
        "replicas": application.get(
            "replicas",
            1
        ),
        "labels": application.get(
            "labels",
            {}
        ),
        "containers": application.get(
            "containers",
            []
        )
    }


def generate_environment_parameters(
    application,
    environments,
    cpu,
    memory,
    vus,
    duration
):

    client = Groq(
        api_key=os.environ.get(
            "GROQ_API_KEY"
        )
    )

    prompt = f"""
You are generating parameters for Kubernetes
chaos engineering experiments.

Application:
{json.dumps(application, indent=2)}
    duration,
    vus
Selected environments:
{json.dumps(environments, indent=2)}
        vus,
User available CPU:
{cpu}

User available memory:
{memory}

k6 VUs:
{vus}

Experiment duration:
{duration}

Rules:

1. Do not choose environments.
2. Do not add environments.
3. Do not remove environments.
4. Return exactly one parameter object for
   every selected environment.
5. Do not generate Kubernetes YAML.
6. Return parameters only.
7. Return valid JSON only.
8. CPU Stress value must be numeric from 1 to 100.
9. Memory Stress must use M as the unit.
10. Memory Stress must not exceed the application's
    memory limit when a memory limit exists.
11. Network Delay must use ms or s.
12. Network Loss must be numeric only.
13. Network Loss must be greater than 0 and at most 100.
14. Network Partition should return a simple value such as
    "1". The actual source and target applications are
    controlled by the system.
15. Pod Kill should return a simple value such as "1".
    The actual target application is controlled by the system.
16. Node Failure should return a simple value such as "1".
    The actual node is controlled by the system.
17. Consider the application's CPU, memory, ports,
    labels and containers.
18. Keep values reasonable for a Kubernetes
    chaos experiment.

Return exactly this structure:

{{
  "environments": {{
    "environment name": {{
      "value": "parameter"
    }}
  }}
}}
"""

    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0
    )

    content = (
        response.choices[0]
        .message
        .content
        .strip()
    )

    try:

        return json.loads(
            content
        )

    except json.JSONDecodeError:

        match = re.search(
            r"\{.*\}",
            content,
            re.DOTALL
        )

        if not match:

            raise RuntimeError(
                "LLM did not return valid JSON."
            )

        return json.loads(
            match.group(0)
        )


def parse_memory(value):

    if value is None:

        return None

    value = str(
        value
    ).strip()

    units = {
        "Ki": 1024,
        "Mi": 1024 ** 2,
        "Gi": 1024 ** 3,
        "K": 1000,
        "M": 1000 ** 2,
        "G": 1000 ** 3
    }

    for unit in sorted(
        units,
        key=len,
        reverse=True
    ):

        if value.endswith(
            unit
        ):

            try:

                number = float(
                    value[
                        :-len(unit)
                    ]
                )

                return (
                    number
                    * units[unit]
                )

            except ValueError:

                return None

    try:

        return float(
            value
        )

    except ValueError:

        return None


def parse_cpu(value):

    if value is None:

        return None

    value = str(
        value
    ).strip()

    if value.endswith("m"):

        try:

            return (
                float(
                    value[:-1]
                )
                / 1000
            )

        except ValueError:

            return None

    try:

        return float(
            value
        )

    except ValueError:

        return None


def validate_environment_parameters(
    result,
    application,
    selected_environments
):

    if not isinstance(
        result,
        dict
    ):

        return False, (
            "LLM result must be a dictionary."
        )

    environments = result.get(
        "environments"
    )

    if not isinstance(
        environments,
        dict
    ):

        return False, (
            "Missing environments object."
        )

    selected_set = set(
        selected_environments
    )

    returned_set = set(
        environments.keys()
    )

    if selected_set != returned_set:

        return False, (
            "LLM environments do not exactly "
            "match selected environments."
        )

    containers = application.get(
        "containers",
        []
    )

    if not containers:

        return False, (
            "Application has no containers."
        )

    container = containers[0]

    resources = container.get(
        "resources",
        {}
    )

    if not isinstance(
        resources,
        dict
    ):

        resources = {}

    limits = resources.get(
        "limits",
        {}
    )

    if not isinstance(
        limits,
        dict
    ):

        limits = {}

    cpu_limit = limits.get(
        "cpu"
    )

    memory_limit = limits.get(
        "memory"
    )

    for environment in selected_environments:

        parameters = environments.get(
            environment
        )

        if not isinstance(
            parameters,
            dict
        ):

            return False, (
                f"Parameters for {environment} "
                f"must be an object."
            )

        value = parameters.get(
            "value"
        )

        if value is None:

            return False, (
                f"Missing value for {environment}."
            )

        if environment == "CPU Stress":

            try:

                cpu_value = float(
                    value
                )

            except (
                ValueError,
                TypeError
            ):

                return False, (
                    "CPU Stress value must "
                    "be numeric."
                )

            if (
                cpu_value <= 0
                or cpu_value > 100
            ):

                return False, (
                    "CPU Stress must be "
                    "between 1 and 100."
                )

            if cpu_limit is not None:

                if parse_cpu(
                    cpu_limit
                ) is None:

                    return False, (
                        "Application CPU "
                        "limit is invalid."
                    )

        elif environment == "Memory Stress":

            memory_value = parse_memory(
                value
            )

            if memory_value is None:

                return False, (
                    "Memory Stress value "
                    "is invalid."
                )

            if memory_value <= 0:

                return False, (
                    "Memory Stress must be "
                    "greater than zero."
                )

            if memory_limit is not None:

                memory_limit_value = parse_memory(
                    memory_limit
                )

                if memory_limit_value is None:

                    return False, (
                        "Application memory "
                        "limit is invalid."
                    )

                if (
                    memory_value
                    > memory_limit_value
                ):

                    return False, (
                        "Memory Stress value "
                        "exceeds application "
                        "memory limit."
                    )

        elif environment == "Network Delay":

            match = re.fullmatch(
                r"(\d+)(ms|s)",
                str(value).strip()
            )

            if not match:

                return False, (
                    "Network Delay must "
                    "use ms or s."
                )

            delay_value = int(
                match.group(1)
            )

            unit = match.group(2)

            if unit == "s":

                delay_ms = (
                    delay_value * 1000
                )

            else:

                delay_ms = delay_value

            if delay_ms <= 0:

                return False, (
                    "Network Delay must "
                    "be greater than zero."
                )

            if delay_ms > 10000:

                return False, (
                    "Network Delay cannot "
                    "exceed 10 seconds."
                )

        elif environment == "Network Loss":

            try:

                loss_value = float(
                    value
                )

            except (
                ValueError,
                TypeError
            ):

                return False, (
                    "Network Loss must "
                    "be numeric."
                )

            if (
                loss_value <= 0
                or loss_value > 100
            ):

                return False, (
                    "Network Loss must be "
                    "greater than 0 and "
                    "at most 100."
                )

    return True, (
        "Environment parameters are valid."
    )


def create_template_environment():

    return Environment(
        loader=FileSystemLoader(
            CHAOS_DIRECTORY
        ),
        autoescape=False
    )


def generate_chaos_yaml(
    environment,
    parameters
):

    if environment not in CHAOS_TEMPLATES:

        raise ValueError(
            f"Unsupported environment: "
            f"{environment}"
        )

    template_environment = (
        create_template_environment()
    )

    template = template_environment.get_template(
        CHAOS_TEMPLATES[environment]
    )

    return template.render(
        **parameters
    )


def generate_compound_chaos_yaml(
    environments,
    parameters
):

    generated_files = []

    for environment in environments:

        if environment not in parameters:

            raise ValueError(
                f"Missing parameters for "
                f"{environment}"
            )

        yaml_content = generate_chaos_yaml(
            environment,
            parameters[environment]
        )

        generated_files.append({
            "environment": environment,
            "yaml": yaml_content
        })

    return generated_files


def get_target_app(
    application
):

    labels = application.get(
        "labels",
        {}
    )

    return labels.get(
        "app",
        application.get(
            "name"
        )
    )


def build_chaos_parameters(
    application,
    environments,
    parameters,
    duration
):

    namespace = application.get(
        "namespace",
        "default"
    )

    target_app = get_target_app(
        application
    )

    target_name = re.sub(
        r"[^a-z0-9-]+",
        "-",
        str(target_app).lower()
    ).strip("-")

    chaos_parameters = {}

    for environment in environments:

        value = parameters[
            "environments"
        ][environment].get(
            "value"
        )

        chaos_parameters[
            environment
        ] = {
            "chaos_name": (
                target_name
                + "-"
                + environment
                .lower()
                .replace(" ", "-")
                + "-experiment"
            ),
            "namespace": namespace,
            "mode": "one",
            "target_app": target_app,
            "source_app": target_app,
            "target_mode": "one",
            "workers": 1,
            "duration": duration
        }

        if environment == "CPU Stress":

            chaos_parameters[
                environment
            ][
                "cpu_load"
            ] = value

        elif environment == "Memory Stress":

            chaos_parameters[
                environment
            ][
                "memory_size"
            ] = value

        elif environment == "Network Delay":

            chaos_parameters[
                environment
            ][
                "latency"
            ] = value

            chaos_parameters[
                environment
            ][
                "jitter"
            ] = "10ms"

            chaos_parameters[
                environment
            ][
                "correlation"
            ] = "0"

        elif environment == "Network Loss":

            chaos_parameters[
                environment
            ][
                "loss_percent"
            ] = value

            chaos_parameters[
                environment
            ][
                "correlation"
            ] = "0"

        elif environment == "Network Partition":

            chaos_parameters[
                environment
            ][
                "source_app"
            ] = target_app

            chaos_parameters[
                environment
            ][
                "target_app"
            ] = target_app

        elif environment == "Pod Kill":

            chaos_parameters[
                environment
            ][
                "target_app"
            ] = target_app

        elif environment == "Node Failure":

            chaos_parameters[
                environment
            ][
                "node_name"
            ] = get_application_node(
                application
            )

    return chaos_parameters


def get_application_pod(
    application
):

    namespace = application.get(
        "namespace",
        "default"
    )

    target_app = get_target_app(
        application
    )

    try:

        output = run_command([
            "kubectl",
            "get",
            "pods",
            "-n",
            namespace,
            "-l",
            f"app={target_app}",
            "-o",
            "json"
        ])

        data = json.loads(
            output
        )

        items = data.get(
            "items",
            []
        )

        if not items:

            return None

        return items[0]

    except (
        RuntimeError,
        json.JSONDecodeError
    ):

        return None


def get_application_node(
    application
):

    pod = get_application_pod(
        application
    )

    if not pod:

        raise RuntimeError(
            "Could not find a running "
            "application pod."
        )

    node_name = (
        pod.get(
            "spec",
            {}
        ).get(
            "nodeName"
        )
    )

    if not node_name:

        raise RuntimeError(
            "Could not determine the "
            "application node."
        )

    return node_name


def get_node_count():

    output = run_command([
        "kubectl",
        "get",
        "nodes",
        "-o",
        "json"
    ])

    data = json.loads(
        output
    )

    return len(
        data.get(
            "items",
            []
        )
    )


def collect_result_metrics(
    application,
    recovery_result,
    k6_result=None
):

    namespace = application.get(
        "namespace",
        "default"
    )

    name = application.get(
        "name"
    )

    metrics = {
        "cpu_avg": None,
        "cpu_max": None,
        "memory_avg": None,
        "memory_max": None,
        "requests_per_second": None,
        "p95_latency_ms": None,
        "p99_latency_ms": None,
        "error_rate_percent": None,
        "pod_restarts": None,
        "available_replicas": None,
        "oom_killed": None,
        "pod_kill_recovered": (
            recovery_result.get("recovered")
            if recovery_result and recovery_result.get("environment") == "Pod Kill"
            else None
        ),
        "pod_kill_recovery_seconds": (
            recovery_result.get("pod_kill_recovery_seconds")
            if recovery_result
            else None
        ),
        "chaos_apply_seconds": (
            recovery_result.get("chaos_apply_seconds")
            if recovery_result
            else None
        ),
        "pod_disappearance_seconds": (
            recovery_result.get("pod_disappearance_seconds")
            if recovery_result
            else None
        ),
        "replacement_pod_ready_seconds": (
            recovery_result.get("replacement_pod_ready_seconds")
            if recovery_result
            else None
        ),
        "total_recovery_seconds": (
            recovery_result.get("total_recovery_seconds")
            if recovery_result
            else None
        ),
        "cpu_stress_duration_seconds": (
            recovery_result.get("stress_duration_seconds")
            if recovery_result and recovery_result.get("environment") == "CPU Stress"
            else None
        ),
        "cpu_cleanup_duration_seconds": (
            recovery_result.get("cleanup_duration_seconds")
            if recovery_result and recovery_result.get("environment") == "CPU Stress"
            else None
        ),
        "cpu_readiness_recovery_seconds": (
            recovery_result.get("readiness_recovery_seconds")
            if recovery_result and recovery_result.get("environment") == "CPU Stress"
            else None
        ),
        "memory_stress_duration_seconds": (
            recovery_result.get("stress_duration_seconds")
            if recovery_result and recovery_result.get("environment") == "Memory Stress"
            else None
        ),
        "memory_cleanup_duration_seconds": (
            recovery_result.get("cleanup_duration_seconds")
            if recovery_result and recovery_result.get("environment") == "Memory Stress"
            else None
        ),
        "memory_readiness_recovery_seconds": (
            recovery_result.get("readiness_recovery_seconds")
            if recovery_result and recovery_result.get("environment") == "Memory Stress"
            else None
        ),
        "collection_errors": [],
        "sources": {
            "cpu_avg": "kubectl top",
            "cpu_max": "kubectl top",
            "memory_avg": "kubectl top",
            "memory_max": "kubectl top",
            "requests_per_second": "k6 pending",
            "p95_latency_ms": "k6 pending",
            "p99_latency_ms": "k6 pending",
            "error_rate_percent": "k6 pending",
            "pod_restarts": "kubectl pod status",
            "available_replicas": "kubectl deployment status",
            "oom_killed": "kubectl pod status",
            "pod_kill_recovered": "Pod Kill recovery event observation",
            "pod_kill_recovery_seconds": "Pod Kill replacement readiness timer",
            "chaos_apply_seconds": "Pod Kill kubectl apply interval",
            "pod_disappearance_seconds": "Pod Kill target disappearance interval",
            "replacement_pod_ready_seconds": "Pod Kill replacement readiness interval",
            "total_recovery_seconds": "Pod Kill total recovery timer",
            "cpu_stress_duration_seconds": "CPU Stress active interval",
            "cpu_cleanup_duration_seconds": "CPU Stress Chaos cleanup interval",
            "cpu_readiness_recovery_seconds": "CPU Stress readiness timer",
            "memory_stress_duration_seconds": "Memory Stress active interval",
            "memory_cleanup_duration_seconds": "Memory Stress Chaos cleanup interval",
            "memory_readiness_recovery_seconds": "Memory Stress readiness timer",
        }
    }

    if k6_result and k6_result.get(
        "status"
    ) == "completed":

        metrics[
            "requests_per_second"
        ] = k6_result.get(
            "requests_per_second"
        )
        metrics[
            "p95_latency_ms"
        ] = k6_result.get(
            "p95_ms"
        )
        metrics[
            "p99_latency_ms"
        ] = k6_result.get(
            "p99_ms"
        )
        metrics[
            "error_rate_percent"
        ] = k6_result.get(
            "error_rate_percent"
        )
        metrics[
            "sources"
        ][
            "requests_per_second"
        ] = "k6"
        metrics[
            "sources"
        ][
            "p95_latency_ms"
        ] = "k6"
        metrics[
            "sources"
        ][
            "p99_latency_ms"
        ] = "k6"
        metrics[
            "sources"
        ][
            "error_rate_percent"
        ] = "k6"

    elif k6_result:

        reason = k6_result.get(
            "reason",
            "k6 did not complete"
        )

        for metric_name in [
            "requests_per_second",
            "p95_latency_ms",
            "p99_latency_ms",
            "error_rate_percent"
        ]:

            metrics[
                "sources"
            ][
                metric_name
            ] = "k6: " + reason

    try:

        deployment = json.loads(
            run_command([
                "kubectl",
                "get",
                "deployment",
                name,
                "-n",
                namespace,
                "-o",
                "json"
            ])
        )

        metrics[
            "available_replicas"
        ] = deployment.get(
            "status",
            {}
        ).get(
            "availableReplicas",
            0
        ) or 0

    except (
        RuntimeError,
        json.JSONDecodeError
    ):

        pass

    if metrics[
        "cpu_avg"
    ] is None:

        try:

            pod = get_application_pod(
                application
            )

            if pod:

                pod_name = pod.get(
                    "metadata",
                    {}
                ).get(
                    "name"
                )

                pod_uid = pod.get(
                    "metadata",
                    {}
                ).get(
                    "uid"
                )
                node_name = pod.get(
                    "spec",
                    {}
                ).get(
                    "nodeName"
                )

                stats = json.loads(
                    run_command([
                        "kubectl",
                        "get",
                        "--raw",
                        f"/api/v1/nodes/{node_name}/proxy/stats/summary"
                    ])
                )

                pod_stats = next(
                    (
                        item for item in stats.get("pods", [])
                        if (
                            item.get("podRef", {}).get("uid") == pod_uid
                            or (
                                item.get("podRef", {}).get("name") == pod_name
                                and item.get("podRef", {}).get("namespace") == namespace
                            )
                        )
                    ),
                    None,
                )
                if not pod_stats:
                    raise RuntimeError("Kubelet stats did not contain the selected pod.")
                containers = pod_stats.get("containers", [])
                if not containers:
                    raise RuntimeError("Kubelet stats did not contain a container.")
                container_stats = containers[0]
                cpu_usage = container_stats.get(
                    "cpu",
                    {}
                ).get(
                    "usageNanoCores"
                )
                memory_usage = container_stats.get(
                    "memory",
                    {}
                ).get(
                    "workingSetBytes"
                )

                if cpu_usage is None:

                    raise RuntimeError(
                        "Kubelet stats did not include CPU usage."
                    )

                if memory_usage is None:

                    raise RuntimeError(
                        "Kubelet stats did not include memory usage."
                    )

                time.sleep(1)
                metrics["cpu_avg"] = cpu_usage / 1000000000
                metrics["cpu_max"] = metrics["cpu_avg"]
                metrics["memory_avg"] = memory_usage
                metrics["memory_max"] = memory_usage
                metrics["sources"]["cpu_avg"] = "kubelet stats summary"
                metrics["sources"]["cpu_max"] = "kubelet stats summary"
                metrics["sources"]["memory_avg"] = "kubelet stats summary"
                metrics["sources"]["memory_max"] = "kubelet stats summary"

        except (
            RuntimeError,
            ValueError,
            IndexError,
            OSError,
            KeyError,
            json.JSONDecodeError
        ):

            metrics[
                "collection_errors"
            ].append(
                "Kubelet CPU/memory stats unavailable."
            )
            metrics["sources"]["cpu_avg"] = "kubelet stats unavailable"
            metrics["sources"]["cpu_max"] = "kubelet stats unavailable"
            metrics["sources"]["memory_avg"] = "kubelet stats unavailable"
            metrics["sources"]["memory_max"] = "kubelet stats unavailable"

    try:

        pod_data = json.loads(
            run_command([
                "kubectl",
                "get",
                "pods",
                "-n",
                namespace,
                "-l",
                f"app={get_target_app(application)}",
                "-o",
                "json"
            ])
        )

        pod_items = pod_data.get("items", [])
        metrics["pod_restarts"] = 0
        metrics["oom_killed"] = False
        candidate_containers = {
            container.get("name"): container
            for container in application.get("containers", [])
        }
        candidate_container_observed = False

        for pod in pod_items:
            statuses = pod.get(
                "status",
                {}
            ).get(
                "containerStatuses",
                []
            )
            pod_containers = {
                container.get("name"): container
                for container in pod.get("spec", {}).get("containers", [])
            }

            for status in statuses:
                candidate_container = candidate_containers.get(status.get("name"))
                pod_container = pod_containers.get(status.get("name"))
                if (
                    candidate_container is None
                    or pod_container is None
                    or not pod_container_matches_candidate(pod_container, candidate_container)
                ):
                    continue
                candidate_container_observed = True

                metrics[
                    "pod_restarts"
                ] += status.get(
                    "restartCount",
                    0
                )

                if container_was_oom_killed(status):

                    metrics[
                        "oom_killed"
                    ] = True

        if not candidate_container_observed:
            metrics["pod_restarts"] = None
            metrics["oom_killed"] = None

    except (
        RuntimeError,
        json.JSONDecodeError
    ):

        pass

    try:

        top = run_command([
            "kubectl",
            "top",
            "pods",
            "-n",
            namespace,
            "-l",
            f"app={get_target_app(application)}",
            "--no-headers"
        ])

        cpu_values = []
        memory_values = []

        for line in top.splitlines():

            columns = line.split()

            if len(columns) < 3:

                continue

            cpu_values.append(
                parse_cpu(columns[1])
            )
            memory_values.append(
                parse_memory(columns[2])
            )

        cpu_values = [
            value for value in cpu_values
            if value is not None
        ]
        memory_values = [
            value for value in memory_values
            if value is not None
        ]

        if cpu_values:

            metrics["cpu_avg"] = sum(
                cpu_values
            ) / len(cpu_values)
            metrics["cpu_max"] = max(
                cpu_values
            )

        if memory_values:

            metrics["memory_avg"] = sum(
                memory_values
            ) / len(memory_values)
            metrics["memory_max"] = max(
                memory_values
            )

    except RuntimeError as error:

        metrics[
            "collection_errors"
        ].append(
            "kubectl top unavailable: " + str(error)
        )

    return metrics


def container_was_oom_killed(container_status):
    """Check both a current termination and the most recent container termination."""
    current_state = container_status.get("state") or {}
    last_state = container_status.get("lastState") or {}
    return any(
        state.get("reason") == "OOMKilled"
        for state in (
            current_state.get("terminated", {}),
            last_state.get("terminated", {}),
        )
    )


def pod_container_matches_candidate(pod_container, candidate_container):
    """Ignore stale ReplicaSet pods whose resource settings differ from this run."""
    if pod_container.get("name") != candidate_container.get("name"):
        return False
    expected = candidate_container.get("resources", {})
    if not expected:
        return True
    actual = pod_container.get("resources", {})
    from services.optimization_policy import cpu_millicores, memory_bytes

    parsers = {"cpu": cpu_millicores, "memory": memory_bytes}
    for group in ("requests", "limits"):
        expected_group = expected.get(group, {})
        actual_group = actual.get(group, {})
        for resource in ("cpu", "memory"):
            if resource in expected_group:
                try:
                    if parsers[resource](actual_group.get(resource)) != parsers[resource](expected_group[resource]):
                        return False
                except (TypeError, ValueError):
                    return False
    return True


def scale_application(
    application,
    replicas
):

    kind = application.get(
        "kind"
    )

    name = application.get(
        "name"
    )

    namespace = application.get(
        "namespace",
        "default"
    )

    if kind not in {
        "Deployment",
        "StatefulSet"
    }:

        return

    run_command([
        "kubectl",
        "scale",
        kind.lower(),
        name,
        "--replicas",
        str(replicas),
        "-n",
        namespace
    ])


def cleanup_application(
    application
):

    try:

        replicas = application.get(
            "replicas",
            1
        )

        if replicas < 1:

            replicas = 1

        scale_application(
            application,
            replicas
        )

        print(
            f"Restored {application.get('name')} "
            f"to {replicas} replicas."
        )

    except RuntimeError as error:

        print(
            f"Application cleanup warning: {error}"
        )


def write_yaml_file(
    filename,
    generated_chaos
):

    os.makedirs(
        GENERATED_DIRECTORY,
        exist_ok=True
    )

    with open(
        filename,
        "w",
        encoding="utf-8"
    ) as file:

        for index, item in enumerate(
            generated_chaos
        ):

            file.write(
                item["yaml"]
            )

            if index < (
                len(generated_chaos) - 1
            ):

                file.write(
                    "\n---\n"
                )


def parse_duration(
    duration
):

    duration = str(
        duration
    ).strip()

    if duration.endswith("s"):

        return float(
            duration[:-1]
        )

    if duration.endswith("m"):

        return (
            float(
                duration[:-1]
            )
            * 60
        )

    raise ValueError(
        "Duration must use s or m."
    )


def analyze_k6_results(
    filename,
    duration_metric="http_req_duration",
    failure_metric="http_req_failed"
):

    latencies = []
    requests = 0
    failed_requests = 0

    with open(
        filename,
        "r",
        encoding="utf-8"
    ) as file:

        for line in file:

            try:

                data = json.loads(line)

            except json.JSONDecodeError:

                continue

            if (
                data.get("type") == "Point"
                and data.get("metric") == duration_metric
            ):

                duration_value = data["data"]["value"]
                requests += 1

                if duration_value > 0:

                    latencies.append(
                        duration_value
                    )

            if (
                data.get("type") == "Point"
                and data.get("metric") == failure_metric
                and data["data"]["value"] == 1
            ):

                failed_requests += 1

    latencies.sort()

    def percentile(
        values,
        percentage
    ):

        index = (
            (len(values) - 1)
            * percentage
            / 100
        )
        lower = int(index)
        upper = min(
            lower + 1,
            len(values) - 1
        )
        weight = index - lower

        return values[lower] + (
            values[upper] - values[lower]
        ) * weight

    return {
        "requests": requests,
        "failed_requests": failed_requests,
        "error_rate_percent": (
            failed_requests / requests * 100
            if requests
            else 0
        ),
        "p95_ms": percentile(latencies, 95)
        if latencies
        else None,
        "p99_ms": percentile(latencies, 99)
        if latencies
        else None
    }


def run_k6_http(
    application,
    vus,
    duration
):

    containers = application.get(
        "containers",
        []
    )

    ports = (
        containers[0].get("ports", [])
        if containers
        else []
    )

    http_port = next(
        (
            port.get("containerPort")
            for port in ports
            if port.get("containerPort") in {80, 8080}
        ),
        None
    )

    if http_port is None:

        return {
            "status": "skipped",
            "reason": "Workload does not expose an HTTP port.",
            "source": "k6"
        }

    name = application.get(
        "name"
    )

    namespace = application.get(
        "namespace",
        "default"
    )

    duration_seconds = parse_duration(
        duration
    )

    temporary_script = None
    output_file = None
    port_forward = None

    try:

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".js",
            delete=False,
            encoding="utf-8"
        ) as script_file:

            script_file.write(
                "import http from 'k6/http';\n"
                "export const options = { vus: "
                + str(vus)
                + ", duration: '"
                + str(duration)
                + "' };\n"
                "export default function () { "
                "http.get('http://127.0.0.1:18080'); }\n"
            )
            temporary_script = script_file.name

        output_file = tempfile.NamedTemporaryFile(
            suffix=".json",
            delete=False
        ).name

        port_forward = subprocess.Popen([
            "kubectl",
            "port-forward",
            f"deployment/{name}",
            f"18080:{http_port}",
            "-n",
            namespace
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        time.sleep(2)

        result = subprocess.run([
            "k6",
            "run",
            "--out",
            f"json={output_file}",
            temporary_script
        ], capture_output=True, text=True, timeout=duration_seconds + 30)

        if result.returncode != 0:

            return {
                "status": "failed",
                "reason": result.stderr.strip()
                or "k6 execution failed.",
                "source": "k6"
            }

        parsed = analyze_k6_results(
            output_file
        )
        parsed[
            "requests_per_second"
        ] = parsed[
            "requests"
        ] / duration_seconds
        parsed[
            "status"
        ] = "completed"
        parsed[
            "source"
        ] = "k6"
        return parsed

    except (
        OSError,
        RuntimeError,
        subprocess.TimeoutExpired
    ) as error:

        return {
            "status": "failed",
            "reason": str(error),
            "source": "k6"
        }

    finally:

        if port_forward:

            port_forward.terminate()

        for path in [
            temporary_script,
            output_file
        ]:

            if path and os.path.exists(path):

                os.remove(path)


def run_k6_grpc(
    application,
    vus,
    duration
):

    grpc_workloads = {
        "currencyservice": {
            "port": 7000,
            "method": "CurrencyService/Convert",
            "request": (
                "{from: { currencyCode: 'USD', units: 1, nanos: 0 }, "
                "toCode: 'EUR'}"
            )
        },
        "productcatalogservice": {
            "port": 3550,
            "method": "ProductCatalogService/ListProducts",
            "request": "{}"
        },
        "adservice": {
            "port": 9555,
            "method": "AdService/GetAds",
            "request": "{contextKeys: ['clothing']}"
        },
        "shippingservice": {
            "port": 50051,
            "method": "ShippingService/GetQuote",
            "request": (
                "{address: {streetAddress: '1 Main St', city: 'New York', "
                "state: 'NY', country: 'US', zipCode: 10001}, items: []}"
            )
        }
    }

    workload = grpc_workloads.get(
        application.get("name")
    )

    if workload is None:

        return {
            "status": "skipped",
            "reason": "No gRPC k6 contract is configured for this workload.",
            "source": "k6"
        }

    proto_file = os.path.join(
        REPOSITORY_DIRECTORY,
        "protos",
        "demo.proto"
    )

    if not os.path.isfile(proto_file):

        return {
            "status": "skipped",
            "reason": "The workload gRPC proto was not found.",
            "source": "k6"
        }

    namespace = application.get(
        "namespace",
        "default"
    )
    duration_seconds = parse_duration(duration)
    script_file = None
    output_file = None
    port_forward = None

    try:

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".js",
            delete=False,
            encoding="utf-8"
        ) as file:

            file.write(
                "import grpc from 'k6/net/grpc';\n"
                "export const options = { vus: "
                + str(vus)
                + ", duration: '"
                + str(duration)
                + "' };\n"
                "const client = new grpc.Client();\n"
                "client.load(['"
                + os.path.dirname(proto_file)
                + "'], 'demo.proto');\n"
                "export default function () {\n"
                "  client.connect('127.0.0.1:17000', { plaintext: true });\n"
                "  client.invoke('hipstershop."
                + workload["method"]
                + "', "
                + workload["request"]
                + ");\n"
                "  client.close();\n"
                "}\n"
            )
            script_file = file.name

        output_file = tempfile.NamedTemporaryFile(
            suffix=".json",
            delete=False
        ).name

        port_forward = subprocess.Popen([
            "kubectl",
            "port-forward",
            f"deployment/{application.get('name')}",
            f"17000:{workload['port']}",
            "-n",
            namespace
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        time.sleep(2)

        result = subprocess.run([
            "k6",
            "run",
            "--out",
            f"json={output_file}",
            script_file
        ], capture_output=True, text=True, timeout=duration_seconds + 30)

        if result.returncode != 0:

            return {
                "status": "failed",
                "reason": result.stderr.strip()
                or "gRPC k6 execution failed.",
                "source": "k6"
            }

        parsed = analyze_k6_results(
            output_file,
            "grpc_req_duration",
            "grpc_req_failed"
        )
        parsed["requests_per_second"] = (
            parsed["requests"] / duration_seconds
        )
        parsed["status"] = "completed"
        parsed["source"] = "k6"
        return parsed

    except (
        OSError,
        RuntimeError,
        subprocess.TimeoutExpired
    ) as error:

        return {
            "status": "failed",
            "reason": str(error),
            "source": "k6"
        }

    finally:

        if port_forward:

            port_forward.terminate()

        for path in [script_file, output_file]:

            if path and os.path.exists(path):

                os.remove(path)


def apply_chaos_file(
    yaml_file
):

    print(
        f"\nApplying: {yaml_file}"
    )

    output = run_command([
        "kubectl",
        "apply",
        "-f",
        yaml_file
    ])

    print(output)

    return output


def cleanup_chaos(
    namespace,
    resource_types
):

    print(
        "\nCleaning up Chaos Mesh resources..."
    )

    for resource_type in resource_types:

        try:

            run_command([
                "kubectl",
                "delete",
                resource_type,
                "--all",
                "-n",
                namespace,
                "--ignore-not-found",
                "--wait=false"
            ])

        except RuntimeError as error:

            print(
                f"Cleanup warning: {error}"
            )

    print(
        "Chaos cleanup completed."
    )


def wait_for_application_recovery(
    application,
    timeout=120
):

    namespace = application.get(
        "namespace",
        "default"
    )

    target_app = get_target_app(
        application
    )

    print(
        "\nWaiting for application recovery..."
    )

    deadline = (
        time.time()
        + timeout
    )

    while time.time() < deadline:

        try:

            output = run_command([
                "kubectl",
                "get",
                "pods",
                "-n",
                namespace,
                "-l",
                f"app={target_app}",
                "-o",
                "json"
            ])

            data = json.loads(
                output
            )

            for pod in data.get(
                "items",
                []
            ):

                phase = (
                    pod.get(
                        "status",
                        {}
                    ).get(
                        "phase"
                    )
                )

                statuses = (
                    pod.get(
                        "status",
                        {}
                    ).get(
                        "containerStatuses",
                        []
                    )
                )

                ready = any(
                    status.get(
                        "ready",
                        False
                    )
                    for status in statuses
                )

                if (
                    phase == "Running"
                    and ready
                ):

                    print(
                        "Application recovered."
                    )

                    return True

        except (
            RuntimeError,
            json.JSONDecodeError
        ):

            pass

        time.sleep(
            3
        )

    print(
        "Application recovery was not "
        "confirmed within the timeout."
    )

    return False


def wait_for_pod_kill_recovery(
    application,
    killed_pod,
    timeout=120,
    pre_chaos_pods=None,
):
    """Observe a post-Chaos pod replacement or container restart becoming Ready."""
    namespace = application.get("namespace", "default")
    target_app = get_target_app(application)
    expected_replicas = int(application.get("replicas", 1))
    original_pods = pre_chaos_pods or [killed_pod]

    def pod_identity(pod):
        metadata = pod.get("metadata", {})
        return metadata.get("uid") or metadata.get("name")

    original_pods_by_id = {pod_identity(pod): pod for pod in original_pods}
    original_pod_ids = set(original_pods_by_id)
    deadline = time.time() + timeout
    pod_disappeared_at = None
    container_restart_detected_at = {}
    replacement_pod_observed_at = None
    detected_killed_pod = killed_pod.get("metadata", {}).get("name")

    def pod_is_ready(pod):
        status = pod.get("status", {})
        container_statuses = status.get("containerStatuses", [])
        return (
            status.get("phase") == "Running"
            and bool(container_statuses)
            and all(container.get("ready", False) for container in container_statuses)
        )

    while time.time() < deadline:
        try:
            data = json.loads(run_command([
                "kubectl", "get", "pods", "-n", namespace,
                "-l", f"app={target_app}", "-o", "json",
            ]))
            pods = data.get("items", [])
            current_pods_by_id = {pod_identity(pod): pod for pod in pods}
            missing_originals = [
                original_pods_by_id[pod_id]
                for pod_id in original_pod_ids
                if pod_id not in current_pods_by_id
            ]
            if missing_originals and pod_disappeared_at is None:
                pod_disappeared_at = time.time()
                detected_killed_pod = missing_originals[0].get("metadata", {}).get("name")

            deleting_originals = [
                pod for pod_id, pod in current_pods_by_id.items()
                if pod_id in original_pod_ids and (
                    pod.get("metadata", {}).get("deletionTimestamp")
                    or pod.get("status", {}).get("phase") in {"Succeeded", "Failed"}
                )
            ]
            if deleting_originals:
                detected_killed_pod = deleting_originals[0].get("metadata", {}).get("name")

            ready_pods = [
                pod for pod in pods
                if pod_is_ready(pod)
            ]
            replacement_pods = [
                pod for pod in pods
                if pod_identity(pod) not in original_pod_ids
            ]
            if replacement_pods and replacement_pod_observed_at is None:
                replacement_pod_observed_at = time.time()
            for original_id, original_pod in original_pods_by_id.items():
                same_pod = current_pods_by_id.get(original_id)
                if same_pod is None:
                    continue
                original_restart_counts = {
                    status.get("name"): status.get("restartCount", 0)
                    for status in original_pod.get("status", {}).get("containerStatuses", [])
                }
                current_restart_counts = {
                    status.get("name"): status.get("restartCount", 0)
                    for status in same_pod.get("status", {}).get("containerStatuses", [])
                }
                restart_observed = any(
                    current_restart_counts.get(name, 0) > restart_count
                    for name, restart_count in original_restart_counts.items()
                )
                if restart_observed and original_id not in container_restart_detected_at:
                    container_restart_detected_at[original_id] = time.time()
                    detected_killed_pod = same_pod.get("metadata", {}).get("name")
                if (
                    restart_observed
                    and pod_is_ready(same_pod)
                    and len(ready_pods) >= expected_replicas
                ):
                    ready_at = time.time()
                    return {
                        "recovered": True,
                        "killed_pod": same_pod.get("metadata", {}).get("name"),
                        "replacement_ready_replicas": len(ready_pods),
                        "pod_disappeared_at": None,
                        "recovery_event_at": container_restart_detected_at[original_id],
                        "replacement_pod_ready_at": ready_at,
                        "recovery_mode": "container_restart",
                        "poll_interval_seconds": 3,
                    }

            replacement_ready_pods = [
                pod for pod in replacement_pods
                if pod_is_ready(pod)
            ]
            if (
                (missing_originals or deleting_originals)
                and replacement_ready_pods
                and len(ready_pods) >= expected_replicas
            ):
                ready_at = time.time()
                return {
                    "recovered": True,
                    "killed_pod": detected_killed_pod,
                    "replacement_ready_replicas": len(ready_pods),
                    "pod_disappeared_at": pod_disappeared_at,
                    "recovery_event_at": (
                        pod_disappeared_at
                        if pod_disappeared_at is not None
                        else replacement_pod_observed_at
                    ),
                    "replacement_pod_ready_at": ready_at,
                    "recovery_mode": "pod_replacement",
                    "poll_interval_seconds": 3,
                }
        except (RuntimeError, json.JSONDecodeError):
            pass
        time.sleep(3)

    return {
        "recovered": False,
        "killed_pod": detected_killed_pod,
        "replacement_ready_replicas": None,
        "pod_disappeared_at": pod_disappeared_at,
        "recovery_event_at": (
            next(iter(container_restart_detected_at.values()), None)
            if container_restart_detected_at
            else replacement_pod_observed_at
            if replacement_pod_observed_at is not None
            else pod_disappeared_at
        ),
        "replacement_pod_ready_at": None,
        "poll_interval_seconds": 3,
        "reason": "POD_REPLACEMENT_NOT_READY",
    }


def execute_pod_kill(
    application,
    yaml_file
):

    namespace = application.get(
        "namespace",
        "default"
    )

    print(
        "\n========================================"
    )

    print(
        "STAGE 1: POD KILL"
    )

    print(
        "========================================"
    )

    pods = json.loads(run_command([
        "kubectl", "get", "pods", "-n", namespace,
        "-l", f"app={get_target_app(application)}", "-o", "json",
    ])).get("items", [])
    if not pods:
        raise RuntimeError("Cannot measure Pod Kill recovery: no target pod exists")
    killed_pod = pods[0]
    start_time = time.time()
    print(f"[pod-kill] timer started for pod {killed_pod.get('metadata', {}).get('name')}")
    apply_chaos_file(
        yaml_file
    )
    chaos_applied_at = time.time()
    chaos_apply_seconds = chaos_applied_at - start_time
    print(f"[pod-kill] chaos_apply_seconds={chaos_apply_seconds:.3f}")

    print(
        "\nPod Kill is active."
    )

    recovery = wait_for_pod_kill_recovery(
        application,
        killed_pod,
        pre_chaos_pods=pods,
    )

    recovery_time = time.time() - start_time
    pod_disappearance_seconds = (
        recovery["pod_disappeared_at"] - chaos_applied_at
        if recovery.get("pod_disappeared_at") is not None
        else None
    )
    recovery_event_at = recovery.get("recovery_event_at") or recovery.get("pod_disappeared_at")
    replacement_pod_ready_seconds = (
        recovery["replacement_pod_ready_at"] - recovery_event_at
        if recovery.get("replacement_pod_ready_at") is not None and recovery_event_at is not None
        else None
    )
    print(f"[pod-kill] pod_disappearance_seconds={pod_disappearance_seconds}")
    print(f"[pod-kill] replacement_pod_ready_seconds={replacement_pod_ready_seconds}")
    print(f"[pod-kill] total_recovery_seconds={recovery_time:.3f}")

    cleanup_chaos(
        namespace,
        [
            "podchaos"
        ]
    )

    return {
        "environment": "Pod Kill",
        "start_time": start_time,
        "end_time": time.time(),
        "recovered": recovery["recovered"],
        "pod_kill_recovery_seconds": recovery_time if recovery["recovered"] else None,
        "chaos_apply_seconds": chaos_apply_seconds,
        "pod_disappearance_seconds": pod_disappearance_seconds,
        "replacement_pod_ready_seconds": replacement_pod_ready_seconds,
        "total_recovery_seconds": recovery_time,
        "replacement": recovery,
        "status": (
            "completed"
            if recovery["recovered"]
            else "completed_without_confirmed_recovery"
        )
    }


def execute_five_chaos(
    application,
    yaml_file,
    duration,
    vus
):

    namespace = application.get(
        "namespace",
        "default"
    )

    print(
        "\n========================================"
    )

    print(
        "STAGE 2: FIVE CHAOS ENVIRONMENTS"
    )

    print(
        "========================================"
    )

    print(
        "\nCPU Stress"
    )

    print(
        "Memory Stress"
    )

    print(
        "Network Delay"
    )

    print(
        "Network Loss"
    )

    print(
        "Network Partition"
    )

    print(
        "\nAll five environments will be "
        "applied together."
    )

    start_time = time.time()

    apply_chaos_file(
        yaml_file
    )

    if application.get(
        "name"
    ) in {
        "currencyservice",
        "productcatalogservice",
        "adservice",
        "shippingservice"
    }:

        k6_result = run_k6_grpc(
            application,
            vus,
            duration
        )

    else:

        k6_result = run_k6_http(
            application,
            vus,
            duration
        )

    seconds = parse_duration(
        duration
    )

    print(
        f"\nFive chaos environments running "
        f"simultaneously for {duration}..."
    )

    time.sleep(
        seconds
    )

    print(
        "\nFive-chaos duration completed."
    )

    cleanup_chaos(
        namespace,
        [
            "stresschaos",
            "networkchaos"
        ]
    )

    cleanup_duration = time.time() - cleanup_started
    readiness_started = time.time()
    recovered = (
        wait_for_application_recovery(
            application
        )
    )

    recovery_time = time.time() - start_time

    readiness_recovery = time.time() - readiness_started

    return {
        "environments": FIVE_CHAOS_ENVIRONMENTS,
        "start_time": start_time,
        "end_time": time.time(),
        "duration": duration,
        "simultaneous": True,
        "recovered": recovered,
        "stress_duration_seconds": stress_duration,
        "cleanup_duration_seconds": cleanup_duration,
        "readiness_recovery_seconds": readiness_recovery,
        "k6": k6_result,
        "status": "completed"
    }


def execute_node_failure(
    application,
    yaml_file,
    duration
):

    namespace = application.get(
        "namespace",
        "default"
    )

    print(
        "\n========================================"
    )

    print(
        "STAGE 3: NODE FAILURE"
    )

    print(
        "========================================"
    )

    node_count = get_node_count()

    if node_count < 2:

        message = (
            "Node Failure cannot be executed. "
            "The cluster has only one node. "
            "Create a multi-node kind cluster "
            "before running Node Failure."
        )

        print(
            f"\n{message}"
        )

        return {
            "environment": "Node Failure",
            "status": "not_executed",
            "reason": message
        }

    start_time = time.time()

    node_name = get_application_node(
        application
    )

    print(
        f"\nTarget node: {node_name}"
    )

    apply_chaos_file(
        yaml_file
    )

    seconds = parse_duration(
        duration
    )

    print(
        f"\nNode Failure running for "
        f"{duration}..."
    )

    time.sleep(
        seconds
    )

    cleanup_chaos(
        namespace,
        [
            "nodechaos"
        ]
    )

    recovered = (
        wait_for_application_recovery(
            application
        )
    )

    recovery_time = time.time() - start_time

    return {
        "environment": "Node Failure",
        "node": node_name,
        "start_time": start_time,
        "end_time": time.time(),
        "duration": duration,
        "recovered": recovered,
        "node_failure_duration_seconds": time.time() - start_time,
        "readiness_recovery_seconds": None,
        "status": (
            "completed"
            if recovered
            else "completed_without_confirmed_recovery"
        )
    }


def process_application(
    application,
    environments,
    cpu,
    memory,
    vus,
    duration
):

    name = application.get(
        "name"
    )

    namespace = application.get(
        "namespace",
        "default"
    )

    print(
        "\n========================================"
    )

    print(
        f"PROCESSING APPLICATION: {name}"
    )

    print(
        "========================================"
    )

    deployment_result = None

    try:

        print(
            "\nDeploying application..."
        )

        deployment_result = deploy_application(
            application
        )

        llm_application = (
            prepare_llm_application(
                application
            )
        )

        print(
            "\nGenerating environment parameters..."
        )

        parameters = (
            generate_environment_parameters(
                application=llm_application,
                environments=environments,
                cpu=cpu,
                memory=memory,
                vus=vus,
                duration=duration
            )
        )

        print(
            "\nGenerated parameters:"
        )

        print(
            json.dumps(
                parameters,
                indent=2
            )
        )

        print(
            "\nValidating parameters..."
        )

        valid, message = (
            validate_environment_parameters(
                parameters,
                application,
                environments
            )
        )

        if not valid:

            raise RuntimeError(
                f"Environment validation failed: "
                f"{message}"
            )

        print(
            "Parameters validated successfully."
        )

        target_app = get_target_app(
            application
        )

        chaos_parameters = (
            build_chaos_parameters(
                application=application,
                environments=environments,
                parameters=parameters,
                duration=duration
            )
        )

        os.makedirs(
            GENERATED_DIRECTORY,
            exist_ok=True
        )

        result = {
            "application": application,
            "deployment": deployment_result,
            "workload": {
                "vus": vus,
                "duration": duration
            },
            "chaos": {},
            "stages": {}
        }

        pod_kill_parameters = {
            "Pod Kill": chaos_parameters[
                "Pod Kill"
            ]
        }

        pod_kill_yaml = (
            generate_chaos_yaml(
                "Pod Kill",
                pod_kill_parameters[
                    "Pod Kill"
                ]
            )
        )

        pod_kill_file = os.path.join(
            GENERATED_DIRECTORY,
            f"{name}-pod-kill.yaml"
        )

        with open(
            pod_kill_file,
            "w",
            encoding="utf-8"
        ) as file:

            file.write(
                pod_kill_yaml
            )

        print(
            f"\nGenerated Pod Kill YAML: "
            f"{pod_kill_file}"
        )

        pod_kill_result = (
            execute_pod_kill(
                application,
                pod_kill_file
            )
        )

        result[
            "stages"
        ][
            "pod_kill"
        ] = pod_kill_result

        five_chaos_parameters = {}

        for environment in FIVE_CHAOS_ENVIRONMENTS:

            if environment in environments:

                five_chaos_parameters[
                    environment
                ] = chaos_parameters[
                    environment
                ]

        missing = [
            environment
            for environment
            in FIVE_CHAOS_ENVIRONMENTS
            if environment not in environments
        ]

        if missing:

            raise RuntimeError(
                "The five-chaos stage is missing: "
                + ", ".join(missing)
            )

        generated_five_chaos = (
            generate_compound_chaos_yaml(
                FIVE_CHAOS_ENVIRONMENTS,
                five_chaos_parameters
            )
        )

        five_chaos_file = os.path.join(
            GENERATED_DIRECTORY,
            f"{name}-five-chaos.yaml"
        )

        write_yaml_file(
            five_chaos_file,
            generated_five_chaos
        )

        print(
            f"\nGenerated five-chaos YAML: "
            f"{five_chaos_file}"
        )

        five_chaos_result = (
            execute_five_chaos(
                application,
                five_chaos_file,
                duration,
                vus
            )
        )

        result[
            "stages"
        ][
            "five_chaos"
        ] = five_chaos_result

        node_failure_parameters = {
            "Node Failure": chaos_parameters[
                "Node Failure"
            ]
        }

        node_failure_yaml = (
            generate_chaos_yaml(
                "Node Failure",
                node_failure_parameters[
                    "Node Failure"
                ]
            )
        )

        node_failure_file = os.path.join(
            GENERATED_DIRECTORY,
            f"{name}-node-failure.yaml"
        )

        with open(
            node_failure_file,
            "w",
            encoding="utf-8"
        ) as file:

            file.write(
                node_failure_yaml
            )

        print(
            f"\nGenerated Node Failure YAML: "
            f"{node_failure_file}"
        )

        node_failure_result = (
            execute_node_failure(
                application,
                node_failure_file,
                duration
            )
        )

        result[
            "stages"
        ][
            "node_failure"
        ] = node_failure_result

        result[
            "metrics"
        ] = collect_result_metrics(
            application,
            pod_kill_result,
            five_chaos_result.get(
                "k6"
            )
        )

        result[
            "chaos"
        ] = parameters

        result[
            "target_app"
        ] = target_app

        result[
            "generated_files"
        ] = {
            "pod_kill": pod_kill_file,
            "five_chaos": five_chaos_file,
            "node_failure": node_failure_file
        }

        result[
            "status"
        ] = "completed"

        result_file = os.path.join(
            GENERATED_DIRECTORY,
            f"{name}-environment-result.json"
        )

        with open(
            result_file,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                result,
                file,
                indent=2
            )

        print(
            f"\nResult saved: {result_file}"
        )

        return result

    finally:

        if deployment_result:

            cleanup_application(
                application
            )


def generate_environment():

    print(
        "\n========================================"
    )

    print(
        "AI KUBERNETES ENVIRONMENT SIMULATOR"
    )

    print(
        "========================================"
    )

    github_url = input(
        "\nGitHub repository URL: "
    ).strip()

    if not github_url:

        raise ValueError(
            "GitHub repository URL is required."
        )

    cpu = input(
        "Available CPU: "
    ).strip()

    memory = input(
        "Available memory: "
    ).strip()

    vus = input(
        "k6 VUs: "
    ).strip()

    duration = input(
        "Experiment duration: "
    ).strip()

    environments = [
        "Pod Kill",
        "CPU Stress",
        "Memory Stress",
        "Network Delay",
        "Network Loss",
        "Network Partition",
        "Node Failure"
    ]

    print(
        "\nSelected environment sequence:"
    )

    for environment in environments:

        print(
            f"  - {environment}"
        )

    project_path = clone_repository(
        github_url
    )

    print(
        "\nRepository:"
    )

    print(
        project_path
    )

    yaml_files = find_yaml_files(
        project_path
    )

    print(
        f"\nDiscovered {len(yaml_files)} YAML files."
    )

    resources = []

    for yaml_file in yaml_files:

        resources.extend(
            parse_yaml_file(
                yaml_file
            )
        )

    print(
        f"Parsed {len(resources)} Kubernetes resources."
    )

    applications = analyze_resources(
        resources
    )

    workloads = get_workloads(
        applications
    )

    print(
        f"\nDetected {len(workloads)} workloads."
    )

    results = []

    for index, application in enumerate(
        workloads,
        start=1
    ):

        print(
            "\n----------------------------------------"
        )

        print(
            f"Application {index}/{len(workloads)}"
        )

        print(
            f"Name: {application.get('name')}"
        )

        print(
            f"Namespace: "
            f"{application.get('namespace', 'default')}"
        )

        print(
            "----------------------------------------"
        )

        try:

            result = process_application(
                application=application,
                environments=environments,
                cpu=cpu,
                memory=memory,
                vus=vus,
                duration=duration
            )

            results.append(
                result
            )

        except Exception as error:

            print(
                f"\nApplication failed: {error}"
            )

            results.append({
                "application": application,
                "status": "failed",
                "error": str(error),
                "metrics": collect_result_metrics(
                    application,
                    None
                )
            })

    aggregate_file = os.path.join(
        GENERATED_DIRECTORY,
        "environment-results.json"
    )

    os.makedirs(
        GENERATED_DIRECTORY,
        exist_ok=True
    )

    with open(
        aggregate_file,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            results,
            file,
            indent=2
        )

    print(
        "\n========================================"
    )

    print(
        "ENVIRONMENT SIMULATION COMPLETED"
    )

    print(
        "========================================"
    )

    print(
        f"\nAggregate result: {aggregate_file}"
    )

    return results


def generate_selected_environment():
    from services.selected_experiment import run_selected_application

    parser = argparse.ArgumentParser(description="Run one selected Kubernetes workload experiment")
    parser.add_argument("--application", help="workload name to test")
    parser.add_argument("--list-applications", action="store_true", help="list discovered workloads and exit")
    parser.add_argument("--repository-url", help="Git repository URL")
    parser.add_argument("--available-cpu", default="2")
    parser.add_argument("--available-memory", default="4Gi")
    parser.add_argument("--vus", type=int, default=5)
    parser.add_argument("--duration", default="20s")
    args = parser.parse_args()

    github_url = args.repository_url or input("\nGitHub repository URL: ").strip()
    if not github_url:
        raise ValueError("GitHub repository URL is required.")

    project_path = clone_repository(github_url)
    resources = []
    for yaml_file in find_yaml_files(project_path):
        resources.extend(parse_yaml_file(yaml_file))
    workloads = get_workloads(analyze_resources(resources))

    print("\nAvailable applications:")
    for index, workload in enumerate(workloads, start=1):
        print(f"  {index}. {workload.get('name')} ({workload.get('kind')}, {workload.get('namespace', 'default')})")
    if args.list_applications:
        return workloads

    application_name = args.application
    if not application_name:
        default_name = "adservice"
        application_name = default_name if any(item.get("name") == default_name for item in workloads) else workloads[0].get("name")
    application = next((item for item in workloads if item.get("name") == application_name), None)
    if application is None:
        raise ValueError(f"Application not found: {application_name}")

    print(f"\nSelected application: {application_name}")
    result = run_selected_application(
        application,
        available_cpu=args.available_cpu,
        available_memory=args.available_memory,
        vus=args.vus,
        duration=args.duration,
    )
    result_file = os.path.join(GENERATED_DIRECTORY, f"{application_name}-selected-result.json")
    os.makedirs(GENERATED_DIRECTORY, exist_ok=True)
    with open(result_file, "w", encoding="utf-8") as file:
        json.dump(result, file, indent=2)
    print(f"\nStructured result saved: {result_file}")
    print(f"Run status: {result.get('status', 'FAILED')}")
    return result


if __name__ == "__main__":
    generate_selected_environment()
