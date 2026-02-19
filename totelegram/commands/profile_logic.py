from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel
from rich.table import Table

from totelegram.console import UI, console
from totelegram.core.registry import SettingsManager
from totelegram.utils import VALUE_NOT_SET


class Profile(BaseModel):
    name: str
    path_env: Path
    path_session: Path

    @property
    def has_env(self):
        return self.path_env.exists()

    @property
    def has_session(self):
        return self.path_session.exists()

    @property
    def is_trinity(self):
        """Un perfil es trinity si tiene ambos archivos y su nombre."""
        return self.has_env and self.has_session


def render_profiles_table(
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
