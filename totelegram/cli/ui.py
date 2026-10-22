import logging
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, List, Literal

import typer
from rich.columns import Columns
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.theme import Theme
from rich.tree import Tree
from tartape.units import format_size

from totelegram.database import (
    DatabaseSession,
    db_proxy,
)
from totelegram.identity import Profile, Settings, SettingsManager
from totelegram.models import TelegramChat
from totelegram.packaging.importer import ImportPlan, PartAction, PlanAction
from totelegram.schemas import (
    COLORS,
    VALUE_NOT_SET,
    AccessLevel,
    Commands,
    ScanReport,
    Strategy,
)
from totelegram.telegram.types import ChatMatch

Spacing = Literal["top", "bottom", "block"] | None

custom_theme = Theme(
    {
        "info": "dim cyan",
        "warning": "orange1",
        "error": "bold red",
        "success": "bold green",
        "progress": "italic blue",
    }
)

console = Console(theme=custom_theme, force_terminal=True, legacy_windows=True)
ui_logger = logging.getLogger("totelegram.ui")


def strip_markup(text: Any) -> str:
    """Elimina las etiquetas de color [bold], [red], etc. para el archivo de log."""
    try:
        return Text.from_markup(str(text)).plain
    except Exception:
        # Fallback si el texto contiene corchetes que no son etiquetas válidas de Rich
        return re.sub(r"\[/?[a-zA-Z0-9_# =]+\]", "", str(text))


def log_table(
    table: Table,
    title: str | None = None,
    logger: logging.Logger = ui_logger,
) -> None:
    """Vuelca una tabla de Rich en texto plano estructurado al logger."""
    try:
        t_title = title or (strip_markup(str(table.title)) if table.title else "Tabla")
        logger.info(f"=== {t_title} ===")
        if not table.columns:
            return

        headers = [strip_markup(col.header) for col in table.columns]
        logger.info(" | ".join(headers))

        num_rows = len(table.columns[0]._cells) if table.columns else 0
        for row_idx in range(num_rows):
            row_vals = [strip_markup(str(col._cells[row_idx])) for col in table.columns]
            logger.info("  " + " | ".join(row_vals))
    except Exception as e:
        logger.debug(f"Error al volcar tabla en log: {e}")


def get_friendly_chat_name(
    chat_id: str | int, db_path: Path | str | None = None
) -> str:
    """
    Devuelve un nombre amigable en formato 'Título (ID)'.
    Aprovecha la conexión activa o consulta la base de datos local SQLite.
    """
    val = str(chat_id).strip()

    if val in ("me", "self", VALUE_NOT_SET):
        return (
            "Mensajes Guardados (me)"
            if val in ("me", "self")
            else "[yellow]Pendiente[/]"
        )

    if val.startswith("@"):
        return val

    try:
        chat_int_id = int(val)
        if db_proxy.obj is not None and not db_proxy.is_closed():
            chat = TelegramChat.get_or_none(TelegramChat.id == chat_int_id)
            if chat and chat.title:
                return f"[bold cyan]{chat.title}[/] [dim]({chat.id})[/]"
        elif db_path:
            with DatabaseSession(db_path, auto_init_schema=False):
                chat = TelegramChat.get_or_none(TelegramChat.id == chat_int_id)
                if chat and chat.title:
                    return f"[bold cyan]{chat.title}[/] [dim]({chat.id})[/]"
    except Exception:  # noqa: S110
        pass

    return str(chat_id)


