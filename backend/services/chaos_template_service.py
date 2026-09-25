import os

from jinja2 import Environment, FileSystemLoader


CHAOS_TEMPLATES = {
    "CPU Stress": "cpu-stress.yaml",
    "Memory Stress": "memory-stress.yaml",
    "Network Delay": "network-delay.yaml",
    "Network Loss": "network-packet-loss.yaml",
    "Network Partition": "network-partition.yaml",
    "Node Failure": "node-failure.yaml",
    "Pod Kill": "pod-kill.yaml"
}


def create_template_environment():

    template_directory = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "chaos"
        )
    )

    return Environment(
        loader=FileSystemLoader(
            template_directory
        ),
        autoescape=False
    )


def generate_chaos_yaml(
    environment,
    parameters
):

    if environment not in CHAOS_TEMPLATES:

        raise ValueError(
            f"Unsupported environment: {environment}"
        )

    template_environment = (
        create_template_environment()
    )

    template = template_environment.get_template(
        CHAOS_TEMPLATES[environment]
    )

    return template.render(
        **parameters
    )


def generate_compound_chaos_yaml(
    environments,
    parameters
):

    generated_files = []

    for environment in environments:

        if environment not in parameters:

            raise ValueError(
                f"Missing parameters for "
                f"{environment}"
            )

        environment_parameters = (
            parameters[environment]
        )

        yaml_content = generate_chaos_yaml(
            environment,
            environment_parameters
        )

        generated_files.append({
            "environment": environment,
            "yaml": yaml_content
        })

    return generated_files
