import json
import os
from typing import List, Optional

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

from totelegram.console import console
from totelegram.core.profiles import ProfileManager
from totelegram.core.setting import Settings, get_settings
from totelegram.services.validator import ValidationService

app = typer.Typer(help="Gestión de perfiles de configuración.")
pm = ProfileManager()


def _normalize_input_values(values: List[str]) -> List[str]:
    """
    Convierte inputs sucios (con comas, comillas, espacios) en una lista limpia.
    Ej: ['"a, b"', 'c'] -> ['a', 'b', 'c']
    """
    cleaned = []
    for val in values:
        # Soportar comas internas
        items = val.split(",") if "," in val else [val]
        for item in items:
            # Quitar comillas de shell y espacios
            clean = item.strip().strip("'").strip('"')
            if clean:
                cleaned.append(clean)
    return cleaned


def _check_shell_expansion(values: List[str], console: Console) -> bool:
    """
    Detecta si la shell expandió un asterisco y muestra advertencia.
    Retorna True si parece haber expansión.
    """
    if len(values) > 1 and all("*" not in v for v in values):
        print_warning_exclusion_files(console)
        return True
    return False


def _render_preview_table(title: str, rows: List[str], style: str = "cyan"):
    """Renderiza una tabla simple de previsualización."""
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

    console.print(table)


def _render_profiles_table(active: str, profiles: dict):
    table = Table(title="Perfiles de toTelegram")
    table.add_column("Estado", style="cyan", no_wrap=True)
    table.add_column("Nombre", style="magenta")
    table.add_column("Ruta Configuración", style="green")

    for name, path in profiles.items():
        is_active = name == active
        status = "★ ACTIVO" if is_active else ""
        style_name = "bold green" if is_active else "white"
        table.add_row(status, f"[{style_name}]{name}[/{style_name}]", path)

    console.print(table)


@app.command("list")
def list_profiles():
    registry = pm.list_profiles()
    if not registry.profiles:
        console.print("[yellow]No hay perfiles.[/yellow]")
        return
    assert registry.active is not None
    _render_profiles_table(registry.active, registry.profiles)