class UI:
    @classmethod
    def separator(cls, style: str = "bright_black"):
        console.print(Columns([Rule(style=style)], width=45))
        ui_logger.info("-" * 45)

    @staticmethod
    def sleep_progress(seconds: int):
        """Muestra una cuenta regresiva visual sin inundar el log."""
        if seconds <= 0:
            return

        UI.info(f"Iniciando pausa ({seconds // 60} min)...")

        with console.status("[bold blue]Pausa activa...", spinner="line") as status:
            for i in range(seconds, 0, -1):
                time.sleep(1)

                if i % 60 == 0 or i <= 10:
                    mins = i // 60
                    secs = i % 60
                    status.update(
                        f"[bold blue]Pausa activa: Siguiente parte en {mins}m {secs}s..."
                    )

        UI.success("Pausa finalizada. Reanudando subida.")

    @staticmethod
    def _print(
        message: str,
        *,
        indent: bool = False,
        spacing: Spacing = None,
        log_level: int = logging.INFO,
        **kwargs,
    ):
        """Imprime un mensaje con formato y opcionalmente agrega espacio antes o después."""
        if spacing in ("top", "block"):
            console.print()

        prefix = "  " if indent else ""
        console.print(f"{prefix}{message}", **kwargs)

        clean_msg = strip_markup(message).strip()
        if clean_msg:
            ui_logger.log(log_level, f"{prefix}{clean_msg}")

        if spacing in ("bottom", "block"):
            console.print()

    @staticmethod
    def print(message: str, *, indent: bool = True, spacing: Spacing = None, **kwargs):
        UI._print(
            f"{message}",
            indent=indent,
            spacing=spacing,
            log_level=logging.INFO,
            **kwargs,
        )

    @staticmethod
    def info(message: str, *, spacing: Spacing = None, **kwargs):
        UI._print(
            f"[info]i[/] {message}", spacing=spacing, log_level=logging.INFO, **kwargs
        )

    @staticmethod
    def success(message: str, *, spacing: Spacing = None, **kwargs):
        UI._print(
            f"[success]>[/] {message}",
            spacing=spacing,
            log_level=logging.INFO,
            **kwargs,
        )

    @staticmethod
    def warn(message: str, *, spacing: Spacing = None, **kwargs):
        UI._print(
            f"[warning]![/] {message}",
            spacing=spacing,
            log_level=logging.WARNING,
            **kwargs,
        )

    @staticmethod
    def error(message: str, *, spacing: Spacing = None, **kwargs):
        UI._print(
            f"[error]X[/] {message}", spacing=spacing, log_level=logging.ERROR, **kwargs
        )

    @staticmethod
    def tip(
        message: str,
        commands: List[str] | str | None = None,
        spacing: Spacing = None,
        **kwargs,
    ):
        """Muestra una sugerencia al usuario y la guarda en el log."""
        UI._print(f"[dim cyan]  Tip:[/] {message}", spacing=spacing, **kwargs)

        if commands:
            if isinstance(commands, str):
                commands = [commands]

            for command in commands:
                console.print(f"    [bold yellow]> {command}[/]")
                ui_logger.info(f"    > {strip_markup(command)}")

    @staticmethod
    def educational_tip(
        message: str,
        title: str | None = None,
        commands: List[str] | str | None = None,
        spacing: Spacing = None,
        border_style: str = "cyan",
    ):
        """Muestra una sugerencia educativa destacada en un recuadro (Panel) y la guarda en el log."""
        if spacing in ("top", "block"):
            console.print()

        content_lines = [message]

        if commands:
            content_lines.append("")
            if isinstance(commands, str):
                commands = [commands]

            for command in commands:
                content_lines.append(f"   [bold yellow]> {command}[/]")

        panel_content = "\n".join(content_lines)

        panel = Panel(
            panel_content,
            title=f"[bold]{title}[/]" if title else None,
            border_style=border_style,
            padding=(1, 1),
        )

        console.print(panel)

        title_prefix = f"[{strip_markup(title)}] " if title else ""
        ui_logger.info(f"{title_prefix}{strip_markup(message)}")
        if commands:
            if isinstance(commands, str):
                commands = [commands]
            for command in commands:
                ui_logger.info(f"   > {strip_markup(command)}")

        if spacing in ("bottom", "block"):
            console.print()

    @classmethod
    @contextmanager
    def loading(cls, message: str):
        ui_logger.info(f"[Iniciando] {strip_markup(message)}")
        with console.status(f"[bold green]{message}"):
            yield


class DisplayUpload:
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
        """Muestra una lista línea por línea para pocos ítems."""
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
        """Muestra bloques resumidos con ejemplos para muchos ítems."""
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
        """Helper para imprimir un bloque de resumen con 3 ejemplos."""
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
        UI.tip(
            "Si quieres archivarlas de todas formas, deberás forzar una subida completa "
            "(comando próximamente).",
        )


