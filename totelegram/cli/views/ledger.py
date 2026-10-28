from pathlib import Path
from typing import TYPE_CHECKING

from rich.table import Table
from tartape.units import format_size

from totelegram.cli.ui import UI, console, log_table

if TYPE_CHECKING:
    from totelegram.packaging.ledger import FileAuditResult


class DisplayLedger:
    """Componentes visuales para la auditoría de directorios y liberación de espacio (Prune)."""

    @staticmethod
    def show_directory_status(
        dir_path: Path,
        audit_results: list["FileAuditResult"],
        target_chat_id: int | str | None = None,
    ) -> None:
        if not audit_results:
            UI.info(
                f"El directorio '[bold]{dir_path.name}[/]' no contiene archivos para auditar."
            )
            return

        table = Table(
            title=f"Estado de Respaldo Local — {dir_path.name}",
            show_header=True,
            header_style="bold cyan",
            title_style="bold blue",
            expand=True,
        )
        table.add_column("Archivo", style="white")
        table.add_column("Tamaño", justify="right", style="dim")
        table.add_column("Destinos en Telegram", style="dim")
        table.add_column("Estado", justify="center")

        for res in audit_results:
            size_str = format_size(res.size)

            if res.destinations:
                dest_labels = []
                for cid, dest in res.destinations.items():
                    title = dest.chat_title or f"Chat {cid}"
                    dest_labels.append(f"{title} [dim]({dest.total_parts}p)[/]")
                dest_str = ", ".join(dest_labels)
            else:
                dest_str = "[dim]--[/]"

            if not res.is_on_disk:
                status_str = "[dim red]☁ Solo en Telegram[/dim red]"
            elif res.is_modified:
                status_str = "[bold yellow]! Modificado localmente[/bold yellow]"
            elif res.is_backed_up:
                status_str = "[bold green]✓ Respaldado[/bold green]"
            else:
                status_str = "[dim]Pendiente de subida[/dim]"

            table.add_row(res.filename, size_str, dest_str, status_str)

        console.print(table)
        log_table(table, f"Estado de Respaldo Local — {dir_path.name}")

    @staticmethod
    def show_prune_candidates(
        candidates: list["FileAuditResult"], total_bytes: int
    ) -> None:
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
            dest_titles = [
                d.chat_title or str(cid) for cid, d in c.destinations.items()
            ]
            table.add_row(c.filename, format_size(c.size), ", ".join(dest_titles))

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
            "El recibo '.totelegram.json' se ha conservado intacto. "
            "Puedes restaurar cualquiera de estos archivos cuando lo necesites usando 'totelegram download'."
        )
