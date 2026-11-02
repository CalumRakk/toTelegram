from pathlib import Path
from typing import TYPE_CHECKING

from rich.table import Table
from tartape.units import format_size

from totelegram.cli.ui import UI, console, log_table
from totelegram.packaging.auditor import AuditStatus

if TYPE_CHECKING:
    from totelegram.packaging.auditor import AuditItem


class DisplayLedger:
    """Componentes visuales para la auditoría de directorios y liberación de espacio (Prune)."""

    @staticmethod
    def show_directory_status(
        dir_path: Path,
        audit_results: list["AuditItem"],
        target_chat_id: int | str | None = None,
    ) -> None:
        if not audit_results:
            UI.info(
                f"El directorio '[bold]{dir_path.name}[/]' no contiene elementos para auditar."
            )
            return

        table = Table(
            title=f"Estado de Respaldo Local — {dir_path.name}",
            show_header=True,
            header_style="bold cyan",
            title_style="bold blue",
            expand=True,
        )
        table.add_column("Elemento", style="white")
        table.add_column("Tamaño", justify="right", style="dim")
        table.add_column("Destinos en Telegram", style="dim")
        table.add_column("Estado", justify="center")

        for res in audit_results:
            # Tamaño: para directorios neutrales no mostramos tamaño
            if res.status == AuditStatus.DIRECTORY:
                size_str = "[dim]--[/dim]"
            else:
                size_str = format_size(res.size)

            # Destinos en Telegram
            if res.destinations:
                dest_str = ", ".join(res.destinations)
            else:
                dest_str = "[dim]--[/dim]"

            # Matriz de estados acordada
            if res.status == AuditStatus.DIRECTORY:
                status_str = "[dim]Directorio[/dim]"
            elif res.status == AuditStatus.TAPE_BACKUP:
                status_str = "[bold green]✓ Cinta TAR[/bold green]"
            elif res.status == AuditStatus.LOCAL_SNAPSHOT:
                status_str = "[cyan]✓ Respaldado (Snapshot local)[/cyan]"
            elif res.status == AuditStatus.REMOTE_ONLY:
                status_str = "[dim red]☁ Solo en Telegram[/dim red]"
            elif res.status == AuditStatus.MODIFIED:
                status_str = "[bold yellow]! Modificado localmente[/bold yellow]"
            elif res.status == AuditStatus.BACKED_UP:
                status_str = "[bold green]✓ Respaldado[/bold green]"
            else:  # AuditStatus.PENDING
                status_str = "[dim]Pendiente de subida[/dim]"

            table.add_row(res.filename, size_str, dest_str, status_str)

        console.print(table)
        log_table(table, f"Estado de Respaldo Local — {dir_path.name}")

    @staticmethod
    def show_prune_candidates(candidates: list["AuditItem"], total_bytes: int) -> None:
        table = Table(
            title="Archivos Respaldados Listos para Liberar Espacio",
            show_header=True,
            header_style="bold green",
            title_style="bold green",
            expand=True,
        )
        table.add_column("Archivo a eliminar", style="bold white")
        table.add_column("Tamaño", justify="right", style="dim cyan")
        table.add_column("Verificado en Telegram", style="dim")

        for c in candidates:
            dest_str = ", ".join(c.destinations) if c.destinations else "[dim]--[/dim]"
            table.add_row(c.filename, format_size(c.size), dest_str)

        console.print(table)
        UI.info(
            f"Total de espacio a recuperar: [bold cyan]{format_size(total_bytes)}[/] en [bold]{len(candidates)}[/] archivos."
        )

    @staticmethod
    def show_prune_summary(deleted_count: int, freed_bytes: int) -> None:
        UI.success(
            f"Liberación completada: Se eliminaron [bold]{deleted_count}[/] archivos locales "
            f"recuperando [bold cyan]{format_size(freed_bytes)}[/] de espacio en disco."
        )
        UI.tip(
            "El registro se conserva en la base de datos local ('☁ Solo en Telegram'). "
            "Puedes restaurar cualquiera de estos archivos cuando lo necesites usando 'totelegram download'."
        )
