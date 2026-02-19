# import tempfile
# from pathlib import Path
# from typing import Optional, cast

# import typer
# from rich.rule import Rule

# from totelegram.commands.profile_ui import ProfileUI
# from totelegram.commands.profile_utils import (
#     _capture_chat_id_wizard,
#     suggest_profile_activation,
#     validate_profile_name,
# )
# from totelegram.console import UI, console
# from totelegram.core.schemas import CLIState
# from totelegram.core.setting import VALUE_NOT_SET, Settings
# from totelegram.services.validator import ValidationService
# from totelegram.store.database import DatabaseSession
# from totelegram.store.models import TelegramChat
# from totelegram.telegram import TelegramSession


from typing import cast

import typer

from totelegram.commands.profile_logic import render_profiles_table
from totelegram.commands.profile_ui import ProfileUI
from totelegram.console import UI, console
from totelegram.core.schemas import CLIState

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
    settings_name = cast(str, manager.resolve_settings_name(state.settings_name))

    profiles = manager.get_all_profiles()
    if not profiles:
        UI.warn("No se encontraron perfiles en el sistema.")
        UI.info("Usa 'totelegram profile create' para empezar.")
        return

    try:
        active_profile = manager.get_active_settings_name()
    except Exception:
        active_profile = None

    render_profiles_table(manager, active_profile, profiles, quiet)


# @app.command("create")
# def create_profile(
#     ctx: typer.Context,
#     profile_name: str = typer.Option(..., prompt=True, callback=validate_profile_name),
#     api_id: int = typer.Option(..., prompt=True),
#     api_hash: str = typer.Option(..., prompt=True, hide_input=True),
#     chat_id: Optional[str] = typer.Option(
#         None, help="Chat ID (opcional para saltar asistente)"
#     ),
# ):
#     state: CLIState = ctx.obj
#     manager = state.manager
#     settings_name = cast(str, manager.resolve_settings_name(state.settings_name))
#     settings = manager.get_settings(settings_name)

#     session_name = f"{profile_name}.session"
#     final_session = manager.profiles_dir / f"{profile_name}.session"

#     final_env = manager.profiles_dir / f"{profile_name}.env"

#     if final_session.exists() or final_env.exists():
#         UI.warn("Un perfil con ese nombre ya existe.")
#         UI.info
#         raise typer.BadParameter(
#             f"[bold red]Error:[/bold red] Ya existe un perfil con el nombre [bold]{profile_name}[/bold]."
#         )

#     temp_folder = tempfile.TemporaryDirectory()
#     temp_session = Path(temp_folder.name) / session_name

#     validator = ValidationService()

#     try:
#         UI.info("\n[bold cyan]1. Autenticación con Telegram[/bold cyan]")
#         UI.info(
#             "[dim]Se solicitará tu número y código (OTP) para vincular la cuenta.[/dim]\n"
#         )
#         with validator.validate_session(
#             manager.worktable, profile_name, api_id, api_hash
#         ) as _:
#             # Una vez dentro ya no necesitamos la session temp, salimos para liberar el archivo
#             pass

#         if not temp_session.exists():
#             raise FileNotFoundError("Error crítico: No se generó el archivo de sesión.")

#         # Por si habia una session previa, por alguna razon creativa del usuario.
#         if final_session.exists():
#             final_session.unlink()

#         temp_session.rename(final_session)
#         pm.create(
#             name=profile_name, api_id=api_id, api_hash=api_hash, chat_id=VALUE_NOT_SET
#         )
#         UI.success(
#             f"[green][v] Identidad salvada correctamente en {profile_name}.session[/green]"
#         )

#         # RESOLUCIÓN DE DESTINO
#         console.print(Rule(style="dim"))
#         UI.info("[bold cyan]2. Configuración del Destino[/bold cyan]")

#         resolved_chat = chat_id

#         settings = pm.get_settings(profile_name)

#         with TelegramSession(settings) as client:
#             if not resolved_chat:
#                 resolved_chat = _capture_chat_id_wizard(validator, client)

#             if resolved_chat and resolved_chat != "me":
#                 success = resolve_and_store_chat_logic(
#                     pm, resolved_chat, profile_name, client=client
#                 )
#                 if not success:
#                     resolved_chat = VALUE_NOT_SET
#             elif resolved_chat == "me":
#                 pm.update_config("CHAT_ID", "me", profile_name=profile_name)
#                 UI.success("[green][v] Destino configurado: Mensajes Guardados[/green]")

#         console.print(Rule(style="dim"))
#         if resolved_chat != VALUE_NOT_SET:
#             UI.success(
#                 f"[bold green]Perfil '{profile_name}' listo para usar.[/bold green]"
#             )
#         else:
#             UI.warn(
#                 f"[bold yellow]Perfil '{profile_name}' creado, pero sin destino configurado.[/bold yellow]"
#             )
#             UI.info("[dim]Puedes configurarlo luego usando:[/dim]")
#             UI.info(f"  [cyan]totelegram config set chat_id <id>[/cyan]\n")

#         suggest_profile_activation(pm, profile_name)
#     except Exception as e:
#         console.print(f"\n[bold red]Error en la creación:[/bold red] {e}")
#         raise typer.Exit(1)


# @app.command("switch")
# def use_profile(
#     ctx: typer.Context,
#     profile_name: str = typer.Argument(
#         None, help="Nombre del perfil a activar globalmente", metavar="PERFIL"
#     ),
# ):
#     """Cambia el perfil activo."""
#     env: Env = ctx.obj
#     pm.get_registry()  # se usa para invocar a `_sync_registry_with_filesystem` y tener todo actualizado.

#     if not profile_name:
#         raise typer.BadParameter(
#             "Debes indicar un perfil. Ejemplo: totelegram profile switch <PROFILE-NAME>"
#         )

#     try:
#         pm.activate(profile_name)
#         UI.success(f"Ahora usando el perfil: [bold]{profile_name}[/]")
#     except ValueError as e:
#         console.print(f"[bold red]Error:[/bold red] {e}")
#         list_profiles(ctx, quiet=True)


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


# @app.command("delete")
# def delete_profile(ctx: typer.Context, name: str):
#     """Borra un perfil (archivo .env y .session)."""
#     try:
#         env: Env = ctx.obj
#         pm.resolve_name(name)
#     except ValueError as e:
#         console.print(f"[bold red]Error:[/bold red] {e}")
#         list_profiles(ctx, quiet=True)
#         raise typer.Exit(code=1)

#     if typer.confirm(
#         f"¿Estás seguro de que deseas borrar el perfil '{name}' y su configuración?"
#     ):
#         pm.delete_profile(name)
#         UI.success(f"Perfil '[bold]{name}[/]' eliminado.")


@app.command("delete")
def delete_profile(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Nombre del perfil a eliminar"),
    force: bool = typer.Option(
        False, "--yes", "-y", help="Confirmar borrado sin preguntar"
    ),
):
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
        deleted = manager.delete_settings_profile(name)

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
