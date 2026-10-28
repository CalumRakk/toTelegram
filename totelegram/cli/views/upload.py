import logging
from pathlib import Path
from typing import Literal

from rich.markup import escape

from totelegram.cli.ui import UI, console
from totelegram.schemas import Commands, ScanReport
from totelegram.types import IncompatibleCapacityError, PermissionDeniedCleanupError

ui_logger = logging.getLogger("totelegram.ui")


class DisplayUpload:
    """Componentes visuales para el ciclo de subida de archivos y carpetas."""

    @classmethod
    def show_backup_header(cls, folder_name: str, index: int, total: int):
        """Muestra el encabezado de procesamiento de una carpeta en el backup."""
        if index > 1:
            UI.separator()

        prefix = f"[dim]{index}/{total}[/] " if total > 1 else ""
        UI.print(
            f"{prefix}Archivando: [bold cyan]{folder_name}[/]",
            highlight=False,
            indent=False,
        )

    @classmethod
    def show_internal_scan_result(cls, report: ScanReport):
        """Informa sobre el resultado del escaneo interno de la carpeta."""
        if report.total_skipped > 0:
            cls.show_skip_report(
                report, item_type="archivo", force_verbose=False, skip_title=True
            )
        else:
            UI.success("Sin archivos omitidos.")

    @classmethod
    def show_skip_report(
        cls,
        scan_report: ScanReport,
        item_type: Literal["archivo", "carpeta"] = "archivo",
        force_verbose: bool | None = None,
        skip_title: bool = False,
    ) -> None:
        """Informa al usuario sobre los ítems omitidos en el inventario."""
        total_skipped = scan_report.total_skipped
        if total_skipped == 0:
            return

        is_verbose = (
            force_verbose if force_verbose is not None else (total_skipped <= 5)
        )

        if is_verbose:
            cls._show_detailed(scan_report, item_type)
        else:
            cls._show_consolidated(scan_report, item_type, skip_title)

    @classmethod
    def _show_detailed(cls, report: ScanReport, item_type: str):
        for p in report.skipped_by_error:
            UI.error(f"Omitido (Ilegible/Error): {escape(p.name)}")

        for p in report.skipped_by_integrity:
            UI.error(f"Omitido (Integridad): {escape(p.name)}")

        for p in report.skipped_by_snapshot:
            UI.warn(f"[bright_black]Omitido (Ya tiene Snapshot):[/] {escape(p.name)}")

        for p in report.skipped_by_size:
            UI.error(f"[bright_black]Omitido (Excede peso máximo):[/] {escape(p.name)}")

        for p in report.skipped_by_exclusion:
            UI.info(f"[bright_black]Omitido (Patrón de exclusión):[/] {escape(p.name)}")

        for p in report.skipped_by_empty:
            UI.info(f"[bright_black]Omitida (Carpeta vacía):[/] {escape(p.name)}")

    @classmethod
    def _show_consolidated(
        cls, report: ScanReport, item_type: str, skip_title: bool = False
    ):
        if not skip_title:
            plural = f"{item_type}s"
            UI.print(
                f"Se excluyeron [bold]{report.total_skipped}[/] {plural} por:",
                spacing="top",
            )

        configs = [
            (report.skipped_by_error, "Errores de integridad/lectura", "dark_red"),
            (report.skipped_by_snapshot, "Ya tienen Snapshot", "dark_blue"),
            (report.skipped_by_size, "Exceden peso máximo", "dark_red"),
            (report.skipped_by_exclusion, "Patrón de exclusión", "dark_blue"),
            (report.skipped_by_empty, "Carpetas sin contenido", "dark_blue"),
            (report.skipped_by_integrity, "Integridad", "dark_red"),
        ]

        for items, label, style in configs:
            if items:
                patterns = report.exclusion_patterns if "Patrón" in label else None
                cls._print_block(label, style, items, patterns)

    @classmethod
    def _print_block(
        cls, label: str, style: str, files: list[Path], current_patterns=None
    ):
        count = len(files)
        pat_str = (
            f" [bright_black]({', '.join(current_patterns)})[/]"
            if current_patterns
            else ""
        )

        header_line = f"  • {label}: {count}" + (
            f" ({', '.join(current_patterns)})" if current_patterns else ""
        )
        console.print(f"  [bold {style}]• {label}:[/] [white]{count}[/]{pat_str}")
        ui_logger.info(header_line)

        for f in files[:3]:
            console.print(f"     [bright_black]- {escape(f.name)}[/]", highlight=False)
            ui_logger.info(f"     - {f.name}")

        if count > 3:
            console.print(f"     [bright_black]... y {count - 3} más.[/]")
            ui_logger.info(f"     ... y {count - 3} más.")

    @staticmethod
    def show_integrity_advice(report: ScanReport):
        if not report.skipped_by_integrity:
            return

        count = len(report.skipped_by_integrity)
        if count == 1:
            name = report.skipped_by_integrity[0].name
            msg = f"La carpeta [bold]'{name}'[/] ha cambiado desde el último escaneo."
        else:
            msg = f"Se detectaron [bold]{count}[/] carpetas con cambios recientes en su contenido."

        subject_text = "estas carpetas" if count > 1 else "esta carpeta"
        UI.warn(msg)
        UI.print(
            f"[dim]El backup de {subject_text} no es confiable en este momento porque "
            "su índice local está desincronizado con el disco.[/]"
        )

    @staticmethod
    def show_incompatible_capacity_panel(err: IncompatibleCapacityError):
        """Muestra un panel visual explicando la degradación de capacidad de la cuenta."""
        job_mb = err.job_chunk_size // (1024 * 1024)
        acc_mb = err.account_max_size // (1024 * 1024)
        account_type = "Premium" if err.is_premium else "Estándar"

        msg = (
            f"Este archivo tiene una subida pendiente iniciada con bloques de [bold]{job_mb} MB[/].\n"
            f"La cuenta activa actual es [bold]{account_type}[/] y solo puede transferir bloques de hasta [bold]{acc_mb} MB[/].\n\n"
            f"[dim]Para mantener la integridad matemática de los fragmentos, no se pueden mezclar trozos de diferente tamaño.[/dim]"
        )
        UI.educational_tip(
            message=msg,
            title="Incompatibilidad de Capacidad",
            commands=[
                "totelegram switch <cuenta_premium>    # Cambiar a una cuenta con capacidad suficiente",
                "totelegram send ... --force           # Descartar intento previo y reiniciar con la capacidad actual",
            ],
            spacing="block",
            border_style="yellow",
        )

    @staticmethod
    def show_permission_denied_panel(err: PermissionDeniedCleanupError):
        """Muestra un panel visual advirtiendo la falta de permisos de eliminación."""
        msg = (
            f"Se requiere limpiar o reorganizar mensajes antiguos en el chat [bold]{err.chat_id}[/], "
            f"pero la cuenta activa carece del permiso de administrador [bold red]'Eliminar mensajes'[/] (can_delete_messages).\n\n"
            f"[dim]toTelegram se detiene aquí para proteger la higiene del canal y no dejar trozos huérfanos abandonados.[/dim]"
        )
        UI.educational_tip(
            message=msg,
            title="Permisos Insuficientes en Telegram",
            commands=[
                "# Otorga a esta cuenta el permiso 'Eliminar mensajes' en los ajustes de administradores del canal.",
                "totelegram send ... --use <cuenta_admin>  # Usar una cuenta que tenga permisos de administración",
            ],
            spacing="block",
            border_style="red",
        )


class DisplayIndexer:
    """Avisos para la indexación de cintas en segundo plano."""

    @staticmethod
    def show_unindexed_notice(count: int):
        plural = "cintas archivadas" if count > 1 else "cinta archivada"
        message = (
            f"Detectadas [bold yellow]{count}[/] {plural} en Telegram que aún no están indexadas localmente.\n"
            "Para habilitar la búsqueda quirúrgica de sus archivos internos, ejecuta:"
        )
        UI.educational_tip(
            message=message,
            title="Catálogo desincronizado",
            commands=[f"{Commands.CLI_BIN or 'totelegram'} index"],
            spacing="block",
            border_style="yellow",
        )
