from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from totelegram.concurrency import LeaseHeartbeat
    from totelegram.models import Job, Payload


@runtime_checkable
class UploadObserver(Protocol):
    """Protocolo que define los eventos del ciclo de vida de la subida."""

    def on_job_start(self, job: "Job", total_payloads: int) -> None:
        """Llamado cuando un Job inicia su procesamiento."""
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
        ...


class NullObserver:
    """Implementación no-op útil para tests unitarios o ejecución desatendida."""

    def on_job_start(self, job: "Job", total_payloads: int) -> None:
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
    """
    Multiplexor para Pyrogram:
    Recibe (current, total) desde send_document() y despacha simultáneamente:
      1. El latido al LeaseHeartbeat (renovación de lease en DB con throttling).
      2. La notificación de bytes al UploadObserver (cálculo de velocidad y dibujo en UI).
    """

    def __init__(
        self,
        heartbeat: "LeaseHeartbeat",
        observer: UploadObserver,
    ):
        self.heartbeat = heartbeat
        self.observer = observer

    def __call__(self, current: int, total: int, *args, **kwargs) -> None:

        self.heartbeat.pulse(current, total, *args, **kwargs)

        self.observer.on_payload_progress(current, total)