class DisplayConfig:
    @staticmethod
    def confirm_expanded_pattern(
        action: Literal["agregar", "eliminar"], key: str, values: List[str]
    ) -> bool:
        if not values:
            return False

        is_unique = len(values) == 1
        file_example = Path(values[0])

        cmd_example = f"{Commands.CONFIG_ADD_LIST if action == 'agregar' else Commands.CONFIG_REMOVE_LIST} {key} '*{file_example.suffix}'"

        if is_unique:
            UI.warn(
                f"No se detectó un patrón comodin (*), pero el valor coincide con el archivo existente: {file_example.name}"
            )
        else:
            UI.warn(
                f"No se detectó un patrón, pero se detectaron {len(values)} archivos existentes."
            )
            UI.print(
                "[i]Esto sugiere que tu terminal expandió un comodín antes de que el programa lo recibiera.[/]"
            )

        UI.tip(
            f"Para {action} un patrón que aplique a archivos futuros, usa comillas simples:",
            commands=[cmd_example],
            spacing="top",
        )

        indicative = (
            f"el archivo '{file_example.name}'"
            if is_unique
            else f"estos {len(values)} archivos"
        )

        return typer.confirm(
            f"\n¿Deseas {action} {indicative} literalmente en la configuración?",
            default=False,
        )

    @classmethod
    def _mark_sensitive(cls, value: int | str) -> str:
        """Ofusca un valor de tipo int o str."""
        if not isinstance(value, (str, int)):
            raise TypeError(
                "mark_sensitive solo admite valores de tipo str o int para enmascarar."
            )

        value = str(value)
        if len(value) <= 6:
            display_val = "•" * len(value)
        else:
            display_val = value[:3] + "•" * (len(value) - 4) + value[-3:]

        return display_val

    @classmethod
    def show_config_table(
        cls,
        profile_name: str,
        manager: SettingsManager,
        is_debug: bool,
        settings: Settings,
    ):
        UI.info(f"Configuración del perfil: [green]{profile_name}[/green]")
        table = Table(show_header=True, title_style=COLORS.TABLE_TITLE)
        table.add_column("Opción (Key)")
        table.add_column("Tipo")
        table.add_column("Valor Actual")
        table.add_column("Descripción")

        default_settings = Settings.get_default_settings()
        for field_name, default_value in default_settings.model_dump().items():
            info = Settings.get_info(field_name)
            if info is None:
                continue

            value = getattr(settings, field_name)
            is_value_default = value != default_value
            value_style = "bold green" if is_value_default else "dim white"

            if field_name.lower() == "chat_id":
                value = get_friendly_chat_name(value, manager.database_path)
                display_val = "?"

            elif info.is_sensitive and value is not None:
                display_val = cls._mark_sensitive(value)
            else:
                display_val = str(value)

            if is_debug and info.level == AccessLevel.DEBUG_READONLY:
                table.add_row(
                    f"[grey0]{field_name.lower()}[/]",
                    f"[grey0]{escape(info.type_annotation)}[/]",
                    f"[grey0]{display_val}[/]",
                    f"[grey0]{info.description}[/]",
                )
            elif is_debug or info.level == AccessLevel.EDITABLE:
                table.add_row(
                    field_name.lower(),
                    escape(info.type_annotation),
                    f"[{value_style}]{display_val}[/]",
                    info.description,
                )

        console.print(table)
        log_table(table, f"Configuración del perfil: {profile_name}")


