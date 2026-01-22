import os
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table


class ProfileUI:
    def __init__(self, console: Console):
        self.console = console

    def render_profiles_table(
        self, active: Optional[str], profiles: Dict[str, str], quiet: bool = False
    ):
        self.console.print()
        if quiet:
            self.console.print("Perfiles disponibles de toTelegram:")
            for name in profiles.keys():
                self.console.print(" - " + name)
            return

        table = Table(
            title="Perfiles disponibles de toTelegram",
            expand=True,
            title_style="bold magenta",
        )
        table.add_column("Estado", style="cyan", no_wrap=True)
        table.add_column("Nombre", style="magenta")
        table.add_column("Ruta Configuración", style="green")

        for name, path in profiles.items():
            is_active = name == active
            status = "★ ACTIVO" if is_active else ""
            style_name = "bold green" if is_active else "white"
            table.add_row(status, f"[{style_name}]{name}[/{style_name}]", path)
        self.console.print(table)

    def render_preview_table(self, title: str, rows: List[str], style: str = "cyan"):
        table = Table(title=title, expand=True, title_style="bold magenta")
        table.add_column("Valor", style=style)

        MAX_PREVIEW = 5
        for i, v in enumerate(rows):
            if i >= MAX_PREVIEW:
                table.add_row(
                    f"[italic yellow]... y {len(rows) - i} más ...[/italic yellow]"
                )
                break
            table.add_row(v)
        self.console.print(table)

    def render_options_table(
        self,
        title: str,
        schema: List[Dict],
        current_settings: Any,
        chat_info: Optional[str] = None,
    ):
        table = Table(title=title)
        table.add_column("Opción (Key)", style="bold cyan")
        table.add_column("Tipo", style="magenta")
        table.add_column("Valor Actual", style="green")
        table.add_column("Descripción", style="white")

        for item in schema:
            key = item["key"]
            val = (
                getattr(current_settings, key.lower(), "-") if current_settings else "-"
            )

            from totelegram.core.setting import Settings

            if key in Settings.SENSITIVE_FIELDS and val != "-":
                val_str = str(val)
                if len(val_str) <= 6:
                    display_val = "•" * len(val_str)
                else:
                    display_val = val_str[:3] + "•" * (len(val_str) - 4) + val_str[-3:]
            elif key == "CHAT_ID" and chat_info:
                display_val = f"{chat_info} [dim]({val})[/dim]"
            else:
                display_val = str(val)

            style_val = (
                "bold green"
                if display_val != item["default"] and display_val != "-"
                else "dim white"
            )
            table.add_row(
                key,
                escape(item["type"]),
                f"[{style_val}]{display_val}[/{style_val}]",
                item["description"],
            )

        self.console.print(table)

    def print_warning_shell_expansion(self):
        is_windows = os.name == "nt"
        help_text = f"""
Parece que usaste un asterisco al inicio de la exclusion. Las terminales expanden los asteriscos (*) antes de pasar los argumentos.
Encierra el patrón entre comillas:
- **Linux/Mac/PS:** `"*.jpg"`
- **Windows CMD:** `'*.jpg'`
        """
        self.console.print(
            Panel(
                Markdown(help_text),
                title="Expansión de asterisco",
                border_style="yellow",
            )
        )

    def print_tip_exclude_files(self):
        help_text = """
**Guía de Exclusión (Estilo Git)**
- `*.jpg` ignora todos los JPG.
- `node_modules` ignora la carpeta y contenido.
- `**/temp` busca carpetas 'temp' en cualquier profundidad.
        """
        self.console.print(
            Panel(Markdown(help_text), title="Info Exclusiones", border_style="blue")
        )

    def print_options_help_footer(self):
        self.console.print(
            "\nUsa [yellow]totelegram profile set <KEY> <VALUE>[/yellow] para modificar."
        )
        self.console.print(
            "Usa [yellow]totelegram profile add/remove[/yellow] para listas (ej: EXCLUDE_FILES)."
        )

    def announce_profile_used(self, profile_name: str):
        self.console.print(
            f"\n[bold green]Usando perfil:[/bold green] [green]{profile_name}[/green]\n"
        )

    def render_search_results(self, chats: List[Dict]):
        if not chats:
            self.console.print(
                "[yellow]No se encontraron chats que coincidan con la búsqueda.[/yellow]"
            )
            return

        table = Table(title="Resultados de búsqueda en Telegram", expand=True)
        table.add_column("#", style="cyan", justify="right")
        table.add_column("Tipo", style="magenta")
        table.add_column("Título / Nombre", style="green")
        table.add_column("Username / ID", style="dim")

        for i, chat in enumerate(chats, 1):
            table.add_row(
                str(i),
                chat["type"],
                chat["title"],
                f"@{chat['username']}" if chat["username"] else str(chat["id"]),
            )
        self.console.print(table)
