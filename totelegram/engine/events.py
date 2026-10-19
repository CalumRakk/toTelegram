from typing import TYPE_CHECKING, Protocol, runtime_checkable

from tartape.exceptions import TarIntegrityError
from tartape.schemas import (
    TarFileEndEvent,
    TarFileStartEvent,
    TarObserver,
    TarTapeCompletedEvent,
)

from totelegram.concurrency import ConcurrencyError

if TYPE_CHECKING:
    from totelegram.concurrency import LeaseHeartbeat
    from totelegram.models import Job, Payload


@runtime_checkable
class UploadObserver(Protocol):
    """Protocolo que define los eventos del ciclo de vida de la subida."""

    def on_hash_start(self, filename: str, total_bytes: int) -> None:
        """Llamado antes de iniciar el cálculo del hash MD5."""
        ...

    def on_hash_progress(self, current_bytes: int, total_bytes: int) -> None:
        """Llamado durante la lectura de disco para el cálculo del hash."""
        ...

    def on_hash_complete(self, filename: str, md5sum: str) -> None:
        """Llamado cuando el hash MD5 ha terminado de calcularse."""
        ...

    def on_job_start(self, job: "Job", total_payloads: int) -> None:
        """Llamado cuando un Job inicia su procesamiento."""
        ...

    def on_archive_file_start(self, arc_path: str, size: int) -> None:
        """Llamado cuando TarTape comienza a empaquetar un archivo específico dentro de un volumen."""
        ...

    def on_archive_file_end(
        self, arc_path: str, size: int, checksum: str | None
    ) -> None:
        """Llamado cuando TarTape finaliza la lectura de un archivo dentro de un volumen."""
        ...

    def on_payload_start(
        self, payload: "Payload", current_part: int, total_parts: int
    ) -> None:
        """Llamado antes de comenzar la subida física de una pieza."""
        ...

    def on_payload_progress(self, current_bytes: int, total_bytes: int) -> None:
        """Llamado durante la transferencia continua de bytes."""
        ...

    def on_payload_complete(self, payload: "Payload") -> None:
        """Llamado cuando una pieza se sube y registra con éxito."""
        ...

    def on_pause_start(self, total_seconds: int, reason: str) -> None:
        """Llamado al iniciar una pausa (intra-job, inter-job o flood)."""
        ...

    def on_pause_tick(self, remaining_seconds: int, total_seconds: int) -> None:
        """Llamado cada segundo durante una pausa activa."""
        ...

    def on_pause_end(self) -> None:
        """Llamado cuando finaliza la pausa."""
        ...

    def on_forward_success(self, job: "Job", pieces_count: int) -> None:
        """Llamado cuando un archivo se resuelve vía Smart Forward (cero bytes)."""
        ...

    def on_job_complete(self, job: "Job", snapshot_created: bool) -> None:
        """Llamado cuando el Job finaliza completamente."""


class NullObserver:
    """Implementación no-op útil para tests unitarios o ejecución desatendida."""

    def on_hash_start(self, filename: str, total_bytes: int) -> None:
        pass

    def on_hash_progress(self, current_bytes: int, total_bytes: int) -> None:
        pass

    def on_hash_complete(self, filename: str, md5sum: str) -> None:
        pass

    def on_job_start(self, job: "Job", total_payloads: int) -> None:
        pass

    def on_archive_file_start(self, arc_path: str, size: int) -> None:
        pass

    def on_archive_file_end(
        self, arc_path: str, size: int, checksum: str | None
    ) -> None:
        pass

    def on_payload_start(
        self, payload: "Payload", current_part: int, total_parts: int
    ) -> None:
        pass

    def on_payload_progress(self, current_bytes: int, total_bytes: int) -> None:
        pass

    def on_payload_complete(self, payload: "Payload") -> None:
        pass

    def on_pause_start(self, total_seconds: int, reason: str) -> None:
        pass

    def on_pause_tick(self, remaining_seconds: int, total_seconds: int) -> None:
        pass

    def on_pause_end(self) -> None:
        pass

    def on_forward_success(self, job: "Job", pieces_count: int) -> None:
        pass

    def on_job_complete(self, job: "Job", snapshot_created: bool) -> None:
        pass


class ProgressMultiplexer:
    """Multiplexes Pyrogram upload progress to lease heartbeats and UI observers."""

    def __init__(self, heartbeat: "LeaseHeartbeat", observer: UploadObserver):
        self.heartbeat = heartbeat
        self.observer = observer

    def __call__(self, current: int, total: int, *args, **kwargs) -> None:
        success = self.heartbeat.pulse(current, total, *args, **kwargs)
        if not success:
            raise ConcurrencyError(
                "Database connection lost during transfer. Aborting upload."
            )

        self.observer.on_payload_progress(current, total)


class TarUploadObserverBridge(TarObserver):
    """Puente que traduce los eventos in-band de TarTape hacia el UploadObserver de toTelegram."""

    def __init__(self, observer: UploadObserver):
        self.observer = observer

    def on_file_start(self, event: TarFileStartEvent) -> None:
        self.observer.on_archive_file_start(
            arc_path=event.entry.info.arc_path,
            size=event.entry.info.size,
        )

    def on_file_end(self, event: TarFileEndEvent) -> None:
        # Validación Fail-Early de integridad en vuelo
        calculated = event.metadata.checksum
        expected = event.entry.info.checksum

        # TODO: la logica de esta comprobacion cuesta seguirla.
        if (
            event.metadata.is_complete and expected and calculated
        ) and calculated.lower() != expected.lower():
            raise TarIntegrityError(
                f"Discrepancia de integridad en vuelo para '{event.entry.info.arc_path}': "
                f"calculado {calculated}, esperado {expected}."
            )

        self.observer.on_archive_file_end(
            arc_path=event.entry.info.arc_path,
            size=event.entry.info.size,
            checksum=calculated,
        )

    def on_tape_completed(self, event: TarTapeCompletedEvent) -> None:
        pass