@app.command("create")
def create_profile(
    profile_name: str = typer.Argument(..., help="Nombre del perfil (ej. personal)"),
    api_id: int = typer.Option(..., help="API ID", prompt=True),
    api_hash: str = typer.Option(..., help="API Hash", prompt=True),
    chat_id: str = typer.Option(
        ..., help="Chat ID o Usersession_name destino", prompt=True
    ),
):
    """Crea un nuevo perfil de configuración interactivamente."""
    try:
        if profile_name is None:
            profile_name = typer.prompt("Nombre del perfil (ej. personal)", type=str)

        if pm.profile_exists(profile_name):
            console.print(f"[bold red]El perfil '{profile_name}' ya existe.[/bold red]")
            if typer.confirm("¿Deseas sobreescribirlo?"):
                console.print(
                    f"[yellow]Sobrescribiendo el perfil '{profile_name}'...[/yellow]"
                )
            else:
                console.print("Operación cancelada.")
                return

        validator = ValidationService(console)
        is_valid = validator.validate_setup(profile_name, api_id, api_hash, chat_id)
        if not is_valid:
            console.print("\n[bold red]La validación falló.[/bold red]")
            if not typer.confirm("¿Guardar de todos modos?"):
                console.print("Operación cancelada.")
                return

        path = pm.create_profile(
            profile_name=profile_name,
            api_id=api_id,
            api_hash=api_hash,
            chat_id=chat_id,
        )

        console.print(
            f"\n[bold green]✔ Perfil '{profile_name}' guardado exitosamente![/bold green]"
        )
        console.print(f"Ruta: {path}")

        config = pm.list_profiles()
        if config.active is None:
            pm.set_active(profile_name)
            console.print(f"[green]Perfil '{profile_name}' activado.[/green]")
        elif config.active != profile_name:
            if typer.confirm("¿Deseas activar este perfil ahora?"):
                pm.set_active(profile_name)
                console.print(f"[green]Perfil '{profile_name}' activado.[/green]")
        else:
            console.print(f"[green]Perfil '{profile_name}' activo.[/green]")

    except Exception as e:
        console.print(f"[bold red]Error creando perfil:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command("use")
def use_profile(profile_name: str):
    """Cambia el perfil activo."""
    try:
        pm.set_active(profile_name)
        console.print(
            f"[bold green]✔ Ahora usando el perfil: {profile_name}[/bold green]"
        )
    except ValueError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        list_profiles()


@app.command("set")
def set_config(
    key: str = typer.Argument(..., help="Clave a modificar"),
    value: str = typer.Argument(..., help="Nuevo valor"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p"),
):
    """Edita una configuración."""
    profile_name = profile or pm.get_name_active_profile()
    if not profile_name:
        console.print("[bold red]No hay perfil activo ni seleccionado.[/bold red]")
        return

    try:
        pm.smart_update_setting(key, value, profile_name=profile_name)

        console.print(
            f"[bold green]✔[/bold green] {key.upper()} actualizado en '[cyan]{profile_name}[/cyan]'."
        )

    except (ValidationError, ValueError) as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command("options")
def list_options():
    """Lista opciones disponibles y sus valores actuales (si hay perfil activo)."""

    schema = Settings.get_schema_info()

    current_settings = None
    active_profile_name = None
    try:
        path = pm.get_profile_path()
        current_settings = get_settings(path)

        registry = pm.list_profiles()
        active_profile_name = registry.active or "Desconocido"
    except (ValueError, FileNotFoundError):
        pass

    # Configurar Tabla
    title = "Opciones de Configuración"
    if active_profile_name:
        title += f" (Perfil Activo: [green]{active_profile_name}[/green])"

    table = Table(title=title)
    table.add_column("Opción (Key)", style="bold cyan")
    table.add_column("Tipo", style="magenta")
    table.add_column("Valor Actual", style="green")
    table.add_column("Descripción", style="white")

    # Campos que preferimos censurar visualmente
    for item in schema:
        key = item["key"]

        current_val_str = "-"
        if current_settings:
            val = getattr(current_settings, key.lower(), None)
            if key in Settings.SENSITIVE_FIELDS and val:
                val = str(val)
                current_val_str = val[:3] + "•" * (len(str(val)) - 4) + val[-3:]
            else:
                current_val_str = str(val)

        # Resaltar si es diferente del default
        style_current = "green"
        if current_val_str != item["default"] and current_val_str != "-":
            style_current = "bold green"
        elif current_val_str == item["default"]:
            style_current = "dim white"

        table.add_row(
            key,
            escape(item["type"]),
            f"[{style_current}]{current_val_str}[/{style_current}]",
            item["description"],
        )

    console.print(table)

    # Sugerencia establecer valores
    console.print(
        "\nUsa el comando "
        "[yellow]totelegram profile set <KEY> <VALUE>[/yellow] "
        "para modificar una opción."
    )

    # Sugerencia para añadir valores a listas
    console.print(
        "\nUsa el comando "
        "[yellow]totelegram profile add <KEY> <VALUE>[/yellow] "
        "para agregar un elemento a una lista."
    )

    # Sugerencia para remover valores de listas
    console.print(
        "\nUsa el comando "
        "[yellow]totelegram profile remove <KEY> <VALUE>[/yellow] "
        "para remover un elemento de una lista."
    )


def _get_current_list_value(key: str, profile: Optional[str]) -> List:
    """Helper para obtener y parsear el valor actual de una lista desde el .env"""

    raw_values = pm.get_profile_values(profile)
    raw_val = raw_values.get(key)
    if not raw_val:
        return []

    try:
        return json.loads(raw_val)
    except json.JSONDecodeError:
        # Si por alguna razón no es JSON válido (ej: legacy o error manual),
        # intentamos tratarlo como un único valor en una lista o devolvemos vacío
        return [raw_val]


def print_tip_exclude_files(console: Console):
    help_text = """
**Guía de Exclusión (Estilo Git)**

- **Extensiones:** `*.jpg` ignora todos los archivos JPG.
- **Carpetas:** `node_modules` ignora la carpeta y **todo su contenido**.
- **Recursivo:** `**/temp` busca carpetas 'temp' en cualquier profundidad.
    """
    console.print(
        Panel(
            Markdown(help_text),
            title="Información sobre Exclusiones",
            border_style="blue",
            expand=False,
        )
    )


def print_warning_exclusion_files(console: Console):
    # detectar si esta en windows
    is_windows = os.name == "nt"
    help_text = f"""

Parece que usaste un asterisco al inicio de la exclusion, algo como `*.jpg` {'o `"*.jpg"` en Windows' if is_windows else ''}.

Las terminales expanden los asteriscos (*) automáticamente antes de pasar los argumentos al programa.
Para evitar que el patrón se interprete y termine afectando archivos individuales (por ejemplo, agregándolos o eliminándolos por error), encierra el patrón entre comillas:

- **Linux/Mac/PowerShell:** `"*.jpg"`
- **Windows CMD:** `'*.jpg'` (Comillas simples)
    """
    console.print(
        Panel(
            Markdown(help_text),
            title="Expansion de asterisco detectada",
            border_style="yellow",
            expand=False,
        )
    )


@app.command("add")
def add_to_list(
    key: str = typer.Argument(..., help="Clave de la lista (ej: EXCLUDE_FILES)"),
    values: List[str] = typer.Argument(..., help="Valores a agregar"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p"),
    force: bool = typer.Option(False, "--yes", "-y", help="Omitir confirmación"),
):
    """Agrega elementos a una lista de configuración."""

    cleaned_values = _normalize_input_values(values)
    if not cleaned_values:
        console.print("[yellow]No se proporcionaron valores válidos.[/yellow]")
        raise typer.Exit()

    if key.upper() == "EXCLUDE_FILES" and not force:
        print_tip_exclude_files(console)
        _check_shell_expansion(cleaned_values, console)

    if not force:
        _render_preview_table(
            f"Previsualización: Agregar a {key.upper()}", cleaned_values
        )
        if not typer.confirm("\n¿Confirmas agregar estos valores?"):
            console.print("[red]Operación cancelada.[/red]")
            raise typer.Exit(code=1)

    try:
        new_list = pm.modify_list_setting("add", key, cleaned_values, profile)
        console.print(f"[green]✔ Agregados {len(cleaned_values)} elementos.[/green]")
        console.print(f"Lista actual: {new_list}")
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command("remove")
def remove_from_list(
    key: str = typer.Argument(..., help="Clave de la lista"),
    values: List[str] = typer.Argument(..., help="Valores a remover"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p"),
    force: bool = typer.Option(False, "--yes", "-y", help="Omitir confirmación"),
):
    """Remueve elementos de una lista de configuración."""

    cleaned_values = _normalize_input_values(values)
    if not cleaned_values:
        console.print("[yellow]No se proporcionaron valores válidos.[/yellow]")
        raise typer.Exit()

    try:
        current_list = _get_current_list_value(key.upper(), profile)
    except Exception:
        current_list = []

    to_remove = [v for v in cleaned_values if v in current_list]
    not_found = [v for v in cleaned_values if v not in current_list]

    if not force:
        looks_like_expansion = _check_shell_expansion(cleaned_values, console)

        if not_found:
            console.print(Rule(style="white"))
            _render_preview_table(
                "No encontrados (No se eliminarán)", not_found, style="dim white"
            )

        if not to_remove:
            console.print(
                f"\n[yellow]Ninguno de los valores coincide con la configuración actual.[/yellow]"
            )
            console.print(f"Lista actual: {current_list}")
            if looks_like_expansion:
                is_windows = os.name == "nt"
                tip = "'*.log'" if is_windows else '"*.log"'
                console.print(
                    f"\n[bold yellow]Tip:[/bold yellow] Intenta usar comillas: {tip}"
                )
            raise typer.Exit(code=1)

        _render_preview_table(
            f"Encontrados (Se eliminarán) de {key.upper()}", to_remove, style="red"
        )

        if not typer.confirm("\n¿Confirmas eliminar estos valores?"):
            console.print("[red]Operación cancelada.[/red]")
            raise typer.Exit(code=1)

    try:
        if to_remove:
            new_list = pm.modify_list_setting("remove", key, to_remove, profile)
            console.print(f"[green]✔ Removidos {len(to_remove)} elementos.[/green]")
            console.print(f"Lista actual: {new_list}")
        else:
            console.print("[yellow]Nada que eliminar.[/yellow]")

    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)
