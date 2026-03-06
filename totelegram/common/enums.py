import enum


class Strategy(str, enum.Enum):
    SINGLE = "single-file"
    CHUNKED = "pieces-file"

    @classmethod
    def evaluate(cls, file_size: int, tg_limit: int) -> "Strategy":
        """Determina la estrategia de subida basado en el tamaño del archivo y el tamaño de Telegram."""
        return cls.SINGLE if file_size <= tg_limit else cls.CHUNKED


class JobStatus(str, enum.Enum):
    PENDING = "PENDING"
    SPLITTED = "SPLITTED"
    UPLOADED = "UPLOADED"
    ORPHANED = "ORPHANED"


class AvailabilityState(str, enum.Enum):
    FULFILLED = "fulfilled"
    CAN_FORWARD = "can-forward"
    NEEDS_UPLOAD = "needs-upload"


class DuplicatePolicy(str, enum.Enum):
    SMART = "smart"  # Intenta recuperar/preguntar.
    STRICT = "strict"  # Evita duplicados a toda costa.
    OVERWRITE = "force"  # Ignora la base de datos y sube de nuevo.

    def __str__(self):
        return self.value

    def __repr__(self):
        return self.value


class SourceType(str, enum.Enum):
    FILE = "file"
    FOLDER = "folder"
