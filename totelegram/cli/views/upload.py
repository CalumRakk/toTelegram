import logging
from pathlib import Path
from typing import Literal

from rich.markup import escape

from totelegram.cli.ui import UI, console
from totelegram.exceptions import (
    IncompatibleCapacityError,
    PermissionDeniedCleanupError,
)
from totelegram.i18n import _, _n
from totelegram.schemas import Commands, ScanReport

ui_logger = logging.getLogger("totelegram.ui")


class DisplayUpload:
    """Componentes visuales para el ciclo de subida de archivos y carpetas."""

    @classmethod
    def show_backup_header(cls, folder_name: str, index: int, total: int):
        """Muestra el encabezado de procesamiento de una carpeta en el backup."""
        if index > 1:
            UI.separator()

        prefix = f"[dim]{index}/{total}[/] " if total > 1 else ""
        header_text = _("Archivando: [bold cyan]{name}[/]").format(name=folder_name)
        UI.print(
            f"{prefix}{header_text}",
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
            UI.success(_("Sin archivos omitidos."))

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
            msg = _("Omitido (Ilegible/Error): {name}").format(name=escape(p.name))
            UI.error(msg)

        for p in report.skipped_by_integrity:
            msg = _("Omitido (Integridad): {name}").format(name=escape(p.name))
            UI.error(msg)

        for p in report.skipped_by_snapshot:
            msg = _("[bright_black]Omitido (Ya tiene Snapshot):[/] {name}").format(
                name=escape(p.name)
            )
            UI.warn(msg)

        for p in report.skipped_by_size:
            msg = _("[bright_black]Omitido (Excede peso máximo):[/] {name}").format(
                name=escape(p.name)
            )
            UI.error(msg)

        for p in report.skipped_by_exclusion:
            msg = _("[bright_black]Omitido (Patrón de exclusión):[/] {name}").format(
                name=escape(p.name)
            )
            UI.info(msg)

        for p in report.skipped_by_empty:
            msg = _("[bright_black]Omitida (Carpeta vacía):[/] {name}").format(
                name=escape(p.name)
            )
            UI.info(msg)

    @classmethod
    def _show_consolidated(
        cls, report: ScanReport, item_type: str, skip_title: bool = False
    ):
        if not skip_title:
            is_folder = str(item_type).lower() in ("carpeta", "folder")
            if is_folder:
                title_msg = _n(
                    "Se excluyó [bold]{n}[/] carpeta por:",
                    "Se excluyeron [bold]{n}[/] carpetas por:",
                    report.total_skipped,
                ).format(n=report.total_skipped)
            else:
                title_msg = _n(
                    "Se excluyó [bold]{n}[/] archivo por:",
                    "Se excluyeron [bold]{n}[/] archivos por:",
                    report.total_skipped,
                ).format(n=report.total_skipped)

            UI.print(title_msg, spacing="top")

        configs = [
            (
                report.skipped_by_error,
                _("Errores de integridad/lectura"),
                "dark_red",
            ),
            (report.skipped_by_snapshot, _("Ya tienen Snapshot"), "dark_blue"),
            (report.skipped_by_size, _("Exceden peso máximo"), "dark_red"),
            (report.skipped_by_exclusion, _("Patrón de exclusión"), "dark_blue"),
            (report.skipped_by_empty, _("Carpetas sin contenido"), "dark_blue"),
            (report.skipped_by_integrity, _("Integridad"), "dark_red"),
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
            more_str = _("... y {n} más.").format(n=count - 3)
            console.print(f"     [bright_black]{more_str}[/]")
            ui_logger.info(f"     {more_str}")

    @staticmethod
    def show_integrity_advice(report: ScanReport):
        if not report.skipped_by_integrity:
            return

        count = len(report.skipped_by_integrity)
        if count == 1:
            name = report.skipped_by_integrity[0].name
            msg = _(
                "La carpeta [bold]'{name}'[/] ha cambiado desde el último escaneo."
            ).format(name=name)
            subject_text = _("esta carpeta")
        else:
            msg = _(
                "Se detectaron [bold]{count}[/] carpetas con cambios recientes en su contenido."
            ).format(count=count)
            subject_text = _("estas carpetas")

        UI.warn(msg)
        UI.print(
            _(
                "[dim]El backup de {subject} no es confiable en este momento porque "
                "su índice local está desincronizado con el disco.[/]"
            ).format(subject=subject_text)
        )

    @staticmethod
    def show_incompatible_capacity_panel(
        err: IncompatibleCapacityError,
        filename: str | None = None,
    ):
        """Muestra un panel visual explicando que la pieza pendiente supera la capacidad de la cuenta."""
        size_to_report = err.pending_payload_size or err.job_chunk_size
        part_mb = size_to_report // (1024 * 1024)
        acc_mb = err.account_max_size // (1024 * 1024)
        account_type = _("Premium") if err.is_premium else _("Estándar")

        file_desc = (
            _(" para '[bold]{name}[/]'").format(name=filename) if filename else ""
        )

        msg = _(
            "Omitiendo subida{file_desc}: la siguiente parte pendiente mide [bold]{part_mb} MB[/].\n"
            "La cuenta activa actual es [bold]{account_type}[/] y solo puede transferir bloques de hasta [bold]{acc_mb} MB[/].\n\n"
        ).format(
            file_desc=file_desc,
            part_mb=part_mb,
            account_type=account_type,
            acc_mb=acc_mb,
        )
        UI.warn(message=msg)

    @staticmethod
    def show_permission_denied_panel(err: PermissionDeniedCleanupError):
        """Muestra un panel visual advirtiendo la falta de permisos de eliminación."""
        msg = _(
            "Se requiere limpiar o reorganizar mensajes antiguos en el chat [bold]{chat_id}[/], "
            "pero la cuenta activa carece del permiso de administrador [bold red]'Eliminar mensajes'[/] (can_delete_messages).\n\n"
            "[dim]toTelegram se detiene aquí para proteger la higiene del canal y no dejar trozos huérfanos abandonados.[/dim]"
        ).format(chat_id=err.chat_id)

        UI.educational_tip(
            message=msg,
            title=_("Permisos Insuficientes en Telegram"),
            commands=[
                "# "
                + _(
                    "Otorga a esta cuenta el permiso 'Eliminar mensajes' en los ajustes de administradores del canal."
                ),
                "totelegram send ... --use <cuenta_admin>  # "
                + _("Usar una cuenta que tenga permisos de administración"),
            ],
            spacing="block",
            border_style="red",
        )


class DisplayIndexer:
    """Avisos para la indexación de cintas en segundo plano."""

    @staticmethod
    def show_unindexed_notice(count: int):
        items_str = _n("cinta archivada", "cintas archivadas", count)
        message = _(
            "Detectadas [bold yellow]{count}[/] {items} en Telegram que aún no están indexadas localmente.\n"
            "Para habilitar la búsqueda quirúrgica de sus archivos internos, ejecuta:"
        ).format(count=count, items=items_str)

        UI.educational_tip(
            message=message,
            title=_("Catálogo desincronizado"),
            commands=[f"{Commands.CLI_BIN or 'totelegram'} index"],
            spacing="block",
            border_style="yellow",
        )
