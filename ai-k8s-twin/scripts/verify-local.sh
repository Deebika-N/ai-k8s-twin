#!/usr/bin/env bash
set -u

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GENERATOR_DIR="$PROJECT_ROOT/environment-generator"

failures=0

check() {
  local label="$1"
  shift
  printf '%-35s' "$label"
  if "$@" >/dev/null 2>&1; then
    echo "OK"
  else
    echo "FAILED"
    failures=$((failures + 1))
  fi
}

echo "Project: $PROJECT_ROOT"
check "Python virtual environment" test -x "$GENERATOR_DIR/.venv/bin/python"
check "Generator tests" bash -c "cd '$GENERATOR_DIR' && '$GENERATOR_DIR/.venv/bin/python' -m unittest -q test_generate_k8s.py"
check "Metrics tests" bash -c "cd '$PROJECT_ROOT' && '$GENERATOR_DIR/.venv/bin/python' -m unittest -q metrics.test_features"
check "Constraint tests" bash -c "cd '$PROJECT_ROOT' && '$GENERATOR_DIR/.venv/bin/python' -m unittest -q constraints.test_engine"
check "Backend tests" bash -c "cd '$PROJECT_ROOT' && '$GENERATOR_DIR/.venv/bin/python' -m unittest -q backend.test_app"
check "Baseline evaluator imports" bash -c "cd '$PROJECT_ROOT' && '$GENERATOR_DIR/.venv/bin/python' -c 'import constraints.evaluate_baseline'"
check "kubectl context" kubectl config current-context
check "Kubernetes node" kubectl get nodes
check "simulation namespace" kubectl get namespace simulation
check "paymentservice deployment" kubectl get deployment simulation-paymentservice -n simulation
check "paymentservice rollout" kubectl rollout status deployment/simulation-paymentservice -n simulation --timeout=10s
check "paymentservice endpoints" kubectl get endpoints simulation-paymentservice -n simulation

if [ "$failures" -ne 0 ]; then
  echo
  echo "$failures check(s) failed. Run the relevant commands in docs/LOCAL_RUNBOOK.md."
  exit 1
fi

echo
echo "All local checks passed."