from typing import Any, Dict, List, Literal, Union

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
from totelegram.services.chat_access import ChatAccessService


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


class ConfigUpdateLogic:
    def __init__(self, settings_name, manager: SettingsManager, is_debug: bool):
        self.manager = manager
        self.is_debug = is_debug
        self.settings_name = settings_name

    def _parse_key_value_pairs(self, args: List[str]) -> dict[str, str]:
        """Convierte una lista de pares ([k1, v1, k2, v2]) a un diccionario: {k1: v1, k2: v2}."""

        # TODO: Hacer más explicito que el valor del par puede ser Any, aunque normalmente se espera que sea un string.
        if not args or len(args) % 2 != 0:
            UI.error(
                "Debes proporcionar pares de CLAVE y VALOR. Ej: 'set chat_id 12345'"
            )
            raise typer.Exit(1)

        # [k1, v1, k2, v2] -> {k1: v1, k2: v2}
        raw_data = {args[i].lower(): args[i + 1] for i in range(0, len(args), 2)}
        return raw_data

    def parse_and_transform(self, args: List[str]) -> Dict[str, Any]:
        """Convierte lista de argumentos en un diccionario validado y tipado."""
        if not args or len(args) % 2 != 0:
            UI.error(
                "Debes proporcionar pares de CLAVE y VALOR. Ej: 'set chat_id 12345'"
            )
            raise typer.Exit(1)

        raw_data = self._parse_key_value_pairs(args)
        updates_to_apply = {}
        errors = []

        for key, raw_value in raw_data.items():
            try:
                Settings.validate_key_access(self.is_debug, key)
                clean_value = Settings.validate_single_setting(key, raw_value)
                updates_to_apply[key] = clean_value
            except ValueError as e:
                errors.append(f"[bold red]{key.upper()}[/]: {str(e)}")

        if errors:
            UI.error("Se encontraron errores de validación:")
            for err in errors:
                console.print(f"  - {err}")
            raise typer.Exit(1)

        return updates_to_apply

    def apply(
        self,
        settings_name: str,
        updates: Dict[str, Any],
        action: Literal["set", "add"] = "set",
    ):
        """Persiste los cambios en el perfil indicado."""
        for field_name, field_value in updates.items():
            try:
                if action == "set":
                    changed, final_val = self.manager.set_setting(
                        settings_name, field_name, field_value
                    )
                else:  # action == "add"
                    changed, final_val = self.manager.add_setting(
                        settings_name, field_name, field_value
                    )

                if changed:
                    settings = self.manager.get_settings(settings_name)
                    display_config_table(self.manager, self.is_debug, settings)
                    if action == "set":
                        UI.success(
                            f"La configuración [bold]{field_name}[/] ha sido actualizada."
                        )
                    else:
                        UI.success(
                            f"La configuración [bold]{field_name}[/] ha sido agregada."
                        )
                else:
                    if action == "set":
                        UI.info(
                            f"La configuración [bold]{field_name}[/] ya estaba configurada."
                        )
                    else:
                        UI.info(
                            f"La configuración [bold]{field_name}[/] ya estaba configurada."
                        )
            except Exception as e:
                UI.error(f"Error persistiendo {field_name}: {e}")


class ConfigResolutionLogic:
    def __init__(self, state: CLIState, chat_access: ChatAccessService):
        self.state = state
        self.manager = state.manager
        self.chat_access = chat_access

    def process_winner(self, match, apply):
        UI.success(f"¡Encontrado! [bold]{match.title}[/] [dim](ID: {match.id})[/]")
        UI.success("Tienes permisos de escritura.")
        if apply:
            assert self.state.settings_name is not None
            self.manager.set_setting(self.state.settings_name, "chat_id", str(match.id))
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
