from pathlib import Path
from typing import Literal

import typer
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from tartape.units import format_size

from totelegram.cli.ui import UI, console, log_table
from totelegram.identity import Profile, Settings, SettingsManager
from totelegram.models import TelegramChat
from totelegram.schemas import (
    COLORS,
    AccessLevel,
    Commands,
)
from totelegram.telegram.types import ChatMatch, TargetResolution


class DisplayConfig:
    @staticmethod
    def confirm_expanded_pattern(
        action: Literal["agregar", "eliminar"], key: str, values: list[str]
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

            if info.is_sensitive and value is not None:
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
    """Componentes visuales para la gestión y listado de perfiles."""

    @classmethod
    def render_profiles_table(
        cls,
        manager: SettingsManager,
        active: str | None,
        profiles: list[Profile],
        quiet: bool = False,
    ) -> None:
        console.print()
        if quiet:
            console.print("Perfiles disponibles:")
            for profile in profiles:
                console.print(f" - {profile.name}")
            return

        table = Table(title="Perfiles disponibles", title_style=COLORS.TABLE_TITLE)
        table.add_column("Estado", no_wrap=True)
        table.add_column("Perfil", style="bold white")
        table.add_column("Sesión (.session)", justify="center")
        table.add_column("Configuración (.json)", justify="center")

        was_orphan = False
        for profile in profiles:
            is_active = profile.name == active
            active_marker = "[bold green]*[/]" if is_active else " "

            auth_status = (
                "[bold green]OK[/]" if profile.has_session else "[bold red]FALTA[/]"
            )
            config_status = (
                "[bold green]OK[/]" if profile.has_config else "[bold red]FALTA[/]"
            )

            if not profile.is_trinity:
                was_orphan = True

            table.add_row(
                active_marker,
                profile.name,
                auth_status,
                config_status,
            )

        console.print(table)
        log_table(table, "Perfiles disponibles")

        if was_orphan:
            console.print()
            UI.warn("Se detectó al menos un perfil huérfano o incompleto.")
            UI.info("Usa 'totelegram logout <PERFIL>' para limpiarlo.")

    @staticmethod
    def announce_profile_used(profile_name: str) -> None:
        UI.info(f"Perfil Actual: [bold]{profile_name}[/]")


class DisplayTarget:
    """Componentes visuales para la inspección y diagnóstico de destinos."""

    @staticmethod
    def show_diagnostic(
        resolution: TargetResolution,
        can_delete: bool,
        account_max_size: int,
        is_premium: bool,
    ) -> None:
        """Muestra el reporte detallado de un destino validado con éxito."""
        match = resolution.match
        assert match is not None

        content = [
            f"• Título:       [bold cyan]{match.title}[/]",
            f"• ID del chat:  [dim]{match.id}[/]",
            f"• Tipo de chat: [magenta]{match.type}[/]",
        ]
        if match.username:
            content.append(f"• Alias:        @{match.username}")

        # Permisos y capacidades
        tier_label = "Premium (4 GB)" if is_premium else "Estándar (2 GB)"
        content.append(
            f"• Capacidad:    [bold green]{format_size(account_max_size)}[/] [dim]({tier_label})[/dim]"
        )

        delete_perm = (
            "[bold green]Sí[/] [dim](permite reorganización limpia)[/]"
            if can_delete
            else "[yellow]No[/] [dim](solo subidas continuas sin limpieza)[/]"
        )
        content.append(f"• Borrar msgs:  {delete_perm}")

        panel = Panel(
            "\n".join(content),
            title="[bold green]✓ Destino verificado y listo para operar[/bold green]",
            border_style="green",
            padding=(1, 2),
        )
        console.print(panel)

        # Sugerencias prácticas listas para copiar
        target_param = f'"{match.title}"' if " " in match.title else match.title
        UI.educational_tip(
            title="Cómo usar este destino",
            message="Usa la opción [bold]-t / --target[/] en tus operaciones:",
            commands=[
                f"totelegram send archivo.mp4 -t {target_param}",
                f"totelegram backup ./mi_carpeta -t {target_param}",
                f'export TOTELEGRAM_TARGET="{match.title}"  # Para sesiones continuas',
            ],
            spacing="top",
            border_style="cyan",
        )

    @staticmethod
    def show_ambiguous_targets(query: str, candidates: list[ChatMatch]) -> None:
        """Muestra los candidatos cuando un nombre coincide con múltiples chats."""
        UI.warn(
            f"La búsqueda '[bold]{query}[/]' arrojó múltiples coincidencias ({len(candidates)} chats)."
        )
        table = Table(
            title="Coincidencias encontradas en Telegram",
            show_header=True,
            header_style="bold cyan",
            title_style="bold blue",
            expand=True,
        )
        table.add_column("ID Directo", style="bold yellow", no_wrap=True)
        table.add_column("Título", style="white")
        table.add_column("Tipo", style="magenta")
        table.add_column("Username / Alias", style="dim")

        for c in candidates:
            alias = f"@{c.username}" if c.username else "[dim]--[/dim]"
            table.add_row(str(c.id), c.title, c.type, alias)

        console.print(table)
        UI.tip(
            "Para evitar ambigüedad, usa el ID numérico directo en lugar del nombre:",
            commands=[f"totelegram send ... -t {candidates[0].id}"],
            spacing="top",
        )

    @staticmethod
    def show_known_targets(chats: list[TelegramChat]) -> None:
        """Renderiza la tabla de destinos conocidos en la base de datos local."""
        table = Table(
            title="Destinos conocidos en este equipo",
            show_header=True,
            header_style="bold cyan",
            title_style="bold blue",
            expand=True,
        )
        table.add_column("Destino", style="bold white")
        table.add_column("Identificador / -t", style="bold yellow")
        table.add_column("Tipo", style="magenta")
        table.add_column("Última verificación", style="dim")

        # Siempre incluimos Mensajes Guardados en primer lugar
        table.add_row(
            "Mensajes Guardados (Buzón personal)",
            "me",
            "Personal",
            "[dim]Siempre disponible[/dim]",
        )

        for chat in chats:
            # Omitimos el registro especial 0 o chats sin ID real
            if chat.id == 0:
                continue

            title = chat.title or (
                f"@{chat.username}" if chat.username else f"Chat {chat.id}"
            )
            identifier = (
                f'"{chat.title}"'
                if (chat.title and " " in chat.title)
                else str(chat.id)
            )

            last_v = (
                chat.last_verified.strftime("%Y-%m-%d %H:%M")
                if chat.last_verified
                else "[dim]Desconocida[/dim]"
            )
            table.add_row(title, identifier, chat.type, str(last_v))

        console.print(table)
        UI.tip(
            "Puedes usar cualquiera de estos nombres o IDs con la opción [bold]-t[/]:",
            commands=[
                "totelegram send archivo.mp4 -t me",
                'totelegram send archivo.mp4 -t "Nombre del Chat"',
            ],
            spacing="top",
        )
