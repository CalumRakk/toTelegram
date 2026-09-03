from typing import TYPE_CHECKING, Optional

from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from totelegram.cli.ui import UI, console

if TYPE_CHECKING:
    from totelegram.models import Job, Payload


class RichUploadObserver:
    """
    Implementación visual de UploadObserver utilizando Rich.
    Maneja barras de progreso reactivas, cálculo de velocidad/ETA y cuentas regresivas de pausas.
    """

    def __init__(self):
        self._progress: Optional[Progress] = None
        self._current_task_id = None
        self._pause_status = None

    def on_job_start(self, job: "Job", total_payloads: int) -> None:
        pass

    def on_payload_start(
        self, payload: "Payload", current_part: int, total_parts: int
    ) -> None:
        """Inicia la barra de progreso visual para la pieza que se va a subir."""
        if self._progress is None:
            self._progress = Progress(
                SpinnerColumn(),
                TextColumn("[bold cyan]{task.fields[label]}[/]"),
                TextColumn("[white]{task.fields[filename]}[/]"),
                BarColumn(bar_width=None),
                "[progress.percentage]{task.percentage:>3.0f}%",
                "•",
                DownloadColumn(),
                "•",
                TransferSpeedColumn(),
                "•",
                TimeRemainingColumn(),
                console=console,
                transient=True,  # Limpia la barra al completarse para dejar espacio al log de éxito
            )
            self._progress.start()

        label = f"[{current_part}/{total_parts}]" if total_parts > 1 else "[1/1]"
        self._current_task_id = self._progress.add_task(
            description="upload",
            total=payload.size,
            filename=payload.filename,
            label=label,
        )

    def on_payload_progress(self, current_bytes: int, total_bytes: int) -> None:
        """Actualiza la barra con la transferencia continua de bytes."""
        if self._progress is not None and self._current_task_id is not None:
            self._progress.update(
                self._current_task_id,
                completed=current_bytes,
                total=total_bytes,
            )

    def on_payload_complete(self, payload: "Payload") -> None:
        """Detiene la barra de la pieza actual y confirma la subida exitosa."""
        if self._progress is not None:
            if self._current_task_id is not None:
                self._progress.remove_task(self._current_task_id)
                self._current_task_id = None
            self._progress.stop()
            self._progress = None

        UI.success(f"Pieza [bold]{payload.filename}[/] subida y verificada.")

    def on_pause_start(self, total_seconds: int, reason: str) -> None:
        """Inicia una pausa visual activa."""
        mins = total_seconds // 60
        secs = total_seconds % 60
        dur_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
        UI.info(f"Iniciando pausa de seguridad ({dur_str}) [{reason}]...")

    def on_pause_tick(self, remaining_seconds: int, total_seconds: int) -> None:
        """Actualiza la cuenta regresiva en vivo segundo a segundo."""
        mins = remaining_seconds // 60
        secs = remaining_seconds % 60
        time_str = f"{mins:02d}:{secs:02d}"

        console.print(
            f"  [italic blue] Pausa activa: Siguiente operación en {time_str}... [dim](Lease renovado)[/][/]",
            end="\r",
        )

    def on_pause_end(self) -> None:
        """Limpia el mensaje de pausa."""
        console.print(" " * 80, end="\r")  # Limpia la línea
        UI.success("Pausa finalizada. Reanudando operaciones.")

    def on_forward_success(self, job: "Job", pieces_count: int) -> None:
        """Confirma la finalización instantánea por Smart Forward."""
        UI.success(
            f"Smart Forward exitoso: [bold]{pieces_count}[/] piezas vinculadas instantáneamente (0 bytes transferidos)."
        )

    def on_job_complete(self, job: "Job", snapshot_created: bool) -> None:
        """Notifica el cierre de un Job."""
        if snapshot_created:
            UI.info("Snapshot local generado e indexado.")
