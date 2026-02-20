from typing import List, Union

import typer
from rich.markup import escape
from rich.table import Table

from totelegram.commands.profile_utils import (
    get_friendly_chat_name,
)
from totelegram.console import UI, console
from totelegram.core.registry import SettingsManager
from totelegram.core.schemas import ChatMatch, CLIState, IntentType
from totelegram.core.setting import SELF_CHAT_ALIASES, AccessLevel, Settings


def mark_sensitive(value: int | str) -> str:
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


def print_chat_table(matches: List[ChatMatch], title: str):
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


def display_config_table(maneger: SettingsManager, is_debug: bool, settings: Settings):
    table = Table(
        title="Configuración del Perfil", show_header=True, header_style="bold blue"
    )
    table.add_column("Opción (Key)")
    table.add_column("Tipo")
    table.add_column("Valor Actual")
    table.add_column("Descripción")

    default_settings = maneger.get_default_settings()
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
            display_val = mark_sensitive(value)
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


def classify_intent(query: Union[str, int]) -> IntentType:
    if isinstance(query, int):
        return IntentType.DIRECT_ID

    clean = str(query).strip()
    if clean.startswith("@"):
        return IntentType.DIRECT_USERNAME

    if "t.me/" in clean or "telegram.me/" in clean:
        return IntentType.DIRECT_LINK

    if clean.lower() in SELF_CHAT_ALIASES:
        return IntentType.DIRECT_ALIAS

    return IntentType.SEARCH_QUERY


class ConfigResolutionLogic:
    def __init__(self, state: CLIState):
        self.state = state

    def process_winner(self, match, apply):
        UI.success(f"¡Encontrado! [bold]{match.title}[/] [dim](ID: {match.id})[/]")
        UI.success("Tienes permisos de escritura.")
        if apply:
            assert self.state.settings_name is not None
            self.state.manager.set_setting(
                self.state.settings_name, "chat_id", str(match.id)
            )
            UI.success(f"Configuración 'chat_id' actualizada.")

    def process_ambiguity(self, conflicts):
        UI.warn(f"Ambigüedad: Hay {len(conflicts)} chats con ese nombre.")
        print_chat_table(conflicts, "Conflictos Encontrados")
        UI.info("Usa el ID exacto: [bold]config set chat_id <ID>[/]")
        raise typer.Exit(1)

    def process_suggestions(self, suggestions, query):
        print_chat_table(suggestions, "Quizás quisiste decir:")
        UI.info(
            "Tip: Si no encuentras lo que buscas, intenta aumentar la profundidad con --depth."
        )
        raise typer.Exit(1)
