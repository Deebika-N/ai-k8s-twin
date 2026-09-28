import subprocess
import time


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


def apply_chaos(yaml_file):

    print(
        f"\nApplying chaos experiment: {yaml_file}"
    )

    output = run_kubectl([
        "kubectl",
        "apply",
        "-f",
        yaml_file
    ])

    print(output)

    return output


def delete_chaos(
    namespace,
    chaos_resources
):

    for resource in chaos_resources:

        try:

            run_kubectl([
                "kubectl",
                "delete",
                resource,
                "-n",
                namespace,
                "--ignore-not-found"
            ])

        except RuntimeError:

            pass


def wait_for_duration(duration):

    duration = duration.strip()

    if duration.endswith("s"):

        seconds = float(
            duration[:-1]
        )

    elif duration.endswith("m"):

        seconds = float(
            duration[:-1]
        ) * 60

    else:

        raise ValueError(
            "Duration must use s or m."
        )

    time.sleep(seconds)


def execute_chaos(
    yaml_file,
    namespace,
    duration,
    chaos_resources
):

    start_time = time.time()

    apply_chaos(
        yaml_file
    )

    print(
        f"\nChaos running for {duration}..."
    )

    wait_for_duration(
        duration
    )

    print(
        "\nChaos duration completed."
    )

    delete_chaos(
        namespace,
        chaos_resources
    )

    end_time = time.time()

    return {
        "start_time": start_time,
        "end_time": end_time,
        "duration": duration,
        "status": "completed"
    }
