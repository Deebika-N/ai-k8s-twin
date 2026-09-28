from services.environment_orchestrator import (
    REPOSITORY_DIRECTORY,
    analyze_resources,
    find_yaml_files,
    get_workloads,
    parse_yaml_file,
    process_application,
)

resources = []
for path in find_yaml_files(REPOSITORY_DIRECTORY):
    resources.extend(parse_yaml_file(path))

workloads = get_workloads(analyze_resources(resources))
application = next(
    item for item in workloads
    if item.get("name") == "productcatalogservice"
)

result = process_application(
    application,
    [
        "Pod Kill",
        "CPU Stress",
        "Memory Stress",
        "Network Delay",
        "Network Loss",
        "Network Partition",
        "Node Failure",
    ],
    "2",
    "4Gi",
    "5",
    "20s",
)

print("RESULT_STATUS=" + result.get("status", "unknown"))
