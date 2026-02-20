from typing import Optional

import typer

from totelegram.commands.config_logic import classify_intent
from totelegram.commands.profile_logic import render_profiles_table
from totelegram.commands.profile_ui import ProfileUI
from totelegram.commands.profile_utils import validate_profile_name
from totelegram.console import UI, console
from totelegram.core.schemas import AccessStatus, CLIState
from totelegram.core.setting import normalize_chat_id
from totelegram.services.auth import AuthLogic
from totelegram.services.chat_access import ChatAccessService
from totelegram.telegram import TelegramSession
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

    auth = AuthLogic(state, api_id, api_hash, chat_id)

    auth.proccess()

    settings = manager.get_settings(profile_name)

    quey = normalize_chat_id(chat_id)
    intent_type = classify_intent(quey)

    if not intent_type.is_direct:
        UI.warn(f"El chat_id {chat_id} no es correcto.")
        UI.info("Utiliza [bold]config set chat_id <ID>[/] para configurarlo.")
        UI.info("Si no sabes el ID, use [bold]totelegram config resolve[/].")
        raise typer.Exit(1)

    with TelegramSession(
        session_name=profile_name,
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        worktable=manager.profiles_dir,
    ) as client:

        access_service = ChatAccessService(client)
        report = access_service.verify_access(quey)
        if report.status == AccessStatus.NOT_FOUND:
            # TODO: aplicar aqui el winzard interactivo.
            UI.warn(f"El chat_id {chat_id} no es correcto.")
            UI.info("Utiliza [bold]config set chat_id <ID>[/] para configurarlo.")
            UI.info("Si no sabes el ID, use [bold]totelegram config resolve[/].")
            raise typer.Exit(1)

        assert report.chat is not None

        if report.is_ready:
            UI.success("Tienes permisos de escritura.")
            manager.set_setting(profile_name, "chat_id", str(report.chat.id))
            UI.success(f"Configuración 'chat_id' actualizada.")
        else:
            UI.warn("No tienes permisos de escritura en el chat.")
            UI.warn("Corrige los permisos antes de intentar cualquier subida.")
            UI.warn("Configuración 'chat_id' no actualizada.")


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
