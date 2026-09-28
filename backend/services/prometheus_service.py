import time
import requests


PROMETHEUS_URL = "http://localhost:9090"


def query_prometheus(query):

    response = requests.get(
        f"{PROMETHEUS_URL}/api/v1/query",
        params={"query": query}
    )

    response.raise_for_status()

    data = response.json()

    results = data["data"]["result"]

    if not results:
        return None

    return float(results[0]["value"][1])


def collect_metrics(duration=60, interval=5):

    metrics = []

    cpu_query = (
        'rate(container_cpu_usage_seconds_total'
        '{namespace="simulation-resource",'
        'container="payment"}[1m])'
    )

    memory_query = (
        'container_memory_working_set_bytes'
        '{namespace="simulation-resource",'
        'container="payment"}'
    )

    start_time = time.time()

    while time.time() - start_time < duration:

        cpu = query_prometheus(cpu_query)

        memory = query_prometheus(memory_query)

        metrics.append({
            "timestamp": time.time(),
            "cpu_cores": cpu,
            "memory_bytes": memory
        })

        time.sleep(interval)

    return metrics

