import json
import sys
from datetime import datetime, timezone
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


def get_utc_now() -> datetime:
    """Retorna la fecha y hora actual garantizando la zona horaria UTC."""
    return datetime.now(timezone.utc)


def is_expired(target_time: datetime, grace_period_seconds: int = 0) -> bool:
    """
    Compara de forma segura si una fecha ya ha pasado respecto al tiempo actual UTC.
    Normaliza automáticamente la zona horaria si el motor de DB la omitió.
    """
    if target_time.tzinfo is None:
        target_time = target_time.replace(tzinfo=timezone.utc)

    return get_utc_now().timestamp() > (target_time.timestamp() + grace_period_seconds)


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
