from services.prometheus_service import collect_metrics


metrics = collect_metrics(
    duration=20,
    interval=5
)

for metric in metrics:
    print(metric)
