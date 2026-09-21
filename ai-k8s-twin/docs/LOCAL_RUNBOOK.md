# Local Development and Manual Test Runbook

This project uses Windows for the editor and WSL2 Ubuntu for Docker, Kind, Kubernetes, and Python commands.

## One-time setup

Install and verify Docker Desktop with WSL2 integration, WSL2 Ubuntu, `kubectl`, `kind`, `helm`, and Python 3.

From WSL Ubuntu:

```bash
docker version
kubectl version --client
kind version
helm version
```

Create the cluster once:

```bash
kind create cluster --name ai-k8s-twin
kubectl config use-context kind-ai-k8s-twin
```

Create the Python environment once:

```bash
cd /mnt/c/Users/sri/Desktop/ai-k8s-twin/ai-k8s-twin/environment-generator
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Daily startup

1. Start Docker Desktop in Windows and wait until it is running.
2. Open WSL Ubuntu.
3. Run:

```bash
cd /mnt/c/Users/sri/Desktop/ai-k8s-twin/ai-k8s-twin
source environment-generator/.venv/bin/activate
kubectl config use-context kind-ai-k8s-twin
kubectl get nodes
```

If the cluster does not exist:

```bash
kind create cluster --name ai-k8s-twin
kubectl config use-context kind-ai-k8s-twin
```

## Generator checks

```bash
cd /mnt/c/Users/sri/Desktop/ai-k8s-twin/ai-k8s-twin/environment-generator
python -m unittest -v test_generate_k8s.py
python generate_k8s.py
```

The generated file must contain:

- image `us-central1-docker.pkg.dev/online-boutique-ci/microservices-demo/paymentservice:v0.10.6`
- `PORT=50051`
- `DISABLE_PROFILER=1`
- namespace `simulation`

## Deploy and verify

```bash
kubectl apply -f generated/deployment.yaml
kubectl rollout status deployment/simulation-paymentservice -n simulation --timeout=180s
kubectl get pods -n simulation -o wide
kubectl get endpoints simulation-paymentservice -n simulation
kubectl logs deployment/simulation-paymentservice -n simulation --tail=50
```

The gate is passed only when all replicas are `1/1 Running`, the restart count is stable, and the Service has endpoints.

## Troubleshooting

```bash
kubectl get events -n simulation --sort-by=.lastTimestamp
kubectl describe deployment simulation-paymentservice -n simulation
kubectl describe pod -n simulation
kubectl logs deployment/simulation-paymentservice -n simulation --previous --tail=100
```

- `connection refused` means Docker Desktop or the Kind cluster is unavailable, or the current kubectl context is wrong.
- `ImagePullBackOff` means the image cannot be pulled. Check the image in the generated YAML and inspect pod events.
- `CrashLoopBackOff` means the image starts and exits. Inspect current and previous container logs.
- No Service endpoints means pods are not Ready; inspect the readiness probe and pod events.

## Run the repository verification script

From the repository root:

```bash
bash scripts/verify-local.sh
```

The script is read-only: it runs tests and `kubectl get/status` checks but does not create, modify, or delete cluster resources.

## Install cluster add-ons

Install Chaos Mesh:

```bash
helm repo add chaos-mesh https://charts.chaos-mesh.org
helm repo update
kubectl create namespace chaos-mesh --dry-run=client -o yaml | kubectl apply -f -
helm upgrade --install chaos-mesh chaos-mesh/chaos-mesh \
	--namespace chaos-mesh \
	--set chaosDaemon.runtime=containerd \
	--set chaosDaemon.socketPath=/run/containerd/containerd.sock \
	--wait --timeout 10m
```

Install Prometheus and Grafana:

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -
helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
	--namespace monitoring --wait --timeout 10m
```

Verify add-ons:

```bash
kubectl get pods -n chaos-mesh
kubectl get pods -n monitoring
kubectl get crd stresschaos.chaos-mesh.org podchaos.chaos-mesh.org
```

## Manual chaos checks

Validate all experiment resources against the live cluster:

```bash
kubectl apply --dry-run=server -f chaos/cpu-stress.yaml
kubectl apply --dry-run=server -f chaos/memory-stress.yaml
kubectl apply --dry-run=server -f chaos/pod-kill.yaml
```

Run CPU stress:

```bash
kubectl apply -f chaos/cpu-stress.yaml
kubectl get stresschaos -n simulation
kubectl get events -n simulation --sort-by=.lastTimestamp
```

The current experiment runs for two minutes and targets one `simulation-paymentservice` pod. Clean it up when finished:

```bash
kubectl delete -f chaos/cpu-stress.yaml
```

Run memory stress similarly:

```bash
kubectl apply -f chaos/memory-stress.yaml
kubectl get events -n simulation --sort-by=.lastTimestamp
kubectl delete -f chaos/memory-stress.yaml
```

Chaos Mesh expects decimal memory suffixes such as `256MB` for this field; Kubernetes `Mi` quantities are not accepted by its stress admission webhook.

Run pod kill:

```bash
kubectl apply -f chaos/pod-kill.yaml
kubectl get pods -n simulation --watch
kubectl delete -f chaos/pod-kill.yaml
```

The expected result is one pod being killed and the Deployment creating or retaining a replacement. Confirm the final state with:

```bash
kubectl rollout status deployment/simulation-paymentservice -n simulation --timeout=180s
kubectl get endpoints simulation-paymentservice -n simulation
```

## Collect the first Prometheus baseline

Keep Prometheus port-forwarded in one WSL terminal:

```bash
kubectl port-forward -n monitoring \
	service/monitoring-kube-prometheus-prometheus 9090:9090
```

In a second WSL terminal, activate the environment and run:

```bash
cd /mnt/c/Users/sri/Desktop/ai-k8s-twin/ai-k8s-twin
source environment-generator/.venv/bin/activate
python metrics/collect_baseline.py
```

The collector currently measures CPU, memory, restarts, available replicas, and OOMKilled state. Application request rate, latency, and error rate are reported as `null` until a gRPC traffic path and application metrics are added.

Run the complete local gate:

```bash
bash scripts/verify-local.sh
```

The next development slice after this baseline is the FastAPI experiment controller, which will add run IDs, start/finish windows, chaos lifecycle, and result persistence around these metrics.

## Evaluate baseline constraints

The deterministic constraint engine is available in `constraints/engine.py`. It evaluates availability, latency, error rate, recovery time, restarts, and OOMKilled without calling an LLM.

Run its tests:

```bash
python -m unittest -v constraints.test_engine
```

Missing required metrics are reported as `UNKNOWN` and make the overall evaluation fail. This is intentional: an unmeasured latency or error rate must not be presented as a successful experiment.

Evaluate the live baseline while the Prometheus port-forward is running:

```bash
python constraints/evaluate_baseline.py
```

The current baseline is expected to fail overall because request latency, error rate, and recovery time are not instrumented yet. That is a useful honest result, not a deployment failure; the next implementation step is a gRPC-capable traffic and application-metrics path.

## Start the backend contract

Install the updated Python dependencies:

```bash
cd /mnt/c/Users/sri/Desktop/ai-k8s-twin/ai-k8s-twin/environment-generator
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Run backend tests:

```bash
cd /mnt/c/Users/sri/Desktop/ai-k8s-twin/ai-k8s-twin
python -m unittest -v backend.test_app
```

Start the API locally:

```bash
uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000
```

Check its contract:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/config
curl -X POST "http://127.0.0.1:8000/experiments?experiment=baseline"
```

At this stage `/experiments` records a queued request only. It does not yet execute Kubernetes commands; that is the next controller implementation after the traffic protocol is fixed.