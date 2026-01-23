from typing import TYPE_CHECKING, List, Literal, Optional

import typer
from rich.panel import Panel

from totelegram.commands.profile_ui import ProfileUI
from totelegram.console import UI, console
from totelegram.core.registry import ProfileManager
from totelegram.core.setting import CHAT_ID_NOT_SET, Settings, normalize_chat_id
from totelegram.store.database import DatabaseSession
from totelegram.store.models import TelegramChat

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore

    from totelegram.services.validator import ValidationService

pm = ProfileManager()
ui = ProfileUI(console)


def get_friendly_chat_name(settings: Settings) -> str:
    """
    Aplica las reglas heurísticas para devolver un nombre amigable
    sin necesariamente golpear la red o la DB.
    """
    val = str(settings.chat_id).lower().strip()

    if val.lower() in ["me", "self"]:
        return "Mensajes Guardados"

    # Usernames
    if val.startswith("@"):
        # TODO: analizar si vale la pena consultar el la db el `title`
        return val

    # IDs Numéricos
    if val.replace("-", "").isdigit():
        with DatabaseSession(settings.database_path):
            chat = TelegramChat.get_or_none(TelegramChat.id == int(val))
            if chat:
                return f"{chat.title}"

    return "[Destino sin identificar]"


def warn_if_override_active():
    """Avisa si se está usando --use en un comando que no lo requiere."""
    if pm._global_override:
        console.print(
            f"[dim yellow]Nota: El contexto activo por flag (--use {pm._global_override}) "
            f"se ignorará para esta operación de gestión.[/dim yellow]\n"
        )


def handle_list_operation(
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
        console.print(
            f"[green][OK] Operación exitosa. Lista actual: {new_list}[/green]"
        )
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


def suggest_profile_activation(profile_name: str):
    """Sugerencia de activación automática.

    Si el perfil activo es None, lo activa.
    Si el perfil activo es distinto al indicado, pide confirmación.
    """
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


def _capture_chat_id_wizard(validator: "ValidationService", client: "Client") -> str:

    ui.console.print("\n[bold cyan]Selección de Destino[/bold cyan]")
    ui.console.print(" [1] Se configurará [bold]Mensajes guardados[/]")
    ui.console.print(" [2] Buscar entres tus Canales, Grupos, etc.")
    ui.console.print(" [3] Introducir [bold]ID[/] o [bold]@username[/].")
    ui.console.print(" [4] Se configurará más tarde.")

    opcion = typer.prompt("\nElige una opción", default="1")

    if opcion == "1":
        UI.info("Chat de destino: [bold]Mensajes Guardados[/]")
        return "me"

    if opcion == "2":
        rul = _interactive_search_loop(validator, client)
        UI.info(f"Chat de destino: {rul}")
        return rul

    if opcion == "3":
        r = typer.prompt("Introduce el ID o @username")
        r_norm = str(normalize_chat_id(r))
        UI.info(f"Chat de destino: {r_norm}")
        return r_norm

    UI.info(f"Chat de destino: {CHAT_ID_NOT_SET}")
    return CHAT_ID_NOT_SET


def _interactive_search_loop(validator: "ValidationService", client: "Client") -> str:
    """Bucle interactivo de búsqueda con TIPS y reintentos."""
    query = typer.prompt("Escribe el nombre del chat que buscas")
    current_limit = 50

    ui.render_privacy_notice()

    while True:
        results, scanned = validator.search_chats(client, query, limit=current_limit)
        ui.render_search_results_feedback(query, scanned, len(results))

        if results:
            ui.render_search_results(results)
            choice = typer.prompt(
                "\nSelecciona el numeral (#) o '0' para buscar de nuevo", default="0"
            )

            if choice == "0":
                query = typer.prompt("Escribe el nuevo nombre")
                continue

            try:
                return str(results[int(choice) - 1]["id"])
            except (ValueError, IndexError):
                ui.console.print("[red]Selección inválida.[/red]")
                query = typer.prompt("Escribe el nuevo nombre")
                continue

        # SI NO HAY RESULTADOS
        ui.render_search_tip()
        ui.console.print("\n[bold]¿Qué deseas hacer?[/bold]")
        ui.console.print(
            f" [1] [bold]Reintentar[/bold] con '{query}' (Si ya enviaste el mensaje)"
        )
        ui.console.print(
            f" [2] [bold]Búsqueda profunda[/bold] (Escanear más chats antiguos)"
        )
        ui.console.print(
            f" [3] [bold]Probar otro nombre[/bold] (Cambiar palabra clave)"
        )
        ui.console.print(f" [4] [bold]Cancelar[/bold] y volver al menú principal")

        action = typer.prompt("\nSelecciona una acción", default="1")

        if action == "1":
            continue
        elif action == "2":
            current_limit += 100
            ui.console.print(
                f"[dim]Ampliando rango de búsqueda a {current_limit} chats...[/dim]"
            )
            continue
        elif action == "3":
            query = typer.prompt("Escribe el nuevo nombre")
            current_limit = 50
            continue
        else:
            return _capture_chat_id_wizard(
                validator, client
            )  # Volver al menú principal
