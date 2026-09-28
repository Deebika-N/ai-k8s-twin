import re


ALLOWED_ENVIRONMENTS = {
    "Pod Kill",
    "CPU Stress",
    "Memory Stress",
    "Network Delay",
    "Network Loss",
    "Network Partition",
    "Node Failure"
}


def parse_memory(value):

    if value is None:
        return None

    value = str(value).strip()

    units = {
        "Ki": 1024 ** 1,
        "Mi": 1024 ** 2,
        "Gi": 1024 ** 3,
        "K": 1000,
        "M": 1000 ** 2,
        "G": 1000 ** 3
    }

    for unit in sorted(
        units,
        key=len,
        reverse=True
    ):

        if value.endswith(unit):

            number = value[:-len(unit)]

            try:

                return (
                    float(number)
                    * units[unit]
                )

            except ValueError:

                return None

    try:

        return float(value)

    except ValueError:

        return None


def parse_cpu(value):

    if value is None:
        return None

    value = str(value).strip()

    if value.endswith("m"):

        try:

            return (
                float(value[:-1])
                / 1000
            )

        except ValueError:

            return None

    try:

        return float(value)

    except ValueError:

        return None


def validate_environment_parameters(
    result,
    application
):

    if not isinstance(
        result,
        dict
    ):

        return False, (
            "LLM result must be a dictionary."
        )

    environments = result.get(
        "environments"
    )

    if not isinstance(
        environments,
        dict
    ):

        return False, (
            "Missing environments object."
        )

    containers = application.get(
        "containers",
        []
    )

    if not containers:

        return False, (
            "Application has no containers."
        )

    container = containers[0]

    resources = container.get(
        "resources"
    )

    if not isinstance(
        resources,
        dict
    ):

        resources = {}

    limits = resources.get(
        "limits"
    )

    if not isinstance(
        limits,
        dict
    ):

        limits = {}

    cpu_limit = limits.get(
        "cpu"
    )

    memory_limit = limits.get(
        "memory"
    )

    for environment, parameters in environments.items():

        if environment not in ALLOWED_ENVIRONMENTS:

            return False, (
                f"Unsupported environment: "
                f"{environment}"
            )

        if not isinstance(
            parameters,
            dict
        ):

            return False, (
                f"Parameters for "
                f"{environment} must be "
                f"an object."
            )

        value = parameters.get(
            "value"
        )

        if value is None:

            return False, (
                f"Missing value for "
                f"{environment}."
            )

        if environment == "CPU Stress":

            try:

                cpu_value = float(value)

            except (
                ValueError,
                TypeError
            ):

                return False, (
                    "CPU Stress value "
                    "must be numeric."
                )

            if (
                cpu_value <= 0
                or cpu_value > 100
            ):

                return False, (
                    "CPU Stress must be "
                    "between 1 and 100."
                )

            if cpu_limit is not None:

                cpu_limit_value = parse_cpu(
                    cpu_limit
                )

                if cpu_limit_value is None:

                    return False, (
                        "Application CPU "
                        "limit is invalid."
                    )

        elif environment == "Memory Stress":

            memory_value = parse_memory(
                value
            )

            if memory_value is None:

                return False, (
                    "Memory Stress value "
                    "is invalid."
                )

            if memory_value <= 0:

                return False, (
                    "Memory Stress must be "
                    "greater than zero."
                )

            if memory_limit is not None:

                memory_limit_value = parse_memory(
                    memory_limit
                )

                if memory_limit_value is None:

                    return False, (
                        "Application memory "
                        "limit is invalid."
                    )

                if (
                    memory_value
                    > memory_limit_value
                ):

                    return False, (
                        "Memory Stress value "
                        "exceeds application "
                        "memory limit."
                    )

        elif environment == "Network Delay":

            match = re.fullmatch(
                r"(\d+)(ms|s)",
                str(value).strip()
            )

            if not match:

                return False, (
                    "Network Delay must "
                    "use ms or s."
                )

            delay_value = int(
                match.group(1)
            )

            unit = match.group(2)

            if unit == "s":

                delay_ms = (
                    delay_value * 1000
                )

            else:

                delay_ms = delay_value

            if delay_ms <= 0:

                return False, (
                    "Network Delay must be "
                    "greater than zero."
                )

            if delay_ms > 10000:

                return False, (
                    "Network Delay cannot "
                    "exceed 10 seconds."
                )

        elif environment == "Network Loss":

            try:

                loss_value = float(value)

            except (
                ValueError,
                TypeError
            ):

                return False, (
                    "Network Loss must "
                    "be numeric."
                )

            if (
                loss_value <= 0
                or loss_value > 100
            ):

                return False, (
                    "Network Loss must be "
                    "greater than 0 and "
                    "at most 100."
                )

        elif environment == "Pod Kill":

            target = parameters.get(
                "target"
            )

            if not target:

                return False, (
                    "Pod Kill requires "
                    "a target."
                )

        elif environment == "Network Partition":

            target = parameters.get(
                "target"
            )

            if not target:

                return False, (
                    "Network Partition "
                    "requires a target."
                )

        elif environment == "Node Failure":

            node = parameters.get(
                "node"
            )

            if not node:

                return False, (
                    "Node Failure requires "
                    "a node."
                )

    return True, (
        "Environment parameters are valid."
    )