class DisplayProfile:
    @staticmethod
    def announce_start_profile_creation(profile_name: str):
        UI.info(f"Creando perfil '[bold]{profile_name}[/]'.\n")
        UI.print("[bold cyan]1. Autenticación con Telegram[/bold cyan]", indent=False)
        UI.print(
            "[dim]Se solicitará tu [bold]número telefónico[/] y [bold]código (OTP)[/] para vincular la cuenta.[/dim]",
            indent=False,
            spacing="bottom",
        )

    @staticmethod
    def announce_profile_creation(profile_name: str):
        console.print()
        UI.info(f"Perfil '[bold]{profile_name}[/]' creado exitosamente.")

    @staticmethod
    def announce_start_destination_setup():
        console.print()
        UI.info("Configurando destino de subida (Chat ID).")
        UI.print(
            "[dim]Puedes omitir este paso y configurarlo más tarde usando 'totelegram config set chat_id <ID>'.[/dim]",
            indent=False,
            spacing="bottom",
        )

    @classmethod
    def render_profiles_table(
        cls,
        manager: SettingsManager,
        active: str | None,
        profiles: List[Profile],
        quiet: bool = False,
    ):
        console.print()
        if quiet:
            console.print("Perfiles disponibles:")
            ui_logger.info("Perfiles disponibles:")
            for profile in profiles:
                console.print(" - " + profile.name)
                ui_logger.info(f" - {profile.name}")
            return

        table = Table(
            title="Perfiles disponibles",
            title_style=COLORS.TABLE_TITLE,
        )
        table.add_column("Estado", no_wrap=True)
        table.add_column("Perfil")
        table.add_column("Session (.session)", justify="center")
        table.add_column("Config (.env)", justify="center")
        table.add_column("Destino (Chat ID)")

        was_orphan = False
        for profile in profiles:
            is_active = profile.name == active

            active_marker = "[bold green]*[/]" if is_active else ""
            auth_status = (
                "[green] OK [/]" if profile.has_session else "[red] MISSING [/]"
            )

            if profile.has_env:
                settings = manager.get_settings(profile.name)
                chat_id = settings.chat_id

                config_status = "[green] OK [/]"
                target_desc = (
                    f"[white]{chat_id}[/]"
                    if chat_id != VALUE_NOT_SET
                    else "[yellow]Pendiente[/]"
                )
            else:
                config_status = "[red] MISSING [/]"
                target_desc = "[dim]--[/]"

            if not profile.is_trinity:
                was_orphan = True

            table.add_row(
                active_marker, profile.name, auth_status, config_status, target_desc
            )

        console.print(table)
        log_table(table, "Perfiles disponibles")

        if was_orphan:
            console.print()
            UI.warn("Se detectó al menos un perfil huérfano.")
            UI.info(
                "Usa 'totelegram profile delete <PERFIL>' para limpiar archivos huérfanos"
            )

    @staticmethod
    def show_profile_conflict(profile: Profile):
        if profile.is_trinity:
            UI.warn("El perfil existe.")
        elif profile.has_session:
            UI.warn("Existe una sesión de Telegram huérfana con este nombre.")
        elif profile.has_env:
            UI.warn("Existe un archivo de configuración (.env) sin sesión asociada.")
            tip = f"[bold]totelegram --use {profile.name} config check[/]"
            UI.info(f"Ejecuta un diagnóstico usando: {tip}")

    @staticmethod
    def show_delete_hint(profile_name: str):
        tip = f"[bold]totelegram profile delete {profile_name}[/]"
        UI.info(f"Para empezar de cero, elimina los rastros usando: {tip}")

    @staticmethod
    def announce_profile_used(profile_name: str):
        UI.info(f"Perfil Actual: [bold]{profile_name}[/]")


