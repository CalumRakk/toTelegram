from contextlib import contextmanager
from typing import List, Literal, Optional, Union

from rich.console import Console
from rich.panel import Panel
from rich.theme import Theme

custom_theme = Theme(
    {
        "info": "dim cyan",
        "warning": "magenta",
        "error": "bold red",
        "success": "bold green",
        "progress": "italic blue",
    }
)

console = Console(theme=custom_theme)

Spacing = Optional[Literal["top", "bottom", "block"]]


class UI:
    @staticmethod
    def _print(message: str, *, spacing: Spacing = None, **kwargs):
        """Imprime un mensaje con formato y opcionalmente agrega espacio antes o después.

        Args:
            message: El mensaje a imprimir.
            spacing: Si se especifica, agrega una línea en la posición indicada:
                - "top": antes del mensaje
                - "bottom": después del mensaje
                - "block": antes y después del mensaje
            **kwargs: Argumentos adicionales para `console.print()`.
        """
        if spacing in ("top", "block"):
            console.print()

        console.print(message, **kwargs)

        if spacing in ("bottom", "block"):
            console.print()

    @staticmethod
    def info(message: str, *, spacing: Spacing = None, **kwargs):
        UI._print(f"[info]i[/] {message}", spacing=spacing, **kwargs)

    @staticmethod
    def success(message: str, *, spacing: Spacing = None, **kwargs):
        UI._print(f"[success]>[/] {message}", spacing=spacing, **kwargs)

    @staticmethod
    def warn(message: str, *, spacing: Spacing = None, **kwargs):
        UI._print(f"[warning]![/] {message}", spacing=spacing, **kwargs)

    @staticmethod
    def error(message: str, *, spacing: Spacing = None, **kwargs):
        UI._print(f"[error]X[/] {message}", spacing=spacing, **kwargs)

    @staticmethod
    def tip(
        message: str,
        commands: Optional[Union[List[str], str]] = None,
        spacing: Spacing = None,
        **kwarg,
    ):
        """Muestra una sugerencia al usuario. Opcionalmente formatea un comando."""

        UI._print(f"[dim cyan] Tip:[/] {message}", spacing=spacing, **kwarg)

        if commands:
            # Estandariza cómo se ven los comandos
            if isinstance(commands, str):
                commands = [commands]

            for command in commands:
                console.print(f"   [bold yellow]> {command}[/]")

    @staticmethod
    def educational_tip(
        message: str,
        title: Optional[str] = None,
        commands: Optional[Union[List[str], str]] = None,
        spacing: Spacing = None,
        border_style: str = "cyan",
    ):
        """Muestra una sugerencia educativa destacada en un recuadro (Panel)."""
        if spacing in ("top", "block"):
            console.print()

        content_lines = [message]

        if commands:
            content_lines.append("")  # Salto de línea estético antes de los comandos
            if isinstance(commands, str):
                commands = [commands]

            for command in commands:
                content_lines.append(f"   [bold yellow]> {command}[/]")

        # Unimos todo en un solo bloque de texto interpretado por Rich
        panel_content = "\n".join(content_lines)

        panel = Panel(
            panel_content,
            title=f"[bold]{title}[/]" if title else None,
            border_style=border_style,
            padding=(1, 1),
        )

        console.print(panel)

        if spacing in ("bottom", "block"):
            console.print()

    @classmethod
    @contextmanager
    def loading(cls, message: str):
        with console.status(f"[bold green]{message}"):
            yield
