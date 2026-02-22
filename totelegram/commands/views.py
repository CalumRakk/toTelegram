from typing import List, Optional, Union

from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from totelegram.console import UI, console
from totelegram.core.registry import Profile, SettingsManager
from totelegram.core.schemas import ChatMatch
from totelegram.core.setting import AccessLevel, Settings
from totelegram.store.database import DatabaseSession
from totelegram.store.models import TelegramChat
from totelegram.utils import VALUE_NOT_SET


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


class DisplayConfig:
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
        cls, maneger: SettingsManager, is_debug: bool, settings: Settings
    ):
        table = Table(
            title="Configuración del Perfil", show_header=True, header_style="bold blue"
        )
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


class DisplayProfile:
    @staticmethod
    def announce_profile_creation(profile_name: str):
        UI.info(f"Creando perfil '[bold]{profile_name}[/]'.")
        UI.info("[bold cyan]1. Autenticación con Telegram[/bold cyan]")
        UI.info(
            "[dim]Se solicitará tu número telefónico y código (OTP) para vincular la cuenta.[/dim]\n"
        )
    @classmethod
    def render_profiles_table(
        cls,
        manager: SettingsManager,
        active: Optional[str],
        profiles: List[Profile],
        quiet: bool = False,
    ):
        console.print()
        if quiet:
            console.print("Perfiles disponibles de toTelegram:")
            for profile in profiles:
                console.print(" - " + profile.name)
            return

        table = Table(
            title="Perfiles disponibles de toTelegram",
            title_style="bold magenta",
        )
        table.add_column("Estado", style="cyan", no_wrap=True)
        table.add_column("Perfil", style="magenta")
        table.add_column("Session (.session)", style="green")
        table.add_column("Config (.env)", style="green")
        table.add_column("Destino (Chat ID)", style="green")

        was_orphan = False
        for profile in profiles:
            is_active = profile.name == active

            active_marker = "[bold green]*[/]" if is_active else ""
            auth_status = (
                "[green][ OK ][/]" if profile.has_session else "[red][ MISSING ][/]"
            )

            if profile.has_env:
                settings = manager.get_settings(profile.name)
                chat_id = settings.chat_id

                config_status = "[green][ OK ][/]"
                target_desc = (
                    f"[white]{chat_id}[/]"
                    if chat_id != VALUE_NOT_SET
                    else "[yellow]Pendiente[/]"
                )
            else:
                config_status = "[red][ MISSING ][/]"
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
        UI.info(f"Perfil activo: [bold]{profile_name}[/]")

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
        table = Table(title=title, show_header=True, header_style="bold magenta")
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
