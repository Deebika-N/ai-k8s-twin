import json
import os
import re
import shutil
import subprocess
import tempfile
import time

import yaml
from git import Repo
from groq import Groq
from jinja2 import Environment, FileSystemLoader


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
            "Repository already exists. Reusing it."
        )

        return REPOSITORY_DIRECTORY

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

    resource = find_application_resource(
        application
    )

    pod_spec = (
        resource
        .get("spec", {})
        .get("template", {})
        .get("spec", {})
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

Selected environments:
{json.dumps(environments, indent=2)}

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
                environment
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

        scale_application(
            application,
            0
        )

        print(
            f"Scaled {application.get('name')} "
            "to 0 replicas."
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
                "--ignore-not-found"
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

    start_time = time.time()

    apply_chaos_file(
        yaml_file
    )

    print(
        "\nPod Kill is active."
    )

    recovered = (
        wait_for_application_recovery(
            application
        )
    )

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
        "recovered": recovered,
        "status": (
            "completed"
            if recovered
            else "completed_without_confirmed_recovery"
        )
    }


def execute_five_chaos(
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

    recovered = (
        wait_for_application_recovery(
            application
        )
    )

    return {
        "environments": FIVE_CHAOS_ENVIRONMENTS,
        "start_time": start_time,
        "end_time": time.time(),
        "duration": duration,
        "simultaneous": True,
        "recovered": recovered,
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

    return {
        "environment": "Node Failure",
        "node": node_name,
        "start_time": start_time,
        "end_time": time.time(),
        "duration": duration,
        "recovered": recovered,
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
                duration
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
                "error": str(error)
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


if __name__ == "__main__":

    generate_environment()
