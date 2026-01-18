import enum


class Strategy(str, enum.Enum):
    SINGLE = "single-file"
    CHUNKED = "pieces-file"


class JobStatus(str, enum.Enum):
    PENDING = "PENDING"
    SPLITTED = "SPLITTED"
    UPLOADED = "UPLOADED"
    ORPHANED = "ORPHANED"


class AvailabilityState(str, enum.Enum):
    FULFILLED = "fulfilled"  # Ya está en el chat de destino
    RECOVERABLE = "recoverable"  # Está en otro chat y podemos reenviarlo (Forward)
    RESTRICTED = "restricted"  # Está en otro chat pero no tenemos acceso
    NEW = "new"  # No existe en el ecosistema


class DuplicatePolicy(str, enum.Enum):
    SMART = "smart"  # Intenta recuperar/preguntar.
    STRICT = "strict"  # Evita duplicados a toda costa.
    OVERWRITE = "force"  # Ignora la base de datos y sube de nuevo.
