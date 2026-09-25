import subprocess


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

    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:

        raise RuntimeError(
            result.stderr.strip()
        )

    return result.stdout.strip()


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

    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:

        raise RuntimeError(
            result.stderr.strip()
        )

    return result.stdout.strip()


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

    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:

        raise RuntimeError(
            result.stderr.strip()
        )

    return result.stdout.strip()
