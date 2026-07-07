import json
import sys
from typing import Any, Iterable, List

if sys.version_info >= (3, 12):
    from itertools import batched
else:

    def batched(iterable: List[Any], n: int) -> Iterable[Any]:
        """Fallback para Python < 3.12."""
        import itertools

        it = iter(iterable)
        while batch := list(itertools.islice(it, n)):
            yield batch


def get_type_annotation(field: Any) -> str:
    from typing import get_origin

    type_annotation = field.annotation
    if get_origin(type_annotation) is None:
        type_name = type_annotation.__name__
    else:
        type_name = str(type_annotation).replace("typing.", "")
    return type_name


def parse_comma_list(value: Any) -> List[str]:
    """Convierte un string separado por comas o representación JSON en una lista."""
    if isinstance(value, list):
        return value

    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            raise ValueError("Formato JSON invalido para la lista.")

    return [item.strip() for item in value.split(",") if item.strip()]
