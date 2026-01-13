from typing import List, Literal, Optional

import typer
from rich.panel import Panel

from totelegram.commands.profile_ui import ProfileUI
from totelegram.console import console
from totelegram.core.registry import ProfileManager

pm = ProfileManager()
ui = ProfileUI(console)

UseOption = typer.Option(
    None,
    "--use",
    "-u",
    help="Perfil a utilizar para esta operación (ignora el perfil activo)",
)


def _handle_list_operation(
    action: Literal["add", "remove"],
    key: str,
    values: List[str],
    profile: Optional[str],
    force: bool,
):
    """Controlador único para operaciones de lista (DRY)."""
    # 1. Parsing (Esto se podría mover a utils.py después)
    cleaned = _normalize_input_values(values)
    if not cleaned:
        console.print("[yellow]No se proporcionaron valores válidos.[/yellow]")
        return

    # 2. Lógica específica de UI para EXCLUDE_FILES
    if key.upper() == "EXCLUDE_FILES" and not force:
        ui.print_tip_exclude_files()
        if len(cleaned) > 1 and all("*" not in v for v in cleaned):
            ui.print_warning_shell_expansion()

    # 3. Confirmación
    if not force:
        title = "Agregar a" if action == "add" else "Remover de"
        ui.render_preview_table(
            f"{title} {key.upper()}",
            cleaned,
            style="green" if action == "add" else "red",
        )
        if not typer.confirm("\n¿Confirmas la operación?"):
            raise typer.Exit(code=1)

    # 4. Ejecución
    try:
        new_list = pm.update_config_list(action, key, cleaned, profile)
        console.print(f"[green]✔ Operación exitosa. Lista actual: {new_list}[/green]")
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


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


def validate_profile_name(profile_name: str):
    def normalize_string(value: str):
        if not isinstance(value, str):
            return value
        return value.strip()

    cleaned = normalize_string(profile_name)
    # TODO: un nombre como `mi-perfil` no es válido en isidentifier ¿es un error?

    if not cleaned.isidentifier():
        raise typer.BadParameter(
            "El nombre del perfil solo puede contener letras, números y guiones bajos, y no puede comenzar con un número."
        )
    profile_name = cleaned

    if not pm.exists(cleaned):
        return profile_name

    console.print(
        Panel(
            f"[bold red]El perfil '{profile_name}' ya existe.[/bold red]\n\n"
            "Por seguridad y consistencia de datos (ADR-001), los perfiles son inmutables.\n"
            f"Si deseas reutilizar este nombre, primero debes eliminar el perfil existente:\n\n"
            f"   [yellow]totelegram profile delete {profile_name}[/yellow]",
            title="Operación no permitida",
            border_style="red",
        )
    )
    raise typer.Exit(code=1)


def _suggest_profile_activation(profile_name: str):
    """Lógica de UI para activar el perfil recién creado."""
    try:
        config = pm.get_registry()

        if config.active is None:
            pm.activate(profile_name)
            console.print(
                f"[green]Perfil '{profile_name}' activado automáticamente.[/green]"
            )

        elif config.active != profile_name:
            if typer.confirm("¿Deseas activar este perfil ahora?"):
                pm.activate(profile_name)
                console.print(f"[green]Perfil '{profile_name}' activado.[/green]")

    except Exception as e:
        console.print(
            f"[yellow]No se pudo activar el perfil automáticamente: {e}[/yellow]"
        )


def _validate_chat_with_retry(validator, client, chat_id) -> bool:
    """Retorna True si el chat es válido o el usuario decide continuar."""
    if validator.validate_chat_id(client, chat_id):
        return True

    ui.console.print(
        Panel(
            f"El chat '[cyan]{chat_id}[/cyan]' no parece accesible.\n"
            "Puedes guardarlo ahora y corregirlo después con [yellow]profile set CHAT_ID[/yellow].",
            title="Chat no encontrado",
            border_style="yellow",
        )
    )
    result = typer.confirm("¿Deseas guardar el perfil de todos modos?", default=True)
    if not result:
        console.print("Operación cancelada por el usuario.")
        return False
    console.print("Continuando con el guardado del perfil...")
    return True


def _finalize_profile(name, temp_file, final_file, api_id, api_hash, chat_id):
    """Realiza el renombramiento y creación del archivo .env."""
    try:
        temp_file.rename(final_file)
        path = pm.create(name=name, api_id=api_id, api_hash=api_hash, chat_id=chat_id)
        console.print(f"\n[bold green]✔ Perfil '{name}' creado en:[/bold green] {path}")
    except Exception as e:
        if final_file.exists():
            final_file.unlink()
        raise e
