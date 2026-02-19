import tempfile
from pathlib import Path
from typing import Optional

import typer
from rich.rule import Rule

from totelegram.commands.profile_logic import render_profiles_table
from totelegram.commands.profile_ui import ProfileUI
from totelegram.commands.profile_utils import (
    validate_profile_name,
)
from totelegram.console import UI, console
from totelegram.core.schemas import CLIState
from totelegram.services.chat_resolver import ChatResolverService
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

    existing_profile = manager.get_profile(profile_name)
    if existing_profile is not None:
        UI.error(f"No se puede crear el perfil '{profile_name}'.")

        if existing_profile.is_trinity:
            UI.warn("El perfil existe.")
        elif existing_profile.has_session:
            UI.warn("Existe una sesión de Telegram huérfana con este nombre.")
        elif existing_profile.has_env:
            UI.warn("Existe un archivo de configuración (.env) sin sesión asociada.")

        UI.info("\nPara empezar de cero, elimina los rastros primero usando:")
        UI.info(f"  [cyan]totelegram profile delete {profile_name}[/cyan]")
        raise typer.Exit(code=1)

    final_session_path = manager.profiles_dir / f"{profile_name}.session"

    validator = ValidationService()

    # ASEGURAR SESSION

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_session_path = Path(temp_dir) / f"{profile_name}.session"
            UI.info("\n[bold cyan]1. Autenticación con Telegram[/bold cyan]")
            UI.info(
                "[dim]Se solicitará tu número telefónico y código (OTP) para vincular la cuenta.[/dim]\n"
            )

            with validator.validate_session(temp_dir, profile_name, api_id, api_hash):
                # Una vez dentro ya no necesitamos la session temp, salimos para liberar el archivo
                pass

            if not temp_session_path.exists():
                raise FileNotFoundError(
                    "Error crítico: No se generó el archivo de sesión."
                )

            manager.profiles_dir.mkdir(parents=True, exist_ok=True)
            temp_session_path.rename(final_session_path)
            UI.success(
                f"[green]Identidad salvada correctamente en {profile_name}.session[/green]"
            )

            settings_dict = {
                "api_id": api_id,
                "api_hash": api_hash,
                "profile_name": profile_name,
                "chat_id": VALUE_NOT_SET,
            }
            manager._write_all_settings(profile_name, settings_dict)

            UI.success(
                f"[green]Identidad salvada correctamente en {profile_name}.session[/green]"
            )
    except Exception as e:
        UI.error(f"Operación abortada durante el login: {e}")
        raise typer.Exit(code=1)

    # RESOLUCIÓN DEL DESTINO
    console.print(Rule(style="dim"))
    UI.info("[bold cyan]2. Configuración del Destino[/bold cyan]")

    final_chat_id = VALUE_NOT_SET
    try:
        with validator.validate_session(
            manager.profiles_dir, profile_name, api_id, api_hash
        ) as client:
            resolver = ChatResolverService(client)

            if chat_id != VALUE_NOT_SET:
                with UI.loading(f"Resolviendo '{chat_id}'..."):
                    result = resolver.resolve(chat_id)  # type: ignore

                if result.is_resolved and result.winner:
                    match = result.winner
                    final_chat_id = str(match.id)
                    UI.success(f"Destino encontrado: [bold]{match.title}[/]")

                    with UI.loading("Verificando permisos..."):
                        has_perms = validator.validate_send_action(client, match.id)

                    if not has_perms:
                        UI.warn(
                            "Destino guardado, pero actualmente NO TIENES permisos de escritura."
                        )
                        UI.info(
                            "[dim]Las subidas fallarán hasta que obtengas permisos en ese chat.[/dim]"
                        )
                else:
                    UI.warn(
                        f"No se pudo resolver '{chat_id}' de forma exacta (posible ambigüedad o no existe)."
                    )
            else:
                # POR IMPLEMENTAR
                final_chat_id = _capture_chat_id_wizard(validator, client)

    except Exception as e:
        UI.error(f"Error consultando a Telegram: {e}")
        UI.info("El destino no pudo ser configurado ahora.")

    # RESUMEN
    console.print(Rule(style="dim"))

    if final_chat_id != VALUE_NOT_SET:
        manager.set_setting(profile_name, "chat_id", final_chat_id)
        UI.success(f"Destino configurado exitosamente.")
    else:
        UI.warn("Perfil creado sin destino.")
        UI.info(
            "Usa [cyan]totelegram config set chat_id <ID>[/cyan] cuando estés listo."
        )

    manager.set_settings_name_as_active(profile_name)
    UI.success(
        f"[bold green]¡Perfil '{profile_name}' activado y listo para usar![/bold green]\n"
    )


@app.command("switch")
def switch_profile(
    ctx: typer.Context,
    name: str = typer.Argument(
        ..., help="Nombre del perfil a activar globalmente", metavar="PERFIL"
    ),
):
    """
    Cambia el perfil activo del sistema.
    Solo permite activar perfiles que estén completos (Configuración + Sesión).
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
