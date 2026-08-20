import os


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


MAX_CROSS_DIMENSIONS = _positive_int("P360_CROSS_MAX_DIMENSIONS", 8)
MAX_CROSS_NODES = _positive_int("P360_CROSS_MAX_NODES", 5000)
MAX_CROSS_COMBINATIONS = _positive_int("P360_CROSS_MAX_COMBINATIONS", 100000)
