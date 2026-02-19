from typing import List

import typer
from rich.table import Table

from totelegram.console import UI, console
from totelegram.core.registry import SettingsManager
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
