from pathlib import Path
from typing import TYPE_CHECKING, List, Literal

import typer
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from totelegram.cli.ui import UI, console, get_friendly_chat_name, log_table
from totelegram.identity import Profile, Settings, SettingsManager
from totelegram.schemas import (
    COLORS,
    VALUE_NOT_SET,
    AccessLevel,
    Commands,
)
from totelegram.telegram.types import ChatMatch

if TYPE_CHECKING:
    from totelegram.telegram.types import TargetResolution


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
        if not isinstance(value, (str, int)):
            raise TypeError("mark_sensitive solo admite valores de tipo str o int.")

        value = str(value)
        if len(value) <= 6:
            return "•" * len(value)
        return value[:3] + "•" * (len(value) - 4) + value[-3:]

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
            for profile in profiles:
                console.print(" - " + profile.name)
            return

        table = Table(title="Perfiles disponibles", title_style=COLORS.TABLE_TITLE)
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
            UI.info("Usa 'totelegram logout <PERFIL>' para limpiar perfiles huérfanos.")

    @staticmethod
    def announce_profile_used(profile_name: str):
        UI.info(f"Perfil Actual: [bold]{profile_name}[/]")


class DisplayTarget:
    @staticmethod
    def show_no_target_warning():
        UI.warn("El perfil activo no tiene un chat destino por defecto.")
        tip_text = (
            "Puedes configurar un destino predeterminado con:\n"
            "  • totelegram target me               (Tus Mensajes Guardados)\n"
            '  • totelegram target "Mi Canal"       (Búsqueda inteligente)\n'
            "  • totelegram target -100xxxxxxxxxx   (ID directo o @username)\n\n"
            "O puedes subir directamente sin destino global usando:\n"
            '  • totelegram send <archivo> --target "Mi Canal"'
        )
        panel = Panel(
            tip_text,
            title="[bold yellow]Destino Pendiente (NOT-SET)[/]",
            border_style="yellow",
            padding=(1, 2),
        )
        console.print(panel)

    @staticmethod
    def show_current_status(
        profile_name: str, resolution: "TargetResolution", raw_chat_id
    ):
        if resolution.is_resolved and resolution.match:
            match = resolution.match
            content = [
                "[bold green]✓ Listo para subir archivos[/bold green]",
                f"• Perfil:  [white]{profile_name}[/]",
                f"• Título:  [bold cyan]{match.title}[/]",
                f"• ID:      [dim]{match.id}[/]",
                f"• Tipo:    [magenta]{match.type}[/]",
            ]
            if match.username:
                content.append(f"• Alias:   @{match.username}")

            panel = Panel(
                "\n".join(content),
                title="[bold green]Destino Actual[/bold green]",
                border_style="green",
                padding=(1, 2),
            )
            console.print(panel)
        else:
            content = [
                "[bold red]! Destino con problemas[/bold red]",
                f"• Perfil:     [white]{profile_name}[/]",
                f"• ID guardado: [dim]{raw_chat_id}[/]",
                f"• Motivo:     [yellow]{resolution.error_message or 'Error de acceso'}[/]",
            ]
            if resolution.hint:
                content.append(f"\n[dim]{resolution.hint}[/dim]")

            panel = Panel(
                "\n".join(content),
                title="[bold red]Destino no disponible[/bold red]",
                border_style="red",
                padding=(1, 2),
            )
            console.print(panel)

    @staticmethod
    def prompt_candidate_selection(candidates: list[ChatMatch]) -> ChatMatch | None:
        table = Table(
            title="Chats coincidentes",
            show_header=True,
            header_style="bold cyan",
            title_style="bold blue",
        )
        table.add_column("#", justify="right", style="bold yellow")
        table.add_column("Título", style="white")
        table.add_column("Tipo", style="magenta")
        table.add_column("ID / Username", style="dim")

        for idx, c in enumerate(candidates, 1):
            identifier = f"@{c.username}" if c.username else str(c.id)
            table.add_row(str(idx), c.title, c.type, identifier)

        console.print(table)

        while True:
            choice = typer.prompt(
                f"\nSelecciona un chat (1-{len(candidates)}) o '0' para cancelar",
                default="1",
            )
            if choice.strip() == "0":
                return None
            try:
                selected_idx = int(choice) - 1
                if 0 <= selected_idx < len(candidates):
                    return candidates[selected_idx]
            except ValueError:
                pass
            console.print("[red]Opción inválida. Intenta nuevamente.[/red]")
