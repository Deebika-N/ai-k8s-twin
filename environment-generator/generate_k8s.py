import json
from pathlib import Path

from config import EnvironmentConfig


ROOT = Path(__file__).resolve().parent
INPUT_FILE = ROOT / "generated" / "environment.json"
OUTPUT_FILE = ROOT / "generated" / "deployment.yaml"
DEFAULT_IMAGE = "us-central1-docker.pkg.dev/online-boutique-ci/microservices-demo/paymentservice:v0.10.6"
NAMESPACE = "simulation"


def render_manifests(config: EnvironmentConfig) -> str:
    resource_name = f"simulation-{config.service}"
    return f"""apiVersion: v1
kind: Namespace
metadata:
  name: {NAMESPACE}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {resource_name}
  namespace: {NAMESPACE}
spec:
  replicas: {config.replicas}
  selector:
    matchLabels:
      app: {resource_name}
  template:
    metadata:
      labels:
        app: {resource_name}
    spec:
      containers:
        - name: {config.service}
          image: {config.image}
          env:
            - name: PORT
              value: "{config.application_port}"
            - name: DISABLE_PROFILER
              value: "1"
          resources:
            requests:
              cpu: {config.cpu_request}
              memory: {config.memory_request}
            limits:
              cpu: {config.cpu_limit}
              memory: {config.memory_limit}
          ports:
            - containerPort: {config.application_port}
          readinessProbe:
            tcpSocket:
              port: {config.application_port}
            initialDelaySeconds: 5
            periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: {resource_name}
  namespace: {NAMESPACE}
spec:
  selector:
    app: {resource_name}
  ports:
    - port: {config.application_port}
      targetPort: {config.application_port}
"""


def main() -> None:
    with INPUT_FILE.open(encoding="utf-8") as file:
        values = json.load(file)
    values.setdefault("image", DEFAULT_IMAGE)
    values.setdefault("application_port", 50051)
    config = EnvironmentConfig.from_mapping(values)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(render_manifests(config), encoding="utf-8")
    print(f"Kubernetes configuration generated: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
