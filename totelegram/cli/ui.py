from contextlib import contextmanager
from pathlib import Path
from typing import List, Literal, Optional, Union

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.theme import Theme

from totelegram.database import DatabaseSession
from totelegram.identity import Profile, Settings, SettingsManager
from totelegram.models import TelegramChat
from totelegram.schemas import COLORS, AccessLevel, ChatMatch, Commands, ScanReport

Spacing = Optional[Literal["top", "bottom", "block"]]

custom_theme = Theme(
    {
        "info": "dim cyan",
        "warning": "magenta",
        "error": "bold red",
        "success": "bold green",
        "progress": "italic blue",
    }
)

console = Console(theme=custom_theme)


def get_friendly_chat_name(chat_id: str, database_path: str) -> str:
    """
    Aplica las reglas heurísticas para devolver un nombre amigable
    sin necesariamente golpear la red o la DB.
    """
    val = str(chat_id).lower().strip()

    if val.lower() in ["me", "self"]:
        return "Mensajes Guardados"

    # Usernames
    if val.startswith("@"):
        # TODO: analizar si vale la pena consultar el la db el `title`
        return val

    # IDs Numéricos
    if val.replace("-", "").isdigit():
        try:
            with DatabaseSession(database_path):
                chat = TelegramChat.get_or_none(TelegramChat.id == int(val))
                if chat:
                    return f"{chat.title}"
        except Exception:
            return chat_id
    return chat_id


class UI:
    @staticmethod
    def _print(message: str, *, spacing: Spacing = None, **kwargs):
        """Imprime un mensaje con formato y opcionalmente agrega espacio antes o después.

        Args:
            message: El mensaje a imprimir.
            spacing: Si se especifica, agrega una línea en la posición indicada:
                - "top": antes del mensaje
                - "bottom": después del mensaje
                - "block": antes y después del mensaje
            **kwargs: Argumentos adicionales para `console.print()`.
        """
        if spacing in ("top", "block"):
            console.print()

        console.print(message, **kwargs)

        if spacing in ("bottom", "block"):
            console.print()

    @staticmethod
    def info(message: str, *, spacing: Spacing = None, **kwargs):
        UI._print(f"[info]i[/] {message}", spacing=spacing, **kwargs)

    @staticmethod
    def success(message: str, *, spacing: Spacing = None, **kwargs):
        UI._print(f"[success]>[/] {message}", spacing=spacing, **kwargs)

    @staticmethod
    def warn(message: str, *, spacing: Spacing = None, **kwargs):
        UI._print(f"[warning]![/] {message}", spacing=spacing, **kwargs)

    @staticmethod
    def error(message: str, *, spacing: Spacing = None, **kwargs):
        UI._print(f"[error]X[/] {message}", spacing=spacing, **kwargs)

    @staticmethod
    def tip(
        message: str,
        commands: Optional[Union[List[str], str]] = None,
        spacing: Spacing = None,
        **kwarg,
    ):
        """Muestra una sugerencia al usuario. Opcionalmente formatea un comando."""

        UI._print(f"[dim cyan]  Tip:[/] {message}", spacing=spacing, **kwarg)

        if commands:
            # Estandariza cómo se ven los comandos
            if isinstance(commands, str):
                commands = [commands]

            for command in commands:
                console.print(f"    [bold yellow]> {command}[/]")

    @staticmethod
    def educational_tip(
        message: str,
        title: Optional[str] = None,
        commands: Optional[Union[List[str], str]] = None,
        spacing: Spacing = None,
        border_style: str = "cyan",
    ):
        """Muestra una sugerencia educativa destacada en un recuadro (Panel)."""
        if spacing in ("top", "block"):
            console.print()

        content_lines = [message]

        if commands:
            content_lines.append("")  # Salto de línea estético antes de los comandos
            if isinstance(commands, str):
                commands = [commands]

            for command in commands:
                content_lines.append(f"   [bold yellow]> {command}[/]")

        # Unimos todo en un solo bloque de texto interpretado por Rich
        panel_content = "\n".join(content_lines)

        panel = Panel(
            panel_content,
            title=f"[bold]{title}[/]" if title else None,
            border_style=border_style,
            padding=(1, 1),
        )

        console.print(panel)

        if spacing in ("bottom", "block"):
            console.print()

    @classmethod
    @contextmanager
    def loading(cls, message: str):
        with console.status(f"[bold green]{message}"):
            yield