class DisplayGeneric:
    @staticmethod
    def show_matches_summary(query: str, matches: List[ChatMatch]):
        base = "Exploré tus chats más recientes"
        if len(matches) > 0:
            message = (
                f"[green]Ok[/green] {base}, y "
                f"encontré [bold cyan]{len(matches)}[/bold cyan] "
                f"coincidencias para '[italic]{query}[/italic]':"
            )
        else:
            message = (
                f"[yellow]![/yellow] {base}, "
                f"pero [bold red]no encontré[/bold red] "
                f"nada que coincida con '[italic]{query}[/italic]'."
            )
        console.print(message)
        ui_logger.info(strip_markup(message))

    @staticmethod
    def show_search_tip():
        """Muestra el consejo de oro para encontrar chats nuevos."""
        tip_text = (
            "Si acabas de crear un canal o grupo y no aparece en la búsqueda:\n"
            "1. Abre ese chat en tu móvil o escritorio.\n"
            "2. Envía un mensaje cualquiera.\n"
            "3. Vuelve aquí e intenta buscar de nuevo."
        )
        panel = Panel(
            tip_text,
            title="¿No encuentras tu chat?",
            border_style="cyan",
            padding=(1, 1),
        )
        console.print(panel)
        ui_logger.info("[¿No encuentras tu chat?] " + tip_text.replace("\n", " "))

    @staticmethod
    def render_search_results(chats: List[ChatMatch]):
        if not chats:
            return

        table = Table(title="Resultados de búsqueda en Telegram", expand=True)
        table.add_column("#", style="cyan", justify="right")
        table.add_column("Tipo", style="magenta")
        table.add_column("Título / Nombre", style="green")
        table.add_column("Username / ID", style="dim")

        for i, chat in enumerate(chats, 1):
            table.add_row(
                str(i),
                chat.type,
                chat.title,
                f"@{chat.username}" if chat.username else str(chat.id),
            )
        console.print(table)
        log_table(table, "Resultados de búsqueda en Telegram")

    @classmethod
    def show_chat_table(cls, matches: List[ChatMatch], title: str):
        """Helper para mostrar resultados de chats en formato tabla."""
        table = Table(title=title, show_header=True, title_style=COLORS.TABLE_TITLE)
        table.add_column("ID", style="dim")
        table.add_column("Título")
        table.add_column("Username")
        table.add_column("Tipo")

        for m in matches:
            table.add_row(
                str(m.id),
                m.title,
                f"@{m.username}" if m.username else "-",
                str(m.type),
            )
        console.print(table)
        log_table(table, title)

    @staticmethod
    def warn_report_access_not_found(chat_id: int | str):
        UI.warn(f"El {chat_id=} no fue encontrado.")
        UI.info("Utiliza [bold]config set chat_id <ID>[/] para configurarlo.")
        UI.info("Si no sabes el ID, usa [bold]totelegram config search[/].")

    @staticmethod
    def warn_report_access_permissions():
        UI.warn("No tienes permisos de escritura en el chat.")
        UI.warn("Corrige los permisos antes de intentar cualquier subida.")
        UI.warn("Configuración 'chat_id' no actualizada.")


