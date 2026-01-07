from typing import Optional, cast

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from build.lib.totelegram.setting import get_settings
from totelegram.core.profiles import ProfileManager
from totelegram.core.setting import Settings
from totelegram.services.validator import ValidationService

app = typer.Typer(
    help="Herramienta para subir archivos a Telegram sin límite de tamaño.",
    add_completion=False,
)
profile_app = typer.Typer(help="Gestión de perfiles de configuración.")
app.add_typer(profile_app, name="profile")

console = Console()
pm = ProfileManager()


@profile_app.command("list")
def list_profiles():
    """Lista todos los perfiles disponibles y marca el activo."""
    data = pm.list_profiles()
    active = data.get("active")
    profiles = cast(dict, data.get("profiles", {}))

    if not profiles:
        console.print("[yellow]No hay perfiles configurados.[/yellow]")
        console.print("Usa [bold]totelegram profile create[/bold] para crear uno.")
        return

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


@profile_app.command("create")
def create_profile(
    name=typer.Argument(None, help="Nombre del perfil (ej. personal)"),
):
    """Crea un nuevo perfil de configuración interactivamente."""
    try:
        if name is None:
            name = typer.prompt("Nombre del perfil (ej. personal)", type=str)

        if pm.profile_exists(name):
            console.print(f"[bold red]El perfil '{name}' ya existe.[/bold red]")
            if typer.confirm("¿Deseas sobreescribirlo?"):
                console.print(f"[yellow]Sobrescribiendo el perfil '{name}'...[/yellow]")
            else:
                console.print("Operación cancelada.")
                return

        api_id = typer.prompt("API ID", type=int)
        api_hash = typer.prompt("API Hash", type=str)
        chat_id = typer.prompt("Chat ID o Username destino", type=str)

        validator = ValidationService(console)
        is_valid = validator.validate_setup(name, api_id, api_hash, chat_id)
        if not is_valid:
            console.print("\n[bold red]La validación falló.[/bold red]")
            if not typer.confirm("¿Guardar de todos modos?"):
                return
            console.print(
                "[yellow]Guardando configuración inválida bajo riesgo del usuario...[/yellow]"
            )

        path = pm.create_profile(
            name=name,
            api_id=api_id,
            api_hash=api_hash,
            chat_id=chat_id,
        )

        console.print(
            f"\n[bold green]✔ Perfil '{name}' guardado exitosamente![/bold green]"
        )
        console.print(f"Ruta: {path}")

        config = pm._load_config()
        if config["active"] is None:
            pm.set_active(name)
            console.print(f"[green]Perfil '{name}' activado.[/green]")
        elif config["active"] != name:
            if typer.confirm("¿Deseas activar este perfil ahora?"):
                pm.set_active(name)
                console.print(f"[green]Perfil '{name}' activado.[/green]")
        else:
            console.print(f"[green]Perfil '{name}' activo.[/green]")

    except Exception as e:
        console.print(f"[bold red]Error creando perfil:[/bold red] {e}")


@profile_app.command("use")
def use_profile(name: str):
    """Cambia el perfil activo."""
    try:
        pm.set_active(name)
        console.print(f"[bold green]✔ Ahora usando el perfil: {name}[/bold green]")
    except ValueError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        list_profiles()


# @app.command("upload")
# def upload_file(
#     target: Path = typer.Argument(
#         ..., exists=True, help="Archivo o directorio a subir"
#     ),
#     profile: Optional[str] = typer.Option(
#         None, "--profile", "-p", help="Usar un perfil específico temporalmente"
#     ),
#     verbose: bool = typer.Option(
#         False, "--verbose", "-v", help="Mostrar logs detallados"
#     ),
# ):
#     """Sube archivos a Telegram usando la configuración activa."""

#     # 1. Configurar Logging
#     log_level = logging.DEBUG if verbose else logging.INFO
#     # Usamos una ruta temporal o la del sistema para logs inmediatos
#     setup_logging("totelegram_cli.log", log_level)
#     logger = logging.getLogger(__name__)

