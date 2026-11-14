from pathlib import Path
from typing import Literal

import typer
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from tartape.units import format_size

from totelegram.cli.ui import UI, console, log_table
from totelegram.i18n import _
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

        action_verb = _("agregar") if action == "agregar" else _("eliminar")
        is_unique = len(values) == 1
        file_example = Path(values[0])
        cmd_example = f"{Commands.CONFIG_ADD_LIST if action == 'agregar' else Commands.CONFIG_REMOVE_LIST} {key} '*{file_example.suffix}'"

        if is_unique:
            UI.warn(
                _(
                    "No se detectó un patrón comodin (*), pero el valor coincide con el archivo existente: {name}"
                ).format(name=file_example.name)
            )
        else:
            UI.warn(
                _(
                    "No se detectó un patrón, pero se detectaron {count} archivos existentes."
                ).format(count=len(values))
            )
            UI.print(
                _(
                    "[i]Esto sugiere que tu terminal expandió un comodín antes de que el programa lo recibiera.[/]"
                )
            )

        UI.tip(
            _(
                "Para {action} un patrón que aplique a archivos futuros, usa comillas simples:"
            ).format(action=action_verb),
            commands=[cmd_example],
            spacing="top",
        )

        indicative = (
            _("el archivo '{name}'").format(name=file_example.name)
            if is_unique
            else _("estos {count} archivos").format(count=len(values))
        )

        return typer.confirm(
            _(
                "\n¿Deseas {action} {indicative} literalmente en la configuración?"
            ).format(action=action_verb, indicative=indicative),
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
        table_title = _("Configuración del perfil: {name}").format(name=profile_name)
        UI.info(table_title)
        table = Table(show_header=True, title_style=COLORS.TABLE_TITLE)
        table.add_column(_("Opción (Key)"), no_wrap=True)
        table.add_column(_("Tipo"))
        table.add_column(_("Valor Actual"))
        table.add_column(_("Descripción"))

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
        log_table(table, table_title)


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
            console.print(_("Perfiles disponibles:"))
            for profile in profiles:
                console.print(f" - {profile.name}")
            return

        table_title = _("Perfiles disponibles")
        table = Table(title=table_title, title_style=COLORS.TABLE_TITLE)
        table.add_column(_("Estado"), no_wrap=True)
        table.add_column(_("Perfil"), style="bold white")
        table.add_column(_("Sesión (.session)"), justify="center")
        table.add_column(_("Configuración (.json)"), justify="center")

        was_orphan = False
        for profile in profiles:
            is_active = profile.name == active
            active_marker = "[bold green]*[/]" if is_active else " "

            auth_status = (
                _("[bold green]OK[/]")
                if profile.has_session
                else _("[bold red]FALTA[/]")
            )
            config_status = (
                _("[bold green]OK[/]")
                if profile.has_config
                else _("[bold red]FALTA[/]")
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
        log_table(table, table_title)

        if was_orphan:
            console.print()
            UI.warn(_("Se detectó al menos un perfil huérfano o incompleto."))
            UI.info(_("Usa 'totelegram logout <PERFIL>' para limpiarlo."))

    @staticmethod
    def announce_profile_used(profile_name: str) -> None:
        UI.info(_("Perfil Actual: [bold]{name}[/]").format(name=profile_name))


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
            f"• {_('Título')}:       [bold cyan]{match.title}[/]",
            f"• {_('ID del chat')}:  [dim]{match.id}[/]",
            f"• {_('Tipo de chat')}: [magenta]{match.type}[/]",
        ]
        if match.username:
            content.append(f"• {_('Alias')}:        @{match.username}")

        # Permisos y capacidades
        tier_label = _("Premium (4 GB)") if is_premium else _("Estándar (2 GB)")
        content.append(
            f"• {_('Capacidad')}:    [bold green]{format_size(account_max_size)}[/] [dim]({tier_label})[/dim]"
        )

        delete_perm = (
            _("[bold green]Sí[/] [dim](permite reorganización limpia)[/]")
            if can_delete
            else _("[yellow]No[/] [dim](solo subidas continuas sin limpieza)[/]")
        )
        content.append(f"• {_('Borrar msgs')}:  {delete_perm}")

        panel = Panel(
            "\n".join(content),
            title=_(
                "[bold green]✓ Destino verificado y listo para operar[/bold green]"
            ),
            border_style="green",
            padding=(1, 2),
        )
        console.print(panel)

        # Sugerencias prácticas listas para copiar
        target_param = f'"{match.title}"' if " " in match.title else match.title
        UI.educational_tip(
            title=_("Cómo usar este destino"),
            message=_("Usa la opción [bold]-t / --target[/] en tus operaciones:"),
            commands=[
                f"totelegram send archivo.mp4 -t {target_param}",
                f"totelegram backup ./mi_carpeta -t {target_param}",
                f'export TOTELEGRAM_TARGET="{match.title}"  # '
                + _("Para sesiones continuas"),
            ],
            spacing="top",
            border_style="cyan",
        )

    @staticmethod
    def show_ambiguous_targets(query: str, candidates: list[ChatMatch]) -> None:
        """Muestra los candidatos cuando un nombre coincide con múltiples chats."""
        UI.warn(
            _(
                "La búsqueda '[bold]{query}[/]' arrojó múltiples coincidencias ({count} chats)."
            ).format(query=query, count=len(candidates))
        )
        table_title = _("Coincidencias encontradas en Telegram")
        table = Table(
            title=table_title,
            show_header=True,
            header_style="bold cyan",
            title_style="bold blue",
            expand=True,
        )
        table.add_column(_("ID Directo"), style="bold yellow", no_wrap=True)
        table.add_column(_("Título"), style="white")
        table.add_column(_("Tipo"), style="magenta")
        table.add_column(_("Username / Alias"), style="dim")

        for c in candidates:
            alias = f"@{c.username}" if c.username else "[dim]--[/dim]"
            table.add_row(str(c.id), c.title, c.type, alias)

        console.print(table)
        UI.tip(
            _(
                "Para evitar ambigüedad, usa el ID numérico directo en lugar del nombre:"
            ),
            commands=[f"totelegram send ... -t {candidates[0].id}"],
            spacing="top",
        )

    @staticmethod
    def show_known_targets(chats: list[TelegramChat]) -> None:
        """Renderiza la tabla de destinos conocidos en este equipo."""
        table_title = _("Destinos conocidos en este equipo")
        table = Table(
            title=table_title,
            show_header=True,
            header_style="bold cyan",
            title_style="bold blue",
            expand=True,
        )
        table.add_column(_("Destino"), style="bold white")
        table.add_column(_("Identificador / -t"), style="bold yellow")
        table.add_column(_("Tipo"), style="magenta")
        table.add_column(_("Última verificación"), style="dim")

        # Siempre incluimos Mensajes Guardados en primer lugar
        table.add_row(
            _("Mensajes Guardados (Buzón personal)"),
            "me",
            _("Personal"),
            _("[dim]Siempre disponible[/dim]"),
        )

        for chat in chats:
            # Omitimos el registro especial 0 o chats sin ID real
            if chat.id == 0:
                continue

            is_channel = chat.type == "channel"
            title = chat.title or (
                f"@{chat.username}" if chat.username else f"Chat {chat.id}"
            )

            if is_channel:
                identifier = (
                    f'"{chat.title}"'
                    if (chat.title and " " in chat.title)
                    else str(chat.id)
                )
                type_display = _("[magenta]Canal[/magenta]")
            else:
                identifier = "[dim red]Incompatible[/dim red]"
                type_display = _("[dim red]{type}[/dim red]").format(
                    type=chat.type.capitalize()
                )

            last_v = (
                chat.last_verified.strftime("%Y-%m-%d %H:%M")
                if chat.last_verified
                else _("[dim]Desconocida[/dim]")
            )
            table.add_row(title, identifier, type_display, str(last_v))

        console.print(table)
        UI.tip(
            _(
                "Puedes usar cualquiera de estos nombres o IDs con la opción [bold]-t[/]:"
            ),
            commands=[
                "totelegram send archivo.mp4 -t me",
                'totelegram send archivo.mp4 -t "Nombre del Canal"',
            ],
            spacing="top",
        )
