import json
import threading
import subprocess
import time

from backend.services.prometheus_service import collect_metrics


def run_k6():

    command = [
        "k6",
        "run",
        "--out",
        "json=results/experiment.json",
        "workload/payment-test.js"
    ]

    return subprocess.run(
        command,
        capture_output=True,
        text=True
    )


def analyze_k6_results(filename):

    latencies = []
    total_requests = 0
    failed_requests = 0

    with open(filename) as file:

        for line in file:

            try:
                data = json.loads(line)

            except json.JSONDecodeError:
                continue

            if (
                data.get("type") == "Point"
                and data.get("metric") == "http_req_duration"
            ):

                latencies.append(
                    data["data"]["value"]
                )

                total_requests += 1

            if (
                data.get("type") == "Point"
                and data.get("metric") == "http_req_failed"
            ):

                if data["data"]["value"] == 1:
                    failed_requests += 1

    if not latencies:

        raise RuntimeError(
            "No HTTP latency data found."
        )

    latencies.sort()

    def percentile(values, percentile):

        index = (
            (len(values) - 1)
            * percentile
            / 100
        )

        lower = int(index)
        upper = lower + 1

        if upper >= len(values):

            return values[lower]

        weight = index - lower

        return (
            values[lower]
            + (
                values[upper]
                - values[lower]
            )
            * weight
        )

    return {
        "requests": total_requests,
        "failed_requests": failed_requests,
        "error_rate_percent": (
            failed_requests
            / total_requests
            * 100
        ),
        "p95_ms": percentile(
            latencies,
            95
        ),
        "p99_ms": percentile(
            latencies,
            99
        )
    }


def run_experiment():

    metrics = []

    experiment_start = time.time()

    def collect():

        nonlocal metrics

        metrics = collect_metrics(
            duration=70,
            interval=5
        )

    collector_thread = threading.Thread(
        target=collect
    )

    collector_thread.start()

    k6_result = run_k6()

    experiment_end = time.time()

    collector_thread.join()

    k6_metrics = analyze_k6_results(
        "results/experiment.json"
    )

    experiment = {
        "experiment": {
            "start_time": experiment_start,
            "end_time": experiment_end,
            "duration_seconds": 60
        },

        "workload": {
            "vus": 10,
            "duration": "60s",
            "requests": k6_metrics["requests"],
            "failed_requests": k6_metrics[
                "failed_requests"
            ],
            "error_rate_percent": k6_metrics[
                "error_rate_percent"
            ],
            "p95_ms": k6_metrics[
                "p95_ms"
            ],
            "p99_ms": k6_metrics[
                "p99_ms"
            ]
        },

        "chaos": {
            "environments": []
        },

        "prometheus": metrics
    }

    with open(
        "results/experiment-record.json",
        "w"
    ) as file:

        json.dump(
            experiment,
            file,
            indent=2
        )

    print(k6_result.stdout)

    if k6_result.returncode != 0:

        print(k6_result.stderr)

        raise RuntimeError(
            "k6 experiment failed."
        )

    print(
        "\nExperiment record saved to "
        "results/experiment-record.json"
    )


if __name__ == "__main__":

    run_experiment()
