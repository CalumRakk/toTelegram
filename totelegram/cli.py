import json
from typing import List, Optional, get_origin

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from typer.models import OptionInfo

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

    registry = pm.list_profiles()
    active = registry.active
    profiles = registry.profiles
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
    # TODO: añadir logica para argumentos opcionales sin tener que entrar al modo interactivo.
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

        config = pm.list_profiles()
        if config.active is None:
            pm.set_active(name)
            console.print(f"[green]Perfil '{name}' activado.[/green]")
        elif config.active != name:
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
    if pm.get_profiles_names() == []:
        console.print("[bold red]No hay perfiles configurados.[/bold red]")
        console.print("Usa [bold]totelegram profile create[/bold] para crear uno.")
        return

    try:
        try:
            key = key.upper()
            field_info = Settings.model_fields.get(key.lower())
            val_to_validate = value
            if field_info:
                origin = get_origin(field_info.annotation)
                if origin is list or origin is List:
                    try:
                        val_to_validate = json.loads(value)
                    except json.JSONDecodeError:
                        console.print(
                            "[bold red]Error:[/bold red] Para campos tipo lista usa formato JSON."
                        )
                        console.print('Ejemplo: \'["*.log", "*.tmp"]\'')
                        console.print(
                            "O mejor usa el comando [bold]profile add[/bold]."
                        )
                        return

            converted_val = Settings.validate_single_setting(key, val_to_validate)
            final_storage_value = value
            if isinstance(converted_val, list):
                final_storage_value = json.dumps(converted_val)

            if profile is None or isinstance(profile, OptionInfo):
                profile = pm.get_name_active_profile()
            else:
                profile = profile

            pm.update_setting(key, final_storage_value, name=profile)
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


@profile_app.command("add")
def add_to_list(
    key: str = typer.Argument(..., help="Clave de configuración (tipo lista)"),
    value: str = typer.Argument(..., help="Valor a agregar a la lista"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p"),
):
    """Agrega un elemento a una configuración tipo lista (ej. EXCLUDE_FILES)."""
    try:
        key = key.upper()
        field_info = Settings.model_fields.get(key.lower())
        if not field_info:
            console.print(f"[bold red]La clave {key} no existe.[/bold red]")
            return

        origin = get_origin(field_info.annotation)
        if origin is not list and origin is not List:
            console.print(f"[bold red]La clave {key} no es una lista.[/bold red]")
            console.print("Usa 'totelegram profile set' para valores simples.")
            return

        current_list = _get_current_list_value(key, profile)
        if value in current_list:
            console.print(f"[yellow]El valor '{value}' ya existe en {key}.[/yellow]")
            return

        current_list.append(value)
        try:
            Settings.validate_single_setting(key, current_list)  # type: ignore
        except ValidationError as e:
            console.print(f"[bold red]Error validando la lista resultante:[/bold red]")
            console.print(e)
            return

        json_val = json.dumps(current_list)
        pm.update_setting(key, json_val, name=profile)

        target_profile = profile if profile else "activo"
        console.print(
            f"[bold green]✔[/bold green] Agregado '{value}' a {key} en perfil '{target_profile}'."
        )
        console.print(f"Lista actual: {current_list}")

    except Exception as e:
        console.print(f"[bold red]Error crítico:[/bold red] {e}")


@profile_app.command("remove")
def remove_from_list(
    key: str = typer.Argument(..., help="Clave de configuración (tipo lista)"),
    value: str = typer.Argument(..., help="Valor a remover de la lista"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p"),
):
    """Remueve un elemento de una configuración tipo lista."""
    if pm.get_profiles_names() == []:
        console.print("[bold red]No hay perfiles configurados.[/bold red]")
        console.print("Usa [bold]totelegram profile create[/bold] para crear uno.")
        return

    try:
        key = key.upper()
        field_info = Settings.model_fields.get(key.lower())
        if not field_info:
            return

        current_list = _get_current_list_value(key, profile)
        if value not in current_list:
            console.print(
                f"[yellow]El valor '{value}' no está en la lista {key}.[/yellow]"
            )
            return

        current_list.remove(value)
        Settings.validate_single_setting(key, current_list)  # type: ignore

        json_val = json.dumps(current_list)
        pm.update_setting(key, json_val, name=profile)

        console.print(f"[bold green]✔[/bold green] Removido '{value}' de {key}.")

    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")


def run_script():
    """Punto de entrada para el setup.py"""
    app()


if __name__ == "__main__":
    app()
