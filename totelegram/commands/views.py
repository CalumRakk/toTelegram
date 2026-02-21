from typing import List, Optional

from rich.markup import escape
from rich.table import Table

from totelegram.commands.profile_utils import (
    get_friendly_chat_name,
)
from totelegram.console import UI, console
from totelegram.core.registry import Profile, SettingsManager
from totelegram.core.schemas import ChatMatch
from totelegram.core.setting import AccessLevel, Settings
from totelegram.utils import VALUE_NOT_SET


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
