from typing import Optional

import typer

from totelegram.commands.profile_logic import ProfileCreateLogic, render_profiles_table
from totelegram.commands.profile_ui import ProfileUI
from totelegram.commands.profile_utils import validate_profile_name
from totelegram.console import UI, console
from totelegram.core.schemas import CLIState
from totelegram.services.validator import ValidationService
from totelegram.utils import VALUE_NOT_SET

app = typer.Typer(help="Gestión de perfiles de configuración.")
ui = ProfileUI(console)


@app.callback(invoke_without_command=True)
def profile_profile(ctx: typer.Context):
    """Muestra la lista de perfiles si no se pasa un subcomando."""
    if ctx.invoked_subcommand is not None:
        return
    list_profiles(ctx, False)


@app.command("list")
def list_profiles(
    ctx: typer.Context,
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Salida silenciosa"),
):
    """Muestra la lista de perfiles si no se pasa un subcomando."""
    state: CLIState = ctx.obj
    manager = state.manager

    profiles = manager.get_all_profiles()
    if not profiles:
        UI.warn("No se encontraron perfiles en el sistema.")
        UI.info("Usa 'totelegram profile create' para empezar.")
        return

    active_profile = manager.get_active_settings_name()
    render_profiles_table(manager, active_profile, profiles, quiet)


@app.command("create")
def create_profile(
    ctx: typer.Context,
    profile_name: str = typer.Option(..., prompt=True, callback=validate_profile_name),
    api_id: int = typer.Option(..., prompt=True),
    api_hash: str = typer.Option(..., prompt=True),
    chat_id: Optional[str] = typer.Option(
        VALUE_NOT_SET, help="Chat ID (opcional para saltar asistente)"
    ),
):
    """Crea una nueva identidad y la vincula con tu cuenta de Telegram."""

    state: CLIState = ctx.obj
    manager = state.manager

    validator = ValidationService()
    validate_profile = ProfileCreateLogic(manager, profile_name)

    validate_profile.validate_profile_exists(profile_name)

    validate_profile.proccess_login(profile_name, api_id, api_hash)
    final_chat_id = validate_profile.procces_dest(validator, manager, profile_name, api_id, api_hash, chat_id)
    validate_profile.store_chat_id_and_active_profile(final_chat_id)


@app.command("switch")
def switch_profile(
    ctx: typer.Context,
    name: str = typer.Argument(
        ..., help="Nombre del perfil a activar globalmente", metavar="PERFIL"
    ),
):
    """
    Cambia el perfil activo del sistema. Solo permite activar perfiles que estén completos (Configuración + Sesión).
    """

    state: CLIState = ctx.obj
    manager = state.manager

    profile = manager.get_profile(name)
    if profile is None:
        UI.error(f"El perfil '[bold]{name}[/]' no existe.")
        UI.info(
            "Usa [cyan]totelegram profile list[/cyan] para ver los perfiles disponibles."
        )
        raise typer.Exit(code=1)

    if not profile.is_trinity:
        UI.error(
            f"No se puede activar el perfil '[bold]{name}[/]' porque está incompleto."
        )

        if not profile.has_env:
            UI.info("Falta el archivo de configuración (.env).")
        if not profile.has_session:
            UI.info("Falta el archivo de sesión de Telegram (.session).")

        UI.warn(
            f"Debes reparar o eliminar este perfil "
            f"([cyan]totelegram profile delete {name}[/cyan]) antes de usarlo."
        )
        raise typer.Exit(code=1)

    current_active = manager.get_active_settings_name()
    if str(current_active).lower() == name.lower():
        UI.info(f"El perfil '[bold]{name}[/]' ya es el perfil activo.")
        return

    try:
        manager.set_settings_name_as_active(name)
        UI.success(
            f"Perfil cambiado exitosamente. Ahora usando: [bold green]{name}[/bold green]"
        )

        settings = manager.get_settings(name)
        destino = (
            settings.chat_id
            if settings.chat_id != "NOT_SET"
            else "[yellow]Sin configurar[/yellow]"
        )
        UI.info(f"Destino actual: {destino}")

    except Exception as e:
        UI.error(f"Ocurrió un error al intentar activar el perfil: {e}")
        raise typer.Exit(code=1)


# def get_chat_name(settings: Settings) -> Optional[str]:

#     with DatabaseSession(settings.database_path):
#         target = settings.chat_id
#         chat = None
#         try:
#             chat = TelegramChat.get_or_none(TelegramChat.id == int(target))
#         except (ValueError, TypeError):
#             clean_username = str(target).replace("@", "")
#             chat = TelegramChat.get_or_none(TelegramChat.username == clean_username)

#         if chat:
#             return chat.title


@app.command("delete")
def delete_profile(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Nombre del perfil a eliminar"),
    force: bool = typer.Option(
        False, "--yes", "-y", help="Confirmar borrado sin preguntar"
    ),
):
    """Elimina un perfil y sus archivos asociados (configuración y sesión)."""
    state: CLIState = ctx.obj
    manager = state.manager

    profile = manager.get_profile(name)
    if profile is None:
        UI.error(f"No se encontró perfil con el nombre '{name}'.")
        raise typer.Exit(code=1)

    if not profile.has_env and not profile.has_session:
        UI.error("No se encontraron archivos para el perfil.")
        raise typer.Exit(code=1)

    if not force:
        console.print()
        UI.warn(f"¿Estás seguro de que deseas eliminar el perfil '{name}'? \n")
        confirm = typer.confirm("Confirmar")
        if not confirm:
            UI.info("Operación cancelada.")
            return

    try:
        deleted = manager.delete_profile(profile)

        if deleted:
            UI.success(f"Perfil '{name}' eliminado correctamente.")
            for f in deleted:
                UI.info(f"Archivo eliminado: [dim]{f.name}[/dim]")
        else:
            UI.warn(
                f"No se encontraron archivos físicos para '{name}', pero el sistema se aseguró de limpiar el rastro."
            )

    except Exception as e:
        UI.error(f"Error al eliminar el perfil: {e}")
        raise typer.Exit(code=1)
