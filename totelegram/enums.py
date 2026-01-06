import enum


class Strategy(str, enum.Enum):
    SINGLE = "single-file"
    CHUNKED = "pieces-file"


class JobStatus(str, enum.Enum):
    PENDING = "PENDING"
    SPLITTED = "SPLITTED"
    UPLOADED = "UPLOADED"
    ORPHANED = "ORPHANED"