class DisplaySnapshotPlan:
    """Presentador visual desacoplado para el plan de importación de snapshots."""

    @classmethod
    def show_balance(cls, plan: ImportPlan) -> None:
        """Muestra el resumen estadístico del escaneo."""
        actionable_count = len(plan.actionable_items)
        conflicts_count = len(plan.conflicts)
        warnings_count = (
            conflicts_count + len(plan.needs_upgrade_items) + len(plan.corrupted_items)
        )

        console.print()
        UI.print("[bold blue]BALANCE DEL ESCANEO:[/bold blue]", indent=False)
        UI.print(f"• Total procesados:     [bold]{plan.total_scanned}[/] snapshots")

        if plan.synced_count > 0:
            UI.print(
                f"• Ya sincronizados:     [bold green]{plan.synced_count}[/] completos al 100% [dim](sin cambios requeridos)[/]"
            )

        if actionable_count > 0:
            UI.print(
                f"• Acciones pendientes:  [bold cyan]{actionable_count}[/] snapshots por aplicar"
            )
        else:
            UI.print("• Acciones pendientes:  [dim]0 snapshots por aplicar[/dim]")

        if warnings_count > 0:
            UI.print(
                f"• Con advertencias:     [bold yellow]{warnings_count}[/] snapshots "
                f"[dim]({conflicts_count} conflictos, {len(plan.needs_upgrade_items)} desactualizados, {len(plan.corrupted_items)} corruptos)[/]"
            )

    @classmethod
    def show_fast_exit(cls, total: int) -> None:
        """Salida visual inmediata cuando todo está sincronizado al 100%."""
        console.print()
        UI.success(
            f"Todos los snapshots ([bold]{total}[/]) ya se encuentran al 100% sincronizados en la base de datos."
        )
        UI.info("Base de datos al día. No se requirieron cambios.")
        console.print()

    @classmethod
    def show_action_table(cls, plan: ImportPlan) -> None:
        """Renderiza la tabla de acciones que alterarán la base de datos."""
        actionables = plan.actionable_items
        if not actionables and not plan.conflicts:
            return

        table = Table(
            title="\nACCIONES A REALIZAR EN BASE DE DATOS",
            show_header=True,
            header_style="bold cyan",
            title_style="bold blue",
            title_justify="left",
        )
        table.add_column("Acción", style="bold", no_wrap=True)
        table.add_column("Estrategia", justify="center")
        table.add_column("Recurso")
        table.add_column("Tamaño", justify="right")
        table.add_column("Chat Destino", style="dim")
        table.add_column("Operaciones en BD", style="bold magenta")

        action_badges = {
            PlanAction.CREATE_NEW: "[green]NUEVO[/]",
            PlanAction.CREATE_JOB: "[cyan]RAMIFICAR[/]",
            PlanAction.MERGE_REMOTES: "[yellow]PARCIAL[/]",
            PlanAction.CONFLICT: "[bold red]CONFLICTO[/]",
        }

        for item in actionables:
            source = item.manifest.source
            total_parts = item.total_parts
            strategy_str = (
                "single"
                if item.manifest.strategy == Strategy.SINGLE
                else f"pieces {total_parts}"
            )

            table.add_row(
                action_badges.get(item.action, str(item.action.value)),
                strategy_str,
                source.filename,
                format_size(source.size),
                str(item.target_chat_id),
                item.mutations.to_badge(),
            )

        for conf in plan.conflicts:
            source = conf.manifest.source
            total_parts = conf.total_parts
            strategy_str = (
                "single (1)"
                if conf.manifest.strategy == Strategy.SINGLE
                else f"pieces ({total_parts})"
            )

            table.add_row(
                action_badges[PlanAction.CONFLICT],
                strategy_str,
                source.filename,
                format_size(source.size),
                str(conf.target_chat_id),
                "[bold red]BLOQUEADO (Inconsistente)[/]",
            )

        console.print(table)
        log_table(table, "ACCIONES A REALIZAR EN BASE DE DATOS")

        legend = "Leyenda BD: +Source (Origen), +Job (Trabajo), +P (Payloads/Piezas), +Remotes (Mensajes en TG), +Files (Cinta TAR)"
        console.print(f"  [dim]{legend}[/]\n")
        ui_logger.info(f"  {legend}")

    @classmethod
    def show_inconsistencies_tree(cls, plan: ImportPlan) -> None:
        """Muestra el árbol Rich detallando las piezas de recursos parciales o con conflictos."""
        partials = plan.partial_items
        conflicts = plan.conflicts

        if not partials and not conflicts:
            return

        UI.print(
            "[bold blue]DETALLE DE INCONSISTENCIAS Y DISCREPANCIAS:[/bold blue]",
            indent=False,
        )
        ui_logger.info("=== DETALLE DE INCONSISTENCIAS Y DISCREPANCIAS ===")

        # Árbol para recursos incompletos / parciales
        for item in partials:
            strat_label = (
                "pieces-file"
                if item.manifest.strategy == Strategy.CHUNKED
                else "single-file"
            )
            root = Tree(
                f"[cyan]•[/] [bold]{item.manifest.source.filename}[/] "
                f"[dim](Estrategia: {strat_label}, {item.total_parts} partes)[/]"
            )
            ui_logger.info(
                f"• {item.manifest.source.filename} (Estrategia: {strat_label}, {item.total_parts} partes)"
            )

            # Detalle de piezas de volumen / remotos
            for p in item.parts_diff:
                part_label = (
                    f"Parte {p.sequence + 1}/{p.total_parts} (msg: {p.message_id})"
                )
                if p.action == PartAction.ALREADY_EXISTS:
                    msg = f"✓ {part_label} ya registrada en DB (omitida)"
                    root.add(f"[dim]{msg}[/]")
                    ui_logger.info(f"    {msg}")
                elif p.action == PartAction.WILL_LINK:
                    msg = f"+ {part_label} será vinculada a la DB"
                    root.add(f"[green]{msg}[/]")
                    ui_logger.info(f"    {msg}")
                elif p.action == PartAction.CONFLICT:
                    msg = f"✗ {part_label}: {p.detail}"
                    root.add(f"[bold red]{msg}[/]")
                    ui_logger.warning(f"    {msg}")

            # Detalle de archivos internos de la cinta TAR si los hay
            if item.mutations.members_count > 0:
                tar_msg = f"+ {item.mutations.members_count} archivos de la cinta TAR serán indexados en base de datos"
                root.add(f"[bold cyan]{tar_msg}[/]")
                ui_logger.info(f"    {tar_msg}")

            console.print(root)
            console.print()

        # Árbol para conflictos estructurales bloqueantes
        for conf in conflicts:
            short_md5 = (
                conf.manifest.source.md5sum[:12]
                if conf.manifest.source.md5sum
                else "desconocido"
            )
            root = Tree(
                f"[yellow]•[/] [bold]{conf.manifest.source.filename}[/] [dim](MD5: {short_md5}...)[/]"
            )
            root.add(f"[red]Conflicto:[/] {conf.reason}")
            console.print(root)
            console.print()

            ui_logger.warning(
                f"• CONFLICTO: {conf.manifest.source.filename} (MD5: {short_md5}...): {conf.reason}"
            )

    @classmethod
    def show_alerts(cls, plan: ImportPlan, sample_path: str | None = None) -> None:
        """Muestra advertencias para versiones incompatibles y archivos corruptos."""
        if not plan.needs_upgrade_items and not plan.corrupted_items:
            return

        UI.print("[bold red]ATENCIÓN REQUERIDA:[/bold red]", indent=False)
        ui_logger.warning("=== ATENCIÓN REQUERIDA ===")

        if plan.needs_upgrade_items:
            count = len(plan.needs_upgrade_items)
            msg = f"! {count} snapshot(s) en formato antiguo (pre-v6.0) detectados:"
            console.print(
                f"  [yellow]![/] [bold]{count}[/] snapshot(s) en formato antiguo (pre-v6.0) no son compatibles directamente:"
            )
            ui_logger.warning(f"  {msg}")

            for insp in plan.needs_upgrade_items[:5]:
                ver = insp.detected_version or "antigua"
                console.print(
                    f"      [bright_black]• {insp.path.name}[/] [dim](versión detectada: {ver})[/]"
                )
                ui_logger.warning(f"      • {insp.path.name} ({ver})")

            if count > 5:
                console.print(f"      [dim]... y {count - 5} archivos más.[/dim]")
                ui_logger.warning(f"      ... y {count - 5} archivos más.")

            UI.educational_tip(
                message="Esta versión de toTelegram opera exclusivamente con el estándar v6.0 (.ttape y .json.xz).\n"
                "Para migrar snapshots históricos masivos a Telegram, utiliza la herramienta de migración (rama migration).",
                title="Snapshots heredados",
                spacing="top",
                border_style="yellow",
            )
            console.print()

        if plan.corrupted_items:
            count = len(plan.corrupted_items)
            msg = f"X {count} snapshot(s) corruptos o con formato inválido:"
            console.print(
                f"  [bold red]X[/] [bold]{count}[/] snapshot(s) corruptos o con formato inválido:"
            )
            ui_logger.error(f"  {msg}")

            for insp in plan.corrupted_items[:5]:
                err = insp.error_detail or "Error de parsing"
                console.print(f"      [red]• {insp.path.name}:[/] [dim]{err}[/]")
                ui_logger.error(f"      • {insp.path.name}: {err}")

            if count > 5:
                console.print(f"      [dim]... y {count - 5} archivos más.[/dim]")
                ui_logger.error(f"      ... y {count - 5} archivos más.")
            console.print()


class DisplayIndexer:
    @staticmethod
    def show_unindexed_notice(count: int):
        """Muestra un panel informativo sugiriendo ejecutar totelegram index."""
        plural = "cintas archivadas" if count > 1 else "cinta archivada"
        message = (
            f"Detectadas [bold yellow]{count}[/] {plural} en Telegram que aún no están indexadas localmente.\n"
            "Para habilitar la búsqueda quirúrgica de sus archivos internos, ejecuta:"
        )
        UI.educational_tip(
            message=message,
            title="Catálogo desincronizado",
            commands=[
                f"{Commands.CLI_BIN if hasattr(Commands, 'CLI_BIN') else 'totelegram'} index"
            ],
            spacing="block",
            border_style="yellow",
        )
