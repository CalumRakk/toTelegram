from typing import Any, Dict, List, Literal

import typer
from rich.table import Table

from totelegram.console import UI, console
from totelegram.core.registry import SettingsManager
from totelegram.core.setting import Settings
from totelegram.services.chat_resolver import ChatMatch
from totelegram.services.validator import ValidationService


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


class ConfigUpdateLogic:
    def __init__(self, manager: SettingsManager, is_debug: bool):
        self.manager = manager
        self.is_debug = is_debug

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

        UI.info("Procesando cambios...")
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
                    UI.info(f"{field_name.upper()} -> '{final_val}'")
                else:
                    UI.info(f"{field_name.upper()} -> No se modificó (mismo valor).")
            except Exception as e:
                UI.error(f"Error persistiendo {field_name}: {e}")


class ConfigResolutionLogic:
    def __init__(self, manager: SettingsManager):
        self.manager = manager
        self.validator = ValidationService()

    def handle_search_result(self, client, result, settings_name, apply):
        if result.is_resolved:
            return self._process_winner(client, result.winner, settings_name, apply)

        if result.is_ambiguous:
            return self._process_ambiguity(result.conflicts)

        if result.needs_help:
            return self._process_suggestions(result.suggestions, result.query)

        UI.error(f"No se encontró ningún chat relacionado con '{result.query}'.")
        raise typer.Exit(1)

    def _process_winner(self, client, match, settings_name, apply):
        UI.success(f"¡Encontrado! [bold]{match.title}[/] [dim](ID: {match.id})[/]")

        with UI.loading("Verificando permisos..."):
            can_write = self.validator.validate_send_action(client, match.id)

        if not can_write:
            UI.warn("No tienes permisos de escritura en el chat.")
            raise typer.Exit(1)

        UI.success("Tienes permisos de escritura.")

        if apply:
            self.manager.set_setting(settings_name, "chat_id", str(match.id))
            UI.info(f"Configuración 'chat_id' actualizada.")

    def _process_ambiguity(self, conflicts):
        UI.warn(f"Ambigüedad: Hay {len(conflicts)} chats con ese nombre.")
        print_chat_table(conflicts, "Conflictos Encontrados")
        UI.info("Usa el ID exacto: [bold]config set chat_id <ID>[/]")
        raise typer.Exit(1)

    def _process_suggestions(self, suggestions, query):
        UI.error(f"No se encontró el chat '{query}'.")
        print_chat_table(suggestions, "Quizás quisiste decir:")
        UI.info(
            "Tip: Si no encuentras lo que buscas, intenta aumentar la profundidad con --depth."
        )
        raise typer.Exit(1)
