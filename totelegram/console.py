from contextlib import contextmanager

from rich.console import Console
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


class UI:
    @staticmethod
    def info(message: str, **kwargs):
        console.print(f"[info]i[/] {message}", **kwargs)

    @staticmethod
    def success(message: str, **kwargs):
        console.print(f"[success]>[/] {message}", **kwargs)

    @staticmethod
    def warn(message: str, **kwargs):
        console.print(f"[warning]![/] {message}", **kwargs)

    @staticmethod
    def error(message: str, **kwargs):
        console.print(f"[error]X[/] {message}", **kwargs)

    @classmethod
    @contextmanager
    def loading(cls, message: str):
        with console.status(f"[bold green]{message}"):
            yield
