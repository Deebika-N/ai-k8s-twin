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


def parse_cpu(value):

    value = str(value).strip()

    if value.endswith("m"):

        try:
            return float(
                value[:-1]
            ) / 1000

        except ValueError:
            return None

    try:
        return float(value)

    except ValueError:
        return None


def parse_memory(value):

    value = str(value).strip()

    units = {
        "K": 1000,
        "M": 1000 ** 2,
        "G": 1000 ** 3,
        "Ki": 1024,
        "Mi": 1024 ** 2,
        "Gi": 1024 ** 3
    }

    for unit, multiplier in units.items():

        if value.endswith(unit):

            try:

                return (
                    float(
                        value[:-len(unit)]
                    )
                    * multiplier
                )

            except ValueError:

                return None

    return None
def validate_environment_parameters(
    result,
    application
):

    # --------------------------------
    # Basic structure
    # --------------------------------

    if not isinstance(result, dict):

        return False, (
            "Result must be a JSON object."
        )

    environments = result.get(
        "environments"
    )

    if not isinstance(
        environments,
        dict
    ):

        return False, (
            "Environments must be a JSON object."
        )

    # --------------------------------
    # Get application limits
    # --------------------------------

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
        "resources",
        {}
    )

    limits = resources.get(
        "limits",
        {}
    )

    cpu_limit = limits.get(
        "cpu"
    )

    memory_limit = limits.get(
        "memory"
    )

    # --------------------------------
    # Validate each environment
    # --------------------------------

    for environment, parameters in environments.items():

        # --------------------------------
        # Environment name
        # --------------------------------

        if environment not in ALLOWED_ENVIRONMENTS:

            return False, (
                f"Invalid environment: {environment}"
            )

        if not isinstance(
            parameters,
            dict
        ):

            return False, (
                f"Parameters for {environment} "
                "must be an object."
            )

        # --------------------------------
        # CPU Stress
        # --------------------------------

        if environment == "CPU Stress":

            value = parameters.get("value")

            if value is None:
                return False, "CPU Stress requires value."

            try:

                stress_load = float(
                    str(value).strip()
                )

            except ValueError:

                return False, "Invalid CPU stress value."

            if stress_load <= 0 or stress_load > 100:

                return False, (
                    "CPU stress load must be between "
                    "1 and 100 percent."
                )

        # --------------------------------
        # Memory Stress
        # --------------------------------

        elif environment == "Memory Stress":

            value = parameters.get(
                "value"
            )

            if value is None:

                return False, (
                    "Memory Stress requires value."
                )

            stress_memory = parse_memory(
                value
            )

            limit_memory = parse_memory(
                memory_limit
            )

            if stress_memory is None:

                return False, (
                    "Invalid memory stress value."
                )

            if limit_memory is None:

                return False, (
                    "Application memory limit is invalid."
                )

            if stress_memory <= 0:

                return False, (
                    "Memory stress must be greater than zero."
                )

            if stress_memory > limit_memory:

                return False, (
                    "Memory stress exceeds application memory limit."
                )

        # --------------------------------
        # Network Delay
        # --------------------------------

        elif environment == "Network Delay":

            value = parameters.get(
                "value"
            )

            if value is None:

                return False, (
                    "Network Delay requires value."
                )

            match = re.fullmatch(
                r"(\d+)(ms|s)",
                str(value).strip()
            )

            if not match:

                return False, (
                    "Network delay must use ms or s."
                )

            delay = int(
                match.group(1)
            )

            unit = match.group(2)

            if unit == "s":

                delay = delay * 1000

            if delay <= 0:

                return False, (
                    "Network delay must be greater than zero."
                )

            if delay > 10000:

                return False, (
                    "Network delay cannot exceed 10 seconds."
                )

        # --------------------------------
        # Network Loss
        # --------------------------------

        elif environment == "Network Loss":

            value = parameters.get(
                "value"
            )

            if value is None:

                return False, (
                    "Network Loss requires value."
                )

            try:

                loss = float(
                    str(value).strip()
                )

            except ValueError:

                return False, (
                    "Invalid network loss value."
                )

            if loss <= 0 or loss > 100:

                return False, (
                    "Network loss must be between "
                    "0 and 100 percent."
                )

        # --------------------------------
        # Pod Kill
        # --------------------------------

        elif environment == "Pod Kill":

            target = parameters.get(
                "target"
            )

            if not target:

                return False, (
                    "Pod Kill requires a target."
                )

        # --------------------------------
        # Network Partition
        # --------------------------------

        elif environment == "Network Partition":

            target = parameters.get(
                "target"
            )

            if not target:

                return False, (
                    "Network Partition requires a target."
                )

        # --------------------------------
        # Node Failure
        # --------------------------------

        elif environment == "Node Failure":

            node = parameters.get(
                "node"
            )

            if not node:

                return False, (
                    "Node Failure requires a node."
                )

    return True, (
        "All environment parameters are valid."
    )
