import enum
import json

import peewee
from peewee import Field


class PydanticJSONField(Field):
    """
    Campo personalizado de Peewee.
    DB: Guarda JSON String (TEXT).
    Python: Usa objetos Pydantic validados.
    """

    field_type = "TEXT"

    def __init__(self, schema_model, *args, **kwargs):
        self.schema_model = schema_model
        super().__init__(*args, **kwargs)

    def db_value(self, value):
        """Python -> DB"""
        if hasattr(value, "model_dump_json"):
            return value.model_dump_json()
        if value is None:
            return None
        return json.dumps(value)

    def python_value(self, value):
        """DB -> Python"""
        if value is None:
            return self.schema_model()
        try:
            # Si viene como string desde la DB
            if isinstance(value, str):
                return self.schema_model.model_validate_json(value)
            # Si viene como dict (algunos drivers)
            return self.schema_model.model_validate(value)
        except Exception:
            return self.schema_model()


class EnumField(peewee.CharField):
    """
    Enum-like field for Peewee
    """

    def __init__(self, enum: type[enum.Enum], *args, **kwargs):
        self.enum = enum
        kwargs.setdefault("max_length", max(len(e.value) for e in enum))
        super().__init__(*args, **kwargs)

    def db_value(self, value):
        if value is None:
            return None
        if isinstance(value, self.enum):
            return value.value
        return self.enum(value).value

    def python_value(self, value):
        if value is None:
            return None
        return self.enum(value)
