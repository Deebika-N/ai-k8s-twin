## Current Implementation

The backend currently implements the environment simulation and workload generation pipeline.

### GitHub Repository Integration
- Accepts a GitHub repository URL.
- Clones the repository into a temporary directory.
- Provides access to the application files for further analysis.

### Kubernetes YAML Discovery & Parsing
- Automatically searches the repository for `.yaml` and `.yml` files.
- Identifies Kubernetes resources such as:
  - Deployment
  - StatefulSet
  - DaemonSet
  - Service
  - ConfigMap
  - Secret
  - Ingress
  - Namespace
- Parses the Kubernetes resource definitions.

### Application Analysis
- Extracts application and deployment information including:
  - Application name
  - Namespace
  - Replicas
  - Pod labels
  - Container name
  - Container image
  - Container ports
  - CPU requests and limits
  - Memory requests and limits
  - Service selectors and ports

### Kubernetes Deployment
- Deploys Kubernetes YAML files using `kubectl`.
- Provides backend functions to retrieve pods and deployments.
- Supports deployment of applications into the simulation cluster.

### AI-Based Environment Parameter Generation
- Uses the Groq LLM to generate parameters for user-selected chaos environments.
- The LLM receives application details, resource limits, selected environments, VUs, and workload duration.
- The LLM generates parameters only.
- Environment selection and Kubernetes YAML generation are not handled by the LLM.

### Deterministic Parameter Validation
- Validates AI-generated parameters before execution.
- Checks CPU stress values against valid ranges.
- Checks memory stress against application memory limits.
- Validates network delay and packet-loss values.
- Ensures required parameters are present.

### Jinja2 Chaos Template Generation
- Uses predefined Chaos Mesh YAML templates.
- Injects validated parameters into the templates using Jinja2.
- Supports generation of:
  - CPU Stress
  - Memory Stress
  - Network Delay
  - Network Loss
  - Network Partition
  - Node Failure
  - Pod Kill

### Compound Chaos Simulation
- Supports multiple chaos environments in a single experiment.
- CPU stress, memory stress, network delay, and network loss have been tested together.
- Chaos Mesh injection and recovery have been verified successfully.

### k6 Load Generation
- Uses k6 to generate workload against the deployed application.
- Runs k6 from inside the Kubernetes cluster.
- Supports configurable:
  - Virtual Users (VUs)
  - Test duration
- Collects:
  - Total requests
  - Failed requests
  - Error rate
  - P95 latency
  - P99 latency

### Prometheus Monitoring
- Collects application resource metrics using Prometheus and PromQL.
- Currently monitors:
  - CPU usage
  - Memory usage
- CPU is collected using `container_cpu_usage_seconds_total`.
- Memory is collected using `container_memory_working_set_bytes`.

### Experiment Recording
- Combines k6 workload results and Prometheus metrics.
- Stores experiment information in a structured JSON file.
- The record contains:
  - Experiment timing
  - Workload configuration
  - Request count
  - Error rate
  - P95/P99 latency
  - Chaos environments
  - Prometheus observations