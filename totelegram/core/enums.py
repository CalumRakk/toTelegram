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
    # Ya se cumplió el objetivo en el chat actual.
    FULFILLED = "fulfilled"

    # El archivo está completo y accesible en OTRO chat único. (Clonación directa, si se desea)
    REMOTE_MIRROR = "remote-mirror"

    # El archivo está completo sumando piezas de varios chats. (Re-unificación)
    REMOTE_PUZZLE = "remote-puzzle"

    # La DB lo conoce, pero no tenemos acceso a los mensajes.
    REMOTE_RESTRICTED = "remote-restricted"

    # Es la primera vez que el sistema ve este archivo.
    SYSTEM_NEW = "system-new"


class DuplicatePolicy(str, enum.Enum):
    SMART = "smart"  # Intenta recuperar/preguntar.
    STRICT = "strict"  # Evita duplicados a toda costa.
    OVERWRITE = "force"  # Ignora la base de datos y sube de nuevo.
