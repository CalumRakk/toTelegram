import os
import uuid
from pathlib import Path
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


def normalize_string(value: str):
    if not isinstance(value, str):
        return value
    return value.strip()


def validate_profile_name(profile_name: str):
    cleaned = normalize_string(profile_name)
    # TODO: un nombre como `mi-perfil` no es válido en isidentifier ¿es un error?

    if not cleaned.isidentifier():
        raise typer.BadParameter(
            "El nombre del perfil solo puede contener letras, números y guiones bajos, y no puede comenzar con un número."
        )
    profile_name = cleaned

    if not pm.exists_profile(cleaned):
        return profile_name

    console.print(
        Panel(
            f"[bold red]El perfil '{profile_name}' ya existe.[/bold red]\n\n"
            "Por seguridad y consistencia de datos (ADR-001), los perfiles son inmutables.\n"
            f"Si deseas reutilizar este nombre, primero debes eliminar el perfil existente:\n\n"
            f"   [yellow]totelegram profile remove {profile_name}[/yellow]",
            title="Operación no permitida",
            border_style="red",
        )
    )
    raise typer.Exit(code=1)


@app.command("create")
def create_profile(
    profile_name: str = typer.Option(
        ...,
        help="Nombre del perfil (ej. personal)",
        prompt=True,
        callback=validate_profile_name,
    ),
    api_id: int = typer.Option(..., help="API ID", prompt=True),
    api_hash: str = typer.Option(
        ..., help="API Hash", prompt=True, callback=normalize_string
    ),
    chat_id: str = typer.Option(
        ...,
        help="Chat ID o Username destino",
        prompt=True,
        callback=normalize_string,
    ),
):
    """Crea un nuevo perfil de configuración interactivamente."""

    final_session_file = ProfileManager.PROFILES_DIR / f"{profile_name}.session"
    temp_session_name = f"temp_{uuid.uuid4().hex[:8]}"
    temp_session_file = ProfileManager.PROFILES_DIR / f"{temp_session_name}.session"

    should_save = _run_interactive_validation(
        temp_session_name=temp_session_name,
        api_id=api_id,
        api_hash=api_hash,
        chat_id=chat_id,
    )

    if not should_save:
        raise typer.Exit(code=0)

    try:
        _commit_profile_creation(
            profile_name=profile_name,
            temp_session_file=temp_session_file,
            final_session_file=final_session_file,
            api_id=api_id,
            api_hash=api_hash,
            chat_id=chat_id,
        )
    except Exception as e:
        console.print(f"[bold red]Error fatal guardando el perfil: {e}[/bold red]")

        if final_session_file.exists():
            final_session_file.unlink()
        if temp_session_file.exists():
            temp_session_file.unlink()

        possible_env = ProfileManager.PROFILES_DIR / f"{profile_name}.env"
        if possible_env.exists():
            possible_env.unlink()

        raise e

    _suggest_profile_activation(profile_name)


def _run_interactive_validation(
    temp_session_name: str, api_id: int, api_hash: str, chat_id: str
) -> bool:
    """
    Maneja la lógica de validación con Pyrogram y la interacción con el usuario.
    Retorna True si el usuario quiere/puede guardar, False si cancela o falla login crítico.
    """
    validator = ValidationService(console)
    temp_session_file = ProfileManager.PROFILES_DIR / f"{temp_session_name}.session"

    try:
        with validator.validate_session(temp_session_name, api_id, api_hash) as client:
            is_chat_valid = validator.validate_chat_id(client, chat_id)

            if is_chat_valid:
                return True

            console.print(
                Panel(
                    f"[yellow]⚠ Atención:[/yellow] La sesión de Telegram se inició correctamente, "
                    f"pero hubo un problema con el CHAT_ID '{chat_id}'.\n\n"
                    "Puedes guardar el perfil ahora para no perder el inicio de sesión "
                    "y corregir el chat más tarde usando:\n"
                    "[cyan]totelegram profile set CHAT_ID <nuevo_valor>[/cyan]",
                    title="Chat no accesible",
                    border_style="yellow",
                )
            )

            if typer.confirm("¿Deseas guardar el perfil de todos modos?", default=True):
                console.print("[dim]Continuando con el guardado...[/dim]")
                return True

            console.print("[red]Operación cancelada por el usuario.[/red]")
            return False

    except Exception as e:
        console.print(
            f"[bold red]Error durante la validación de credenciales: {e}[/bold red]"
        )
        if temp_session_file.exists():
            temp_session_file.unlink()
        return False


def _commit_profile_creation(
    profile_name: str,
    temp_session_file: Path,
    final_session_file: Path,
    api_id: int,
    api_hash: str,
    chat_id: str,
):
    """
    Realiza las operaciones de escritura en disco.
    Asume que la validación ya pasó. Lanza excepciones si algo falla.
    """
    if not temp_session_file.exists():
        raise FileNotFoundError("Se perdió el archivo de sesión temporal.")

    if final_session_file.exists():
        raise FileExistsError(
            f"El perfil '{profile_name}' ya existe (colisión detectada)."
        )

    temp_session_file.rename(final_session_file)

    try:
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
    except Exception:
        raise


def _suggest_profile_activation(profile_name: str):
    """Lógica de UI para activar el perfil recién creado."""
    try:
        config = pm.list_profiles()

        if config.active is None:
            pm.set_active(profile_name)
            console.print(
                f"[green]Perfil '{profile_name}' activado automáticamente.[/green]"
            )

        elif config.active != profile_name:
            if typer.confirm("¿Deseas activar este perfil ahora?"):
                pm.set_active(profile_name)
                console.print(f"[green]Perfil '{profile_name}' activado.[/green]")

    except Exception as e:
        console.print(
            f"[yellow]No se pudo activar el perfil automáticamente: {e}[/yellow]"
        )


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
        console.print(
            f"\n[yellow]Ningún perfil activo. Mostrando valores por defecto.[/yellow]"
        )
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
        current_list = pm._parse_string_to_list(key.upper(), profile)
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


if __name__ == "__main__":
    create_profile("leo", 123456, "dfggdfdfhghfg", "your_chat_id")
