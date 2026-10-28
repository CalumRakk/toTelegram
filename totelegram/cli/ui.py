import logging
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, List, Literal

from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from totelegram.database import DatabaseSession, db_proxy
from totelegram.models import TelegramChat
from totelegram.schemas import VALUE_NOT_SET

Spacing = Literal["top", "bottom", "block"] | None

custom_theme = Theme(
    {
        "info": "dim cyan",
        "warning": "orange1",
        "error": "bold red",
        "success": "bold green",
        "progress": "italic blue",
    }
)

console = Console(theme=custom_theme, force_terminal=True, legacy_windows=True)
ui_logger = logging.getLogger("totelegram.ui")


def strip_markup(text: Any) -> str:
    """Elimina las etiquetas de color [bold], [red], etc. para el archivo de log."""
    try:
        return Text.from_markup(str(text)).plain
    except Exception:
        return re.sub(r"\[/?[a-zA-Z0-9_# =]+\]", "", str(text))


def log_table(
    table: Table,
    title: str | None = None,
    logger: logging.Logger = ui_logger,
) -> None:
    """Vuelca una tabla de Rich en texto plano estructurado al logger."""
    try:
        t_title = title or (strip_markup(str(table.title)) if table.title else "Tabla")
        logger.info(f"=== {t_title} ===")
        if not table.columns:
            return

        headers = [strip_markup(col.header) for col in table.columns]
        logger.info(" | ".join(headers))

        num_rows = len(table.columns[0]._cells) if table.columns else 0
        for row_idx in range(num_rows):
            row_vals = [strip_markup(str(col._cells[row_idx])) for col in table.columns]
            logger.info("  " + " | ".join(row_vals))
    except Exception as e:
        logger.debug(f"Error al volcar tabla en log: {e}")


def get_friendly_chat_name(
    chat_id: str | int, db_path: Path | str | None = None
) -> str:
    """Devuelve un nombre amigable en formato 'Título (ID)'."""
    val = str(chat_id).strip()

    if val in ("me", "self", VALUE_NOT_SET):
        return (
            "Mensajes Guardados (me)"
            if val in ("me", "self")
            else "[yellow]Pendiente[/]"
        )

    if val.startswith("@"):
        return val

    try:
        chat_int_id = int(val)
        if db_proxy.obj is not None and not db_proxy.is_closed():
            chat = TelegramChat.get_or_none(TelegramChat.id == chat_int_id)
            if chat and chat.title:
                return f"[bold cyan]{chat.title}[/] [dim]({chat.id})[/]"
        elif db_path:
            with DatabaseSession(db_path, auto_init_schema=False):
                chat = TelegramChat.get_or_none(TelegramChat.id == chat_int_id)
                if chat and chat.title:
                    return f"[bold cyan]{chat.title}[/] [dim]({chat.id})[/]"
    except Exception:  # noqa: S110
        pass

    return str(chat_id)


class UI:
    """Primitivas de consola y componentes base del sistema visual."""

    @classmethod
    def separator(cls, style: str = "bright_black"):
        console.print(Columns([Rule(style=style)], width=45))
        ui_logger.info("-" * 45)

    @staticmethod
    def sleep_progress(seconds: int):
        """Muestra una cuenta regresiva visual sin inundar el log."""
        if seconds <= 0:
            return

        UI.info(f"Iniciando pausa ({seconds // 60} min)...")
        with console.status("[bold blue]Pausa activa...", spinner="line") as status:
            for i in range(seconds, 0, -1):
                time.sleep(1)
                if i % 60 == 0 or i <= 10:
                    mins = i // 60
                    secs = i % 60
                    status.update(
                        f"[bold blue]Pausa activa: Siguiente parte en {mins}m {secs}s..."
                    )

        UI.success("Pausa finalizada. Reanudando subida.")

    @staticmethod
    def _print(
        message: str,
        *,
        indent: bool = False,
        spacing: Spacing = None,
        log_level: int = logging.INFO,
        **kwargs,
    ):
        if spacing in ("top", "block"):
            console.print()

        prefix = "  " if indent else ""
        console.print(f"{prefix}{message}", **kwargs)

        clean_msg = strip_markup(message).strip()
        if clean_msg:
            ui_logger.log(log_level, f"{prefix}{clean_msg}")

        if spacing in ("bottom", "block"):
            console.print()

    @staticmethod
    def print(message: str, *, indent: bool = True, spacing: Spacing = None, **kwargs):
        UI._print(
            message, indent=indent, spacing=spacing, log_level=logging.INFO, **kwargs
        )

    @staticmethod
    def info(message: str, *, spacing: Spacing = None, **kwargs):
        UI._print(
            f"[info]i[/] {message}", spacing=spacing, log_level=logging.INFO, **kwargs
        )

    @staticmethod
    def success(message: str, *, spacing: Spacing = None, **kwargs):
        UI._print(
            f"[success]>[/] {message}",
            spacing=spacing,
            log_level=logging.INFO,
            **kwargs,
        )

    @staticmethod
    def warn(message: str, *, spacing: Spacing = None, **kwargs):
        UI._print(
            f"[warning]![/] {message}",
            spacing=spacing,
            log_level=logging.WARNING,
            **kwargs,
        )

    @staticmethod
    def error(message: str, *, spacing: Spacing = None, **kwargs):
        UI._print(
            f"[error]X[/] {message}", spacing=spacing, log_level=logging.ERROR, **kwargs
        )

    @staticmethod
    def tip(
        message: str,
        commands: List[str] | str | None = None,
        spacing: Spacing = None,
        **kwargs,
    ):
        UI._print(f"[dim cyan]  Tip:[/] {message}", spacing=spacing, **kwargs)
        if commands:
            if isinstance(commands, str):
                commands = [commands]
            for command in commands:
                console.print(f"    [bold yellow]> {command}[/]")
                ui_logger.info(f"    > {strip_markup(command)}")

    @staticmethod
    def educational_tip(
        message: str,
        title: str | None = None,
        commands: List[str] | str | None = None,
        spacing: Spacing = None,
        border_style: str = "cyan",
    ):
        if spacing in ("top", "block"):
            console.print()

        content_lines = [message]
        if commands:
            content_lines.append("")
            if isinstance(commands, str):
                commands = [commands]
            for command in commands:
                content_lines.append(f"   [bold yellow]> {command}[/]")

        panel = Panel(
            "\n".join(content_lines),
            title=f"[bold]{title}[/]" if title else None,
            border_style=border_style,
            padding=(1, 1),
        )
        console.print(panel)

        title_prefix = f"[{strip_markup(title)}] " if title else ""
        ui_logger.info(f"{title_prefix}{strip_markup(message)}")
        if commands:
            for command in commands:
                ui_logger.info(f"   > {strip_markup(command)}")

        if spacing in ("bottom", "block"):
            console.print()

    @classmethod
    @contextmanager
    def loading(cls, message: str):
        ui_logger.info(f"[Iniciando] {strip_markup(message)}")
        with console.status(f"[bold green]{message}"):
            yield
