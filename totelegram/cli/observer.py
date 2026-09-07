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
                transient=True,
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
        console.print(" " * 80, end="\r")
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


class RichDownloadObserver:
    """
    Implementación visual de DownloadObserver utilizando Rich.
    Maneja barras de progreso para descarga de piezas de Telegram y extracción de archivos.
    """

    def __init__(self):
        self._dl_progress: Optional[Progress] = None
        self._extract_progress: Optional[Progress] = None
        self._dl_task_id = None
        self._extract_task_id = None

    def on_part_skipped(self, part_index: int, total_parts: int, filename: str):
        label = f"[{part_index}/{total_parts}]" if total_parts > 1 else "[1/1]"
        UI.info(
            f"{label} Pieza [bold]{filename}[/] ya en disco y verificada [dim](Reutilizada)[/dim]."
        )

    def on_part_download_start(
        self, part_index: int, total_parts: int, filename: str, total_bytes: int
    ):
        if self._dl_progress is None:
            self._dl_progress = Progress(
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
                transient=True,
            )
            self._dl_progress.start()

        label = f"[{part_index}/{total_parts}]" if total_parts > 1 else "[1/1]"
        self._dl_task_id = self._dl_progress.add_task(
            description="download",
            total=total_bytes,
            filename=filename,
            label=label,
        )

    def on_part_download_progress(self, current_bytes: int, total_bytes: int):
        if self._dl_progress is not None and self._dl_task_id is not None:
            self._dl_progress.update(
                self._dl_task_id,
                completed=current_bytes,
                total=total_bytes,
            )

    def on_part_download_complete(self, part_index: int, filename: str):
        if self._dl_progress is not None:
            if self._dl_task_id is not None:
                self._dl_progress.remove_task(self._dl_task_id)
                self._dl_task_id = None
            self._dl_progress.stop()
            self._dl_progress = None

        UI.success(f"Pieza [bold]{filename}[/] descargada y verificada (MD5 OK).")

    def on_extraction_start(self, total_files: int):
        console.print()
        UI.info(
            f"Iniciando extracción y verificación de [bold]{total_files}[/] archivos..."
        )
        if self._extract_progress is None:
            self._extract_progress = Progress(
                SpinnerColumn(),
                TextColumn("[bold green]Extrayendo:[/]"),
                TextColumn("[white]{task.fields[filename]}[/]"),
                BarColumn(bar_width=None),
                "[progress.percentage]{task.percentage:>3.0f}%",
                "•",
                TextColumn("[dim]({task.completed}/{task.total})[/]"),
                console=console,
                transient=True,
            )
            self._extract_progress.start()

        self._extract_task_id = self._extract_progress.add_task(
            description="extract",
            total=total_files,
            filename="Iniciando...",
        )

    def on_file_extracted(
        self, relative_path: str, size: int, current_file_idx: int, total_files: int
    ):
        if self._extract_progress is not None and self._extract_task_id is not None:
            display_name = (
                relative_path
                if len(relative_path) <= 40
                else "..." + relative_path[-37:]
            )
            self._extract_progress.update(
                self._extract_task_id,
                completed=current_file_idx,
                filename=display_name,
            )

    def on_extraction_complete(self, total_files: int):
        if self._extract_progress is not None:
            if self._extract_task_id is not None:
                self._extract_progress.remove_task(self._extract_task_id)
                self._extract_task_id = None
            self._extract_progress.stop()
            self._extract_progress = None

        UI.success(
            f"Extracción completada: [bold]{total_files}[/] archivos validados al 100%."
        )