#     try:
#         # 2. Obtener el path del .env
#         if profile:
#             # Si el usuario fuerza un perfil con -p
#             env_path = pm.get_profile_path(profile)
#             console.print(f"[blue]Usando perfil forzado: {profile}[/blue]")
#         else:
#             # Usar el activo por defecto
#             try:
#                 env_path = pm.get_profile_path()
#             except ValueError:
#                 console.print("[bold red]No hay perfil activo.[/bold red]")
#                 console.print("Ejecuta 'totelegram profile create' primero.")
#                 raise typer.Exit(code=1)

#         # 3. Cargar settings
#         settings = get_settings(env_path)
#         console.print(
#             f"Iniciando subida usando configuración de: [bold]{settings.chat_id}[/bold]"
#         )

#         # 4. Ejecutar orquestador
#         # Convertimos el generador a lista para consumir el proceso
#         snapshots = list(orchestrator_upload(target, settings))

#         if snapshots:
#             console.print(
#                 f"[bold green]✔ Proceso finalizado. {len(snapshots)} archivos procesados.[/bold green]"
#             )
#         else:
#             console.print(
#                 "[yellow]No se generaron snapshots (¿archivos excluidos o vacíos?).[/yellow]"
#             )

#     except Exception as e:
#         console.print(f"[bold red]Fallo crítico:[/bold red] {e}")
#         if verbose:
#             logger.exception("Traceback completo:")
#         raise typer.Exit(code=1)


@profile_app.command("set")
def set_config(
    key: str = typer.Argument(..., help="Clave a modificar (ej. MAX_FILESIZE_BYTES)"),
    value: str = typer.Argument(..., help="Nuevo valor"),
    profile: Optional[str] = typer.Option(
        None, "--profile", "-p", help="Perfil destino (opcional)"
    ),
):
    """Edita una configuración validando el tipo de dato."""
    try:
        key = key.upper()

        try:
            # Esto verificará si es un int válido, bool, etc.
            # Convertimos el input a string porque los args de CLI siempre son string
            # pero validate_single_setting hará el casting interno.
            converted_val = Settings.validate_single_setting(key, value)

            val_type = type(converted_val).__name__
            console.print(
                f"[dim]Valor interpretado como {val_type}: {converted_val}[/dim]"
            )

        except ValidationError as e:
            console.print(f"[bold red]Valor inválido para {key}:[/bold red]")
            for err in e.errors():
                msg = err.get("msg")
                console.print(f" - {msg}")
            return
        except ValueError as e:
            console.print(f"[bold red]Error:[/bold red] {e}")
            console.print(
                "Ejecuta 'totelegram profile options' para ver las claves válidas."
            )
            return

        pm.update_setting(key, value, name=profile)

        target_profile = profile if profile else "activo"
        console.print(
            f"[bold green]✔[/bold green] {key} actualizado exitosamente en perfil '{target_profile}'."
        )

    except Exception as e:
        console.print(f"[bold red]Error crítico:[/bold red] {e}")


@profile_app.command("options")
def list_options():
    """Lista opciones disponibles y sus valores actuales (si hay perfil activo)."""

    schema = Settings.get_schema_info()

    current_settings = None
    active_profile_name = None
    try:
        path = pm.get_profile_path()
        current_settings = get_settings(path)
        config_data = pm._load_config()
        active_profile_name = config_data.get("active", "Desconocido")
    except (ValueError, FileNotFoundError):
        # Si no hay perfil activo, no pasa nada, current_settings se queda en None
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
    SENSITIVE_KEYS = ["API_HASH"]
    for item in schema:
        key = item["key"]

        current_val_str = "-"
        if current_settings:
            val = getattr(current_settings, key.lower(), None)
            if key in SENSITIVE_KEYS and val:
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
    console.print(
        "\n[yellow]Usa [bold]totelegram profile set CLAVE valor[/bold] para modificar.[/yellow]"
    )


def run_script():
    """Punto de entrada para el setup.py"""
    app()


if __name__ == "__main__":
    app()