class DisplayUpload:
    @staticmethod
    def announces_total_files_found(scan_report: ScanReport) -> None:
        summary = (
            f"[dim]Se encontraron [bold]{scan_report.total_files}[/bold] archivos "
            f"({scan_report.content_files} contenido, {len(scan_report.skipped_by_snapshot)} snapshots)"
        )
        UI.info(summary)

    @classmethod
    def _print_block(
        cls, label: str, style: str, files: list[Path], current_patterns=None
    ):
        if not files:
            return

        count = len(files)

        # Formateo de patrones (Para quitar el None y la lista cruda)
        pat_str = ""
        if current_patterns:
            clean_list = ", ".join(current_patterns)
            pat_str = f"[dim]({clean_list})[/dim]"

        # Categoría, Cantidad y Patrones (si existen)
        console.print(f"  [bold {style}]{label}:[/] [bold white]{count}[/] {pat_str}")

        # Mostramos hasta 3 ejemplos
        limit = 3
        for f in files[:limit]:
            console.print(f"     [dim]{escape(f.as_posix())}[/dim]", highlight=False)

        # Mostramos el resto. Si el resto es 1, lo mostramos.
        remaining = count - limit
        if remaining > 0:
            if remaining == 1:
                console.print(
                    f"     [dim]{escape(files[-1].name)}[/dim]", highlight=False
                )
            else:
                console.print(f"     [dim]{remaining} archivos mas...[/dim]")

    @classmethod
    def show_skip_report(
        cls,
        scan_report: ScanReport,
        verbose: bool,
    ) -> None:
        """
        Muestra un reporte.
        - Pocos archivos (<5): Lista detallada línea por línea.
        - Muchos archivos: Bloques de resumen con ejemplos en una sola línea.
        """
        by_snapshot = scan_report.skipped_by_snapshot
        by_size = scan_report.skipped_by_size
        by_exclusion = scan_report.skipped_by_exclusion
        patterns = scan_report.exclusion_patterns

        total_skipped = len(by_snapshot) + len(by_size) + len(by_exclusion)

        if total_skipped == 0:
            return

        # FORMATO DETALLADO (Pocos archivos)
        if verbose:
            console.print()
            for idx, p in enumerate(by_snapshot):
                if idx > 5:
                    console.print(f"...")
                    break
                console.print(
                    f"[yellow]Omitido (Ya tiene Snapshot):[/] {escape(p.name)}"
                )
            for idx, p in enumerate(by_exclusion):
                if idx > 5:
                    console.print(f"...")
                    break
                console.print(
                    f"[dim yellow]Omitido (Excluido por Patron):[/] {escape(p.name)}"
                )
            for idx, p in enumerate(by_size):
                if idx > 5:
                    console.print(f"...")
                    break
                console.print(f"[red]Omitido (Excede peso maximo):[/] {escape(p.name)}")

        # FORMATO CONSOLIDADO (Muchos archivos)
        else:
            is_unique = total_skipped == 1
            if is_unique:
                UI.info(f"Se excluyo un archivo por:")
            else:
                UI.info(f"Se excluyeron {total_skipped} archivos por:")
            cls._print_block("Ya tienen Snapshot", "yellow", by_snapshot)
            cls._print_block("Patron de exclusion", "yellow", by_exclusion, patterns)
            cls._print_block("Exceden peso maximo", "red", by_size)


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
            # UI.info(f"Archivo detectado: [bold]{file_example.name}[/]")
        else:
            UI.warn(
                f"No se detectó un patrón, pero se detectaron {len(values)} archivos existentes."
            )
            console.print(
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
        if not isinstance(value, (str, int)):
            raise ValueError(
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
        maneger: SettingsManager,
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

            # Si es CHAT_ID, lo hace amigable.
            if field_name.lower() == "chat_id":
                value = get_friendly_chat_name(value, str(maneger.database_path))

            # Oculta value sencille
            if info.is_sensitive:
                display_val = cls._mark_sensitive(value)
            else:
                display_val = str(value)

            # Agrega a la tabla los campo segun el nivel de acceso.
            if is_debug and info.level == AccessLevel.DEBUG_READONLY:
                table.add_row(
                    f"[grey0]{field_name.lower()}[/]",
                    f"[grey0]{escape(info.type_annotation)}[/]",
                    f"[grey0]{display_val}[/]",
                    f"[grey0]{info.description}[/]",
                )
            elif is_debug:
                table.add_row(
                    field_name.lower(),
                    escape(info.type_annotation),
                    f"[{value_style}]{display_val}[/]",
                    info.description,
                )
            elif info.level == AccessLevel.EDITABLE:
                table.add_row(
                    field_name.lower(),
                    escape(info.type_annotation),
                    f"[{value_style}]{display_val}[/]",
                    info.description,
                )

        console.print(table)
        # console.print("[green]Valor modificado[/] | [dim]Valor por defecto[/]")


class DisplayProfile:
    @staticmethod
    def announce_start_profile_creation(profile_name: str):
        UI.info(f"Creando perfil '[bold]{profile_name}[/]'.\n")
        console.print("[bold cyan]1. Autenticación con Telegram[/bold cyan]")
        console.print(
            "[dim]Se solicitará tu [bold]número telefónico[/] y [bold]código (OTP)[/] para vincular la cuenta.[/dim]\n"
        )

    @staticmethod
    def announce_profile_creation(profile_name: str):
        console.print()
        UI.info(f"Perfil '[bold]{profile_name}[/]' creado exitosamente.")

    @staticmethod
    def announce_start_destination_setup():
        console.print()
        UI.info("Configurando destino de subida (Chat ID).")
        console.print(
            "[dim]Puedes omitir este paso y configurarlo más tarde usando 'totelegram config set chat_id <ID>'.[/dim]\n"
        )

    @classmethod
    def render_profiles_table(
        cls,
        manager: SettingsManager,
        active: Optional[str],
        profiles: List[Profile],
        quiet: bool = False,
    ):
        from totelegram.utils import VALUE_NOT_SET

        console.print()
        if quiet:
            console.print("Perfiles disponibles:")
            for profile in profiles:
                console.print(" - " + profile.name)
            return

        table = Table(
            title="Perfiles disponibles",
            title_style=COLORS.TABLE_TITLE,
        )
        table.add_column("Estado", no_wrap=True)
        table.add_column(
            "Perfil",
        )
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

        if was_orphan:
            console.print()
            UI.warn("Se detecto al menos un perfil huérfano.")
            UI.info(
                f"Usa 'totelegram profile delete <PERFIL>' para limpiar archivos huérfanos"
            )

    @staticmethod
    def show_profile_conflict(profile: Profile):
        if profile.is_trinity:
            UI.warn("El perfil existe.")
        elif profile.has_session:
            UI.warn("Existe una sesión de Telegram huérfana con este nombre.")
        elif profile.has_env:
            UI.warn(f"Existe un archivo de configuración (.env) sin sesión asociada.")
            tip = f"[bold]totelegram --use {profile.name} config check[/]"
            UI.info(f"Ejecuta un diagnóstico usando: {tip} ")

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
        base = f"Explore tus chats más recientes"
        if len(matches) > 0:
            message = (
                f"[green]Ok[/green] {base}, y"
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

    @staticmethod
    def show_search_tip():
        """Muestra el consejo de oro para encontrar chats nuevos."""
        tip_text = (
            "[bold cyan]TIP PARA CHATS NUEVOS:[/bold cyan]\n"
            "Si acabas de crear un canal o grupo y no aparece en la búsqueda:\n"
            "1. Abre ese chat en tu móvil o escritorio.\n"
            "2. Envía un mensaje cualquiera.\n"
            "3. Vuelva aquí e intenta buscar de nuevo."
        )
        console.print(
            Panel(
                tip_text,
                title="¿No encuentras tu chat?",
                border_style="cyan",
                padding=(1, 1),
            )
        )

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

    @classmethod
    def show_chat_table(cls, matches: List[ChatMatch], title: str):
        """Helper para mostrar resultados de chats en formato tabla."""
        table = Table(title=title, show_header=True, title_style=COLORS.TABLE_TITLE)
        table.add_column("ID", style="dim")
        table.add_column("Titulo")
        table.add_column("Username")
        table.add_column("Tipo")

        for m in matches:
            table.add_row(
                str(m.id), m.title, f"@{m.username}" if m.username else "-", str(m.type)
            )
        console.print(table)

    @staticmethod
    def warn_report_access_not_found(chat_id: Union[int, str]):
        UI.warn(f"El {chat_id=} no fue encontrado.")
        UI.info("Utiliza [bold]config set chat_id <ID>[/] para configurarlo.")
        UI.info("Si no sabes el ID, use [bold]totelegram config search[/].")

    @staticmethod
    def warn_report_access_permissions():
        UI.warn("No tienes permisos de escritura en el chat.")
        UI.warn("Corrige los permisos antes de intentar cualquier subida.")
        UI.warn("Configuración 'chat_id' no actualizada.")
