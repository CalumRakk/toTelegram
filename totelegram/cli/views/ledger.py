from pathlib import Path
from typing import TYPE_CHECKING

from rich.table import Table
from tartape.units import format_size

from totelegram.cli.ui import UI, console, log_table
from totelegram.i18n import _, _n
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
                _(
                    "El directorio '[bold]{name}[/]' no contiene elementos para auditar."
                ).format(name=dir_path.name)
            )
            return

        table_title = _("Estado de Respaldo Local — {name}").format(name=dir_path.name)
        table = Table(
            title=table_title,
            show_header=True,
            header_style="bold cyan",
            title_style="bold blue",
            expand=True,
        )
        table.add_column(_("Elemento"), style="white")
        table.add_column(_("Tamaño"), justify="right", style="dim")
        table.add_column(_("Destinos en Telegram"), style="dim")
        table.add_column(_("Estado"), justify="center")

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

            # Matriz de estados traducida
            if res.status == AuditStatus.DIRECTORY:
                status_str = _("[dim]Directorio[/dim]")
            elif res.status == AuditStatus.TAPE_BACKUP:
                status_str = _("[bold green]✓ Cinta TAR[/bold green]")
            elif res.status == AuditStatus.LOCAL_SNAPSHOT:
                status_str = _("[cyan]✓ Respaldado (Snapshot local)[/cyan]")
            elif res.status == AuditStatus.REMOTE_ONLY:
                status_str = _("[dim red]☁ Solo en Telegram[/dim red]")
            elif res.status == AuditStatus.MODIFIED:
                status_str = _("[bold yellow]! Modificado localmente[/bold yellow]")
            elif res.status == AuditStatus.BACKED_UP:
                status_str = _("[bold green]✓ Respaldado[/bold green]")
            else:  # AuditStatus.PENDING
                status_str = _("[dim]Pendiente de subida[/dim]")

            table.add_row(res.filename, size_str, dest_str, status_str)

        console.print(table)
        log_table(table, table_title)

    @staticmethod
    def show_prune_candidates(candidates: list["AuditItem"], total_bytes: int) -> None:
        table_title = _("Archivos Respaldados Listos para Liberar Espacio")
        table = Table(
            title=table_title,
            show_header=True,
            header_style="bold green",
            title_style="bold green",
            expand=True,
        )
        table.add_column(_("Archivo a eliminar"), style="bold white")
        table.add_column(_("Tamaño"), justify="right", style="dim cyan")
        table.add_column(_("Verificado en Telegram"), style="dim")

        for c in candidates:
            dest_str = ", ".join(c.destinations) if c.destinations else "[dim]--[/dim]"
            table.add_row(c.filename, format_size(c.size), dest_str)

        console.print(table)
        UI.info(
            _n(
                "Total de espacio a recuperar: [bold cyan]{size}[/] en [bold]{n}[/] archivo.",
                "Total de espacio a recuperar: [bold cyan]{size}[/] en [bold]{n}[/] archivos.",
                len(candidates),
            ).format(size=format_size(total_bytes), n=len(candidates))
        )

    @staticmethod
    def show_prune_summary(deleted_count: int, freed_bytes: int) -> None:
        UI.success(
            _n(
                "Liberación completada: Se eliminó [bold]{n}[/] archivo local "
                "recuperando [bold cyan]{size}[/] de espacio en disco.",
                "Liberación completada: Se eliminaron [bold]{n}[/] archivos locales "
                "recuperando [bold cyan]{size}[/] de espacio en disco.",
                deleted_count,
            ).format(n=deleted_count, size=format_size(freed_bytes))
        )
        UI.tip(
            _(
                "El registro se conserva en la base de datos local ('☁ Solo en Telegram'). "
                "Puedes restaurar cualquiera de estos archivos cuando lo necesites usando 'totelegram download'."
            )
        )
