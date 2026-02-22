import tempfile
from typing import TYPE_CHECKING, Optional, Union

import typer

from totelegram.core.consts import Commands
from totelegram.services.chat_search import ChatSearchService

if TYPE_CHECKING:
    from pyrogram import Client # type: ignore

from totelegram.commands.views import DisplayGeneric, DisplayProfile
from totelegram.console import UI, console
from totelegram.core.schemas import AccessStatus, CLIState
from totelegram.core.setting import normalize_chat_id
from totelegram.services.auth import AuthLogic
from totelegram.services.chat_access import ChatAccessService
from totelegram.telegram import TelegramSession
from totelegram.utils import VALUE_NOT_SET, is_direct_identifier, is_valid_profile_name

app = typer.Typer(help="Gestión de perfiles de configuración.")


def _run_destination_wizard(client: "Client", current_depth:int=100) -> Optional[Union[str, int]]:
    """
    Inicia el asistente interactivo para determinar y validar el chat destino.
    Devuelve el identificador del chat (int o str) si se resuelve con éxito,
    o None si el usuario decide omitir la configuración.
    """

    def has_write_permission(target: Union[str, int])-> bool:
        with UI.loading("Verificando permisos..."):
            report= access_service.verify_access(target)

        if report.is_ready and report.chat:
            UI.success("Tienes permisos de escritura.")
            return True

        UI.error(f"Error: {report.reason}")
        if report.hint:
            UI.info(report.hint)

        return False

    search_service = ChatSearchService(client)
    access_service = ChatAccessService(client)

    while True:
        console.print("\n[bold cyan]Selección de Destino[/bold cyan]")
        console.print(" [1] Usar [bold]Mensajes guardados[/] (Nube personal)")
        console.print(" [2] Buscar entre tus Canales, Grupos o Chats.")
        console.print(
            " [3] Introduce un [bold]ID[/], [bold]@username[/] o [bold]link[/]."
        )
        console.print(" [4] Configurar más tarde (salir).")

        option = typer.prompt("\nSelecciona una opción", default="1")
        if option == "1":
            if has_write_permission("me"):
                return "me"

        elif option == "2":
            query = typer.prompt("\nEscribe el nombre del chat que buscas")
            console.print()

            while True:
                with UI.loading(f"Buscando chat que contenga [bold]{query}[/] (profundidad: {current_depth})..."):
                    resolution = search_service.search_by_name(query, is_exact=False, depth=current_depth)

                matches = resolution.all_unique_matches()

                DisplayGeneric.show_matches_summary(query, matches)
                DisplayGeneric.render_search_results(matches)

                # Si NO HAY resultados, damos opciones para intentar de nuevo o cambiar la búsqueda
                if not matches:
                    DisplayGeneric.show_search_tip()
                    console.print("\n[bold]¿Qué deseas hacer?[/bold]")
                    console.print(f" [1] Reintentar con '{query}' (Si ya enviaste el mensaje)")
                    console.print(f" [2] Búsqueda profunda (Escanear más chats antiguos)")
                    console.print(f" [3] Probar otro nombre.")
                    console.print(f" [4] Volver al menú principal.")

                    sub_opt = typer.prompt("\nSelecciona una acción", default="1")
                    if sub_opt == "1":
                        continue
                    elif sub_opt == "2":
                        current_depth += 100
                        continue
                    elif sub_opt == "3":
                        query = typer.prompt("Escribe el nuevo nombre")
                        current_depth = 50
                        continue
                    else:
                        break  # Rompe el sub-bucle y vuelve al menú principal

                # Si HAY resultados, procesamos la selección
                try:
                    choice = typer.prompt("\nSelecciona el numeral (#) o '0' para cancelar", default="0")

                    # Manejo explicíto del 0, se evitan bug de lista [-1]
                    if choice == "0":
                        UI.info("Selección cancelada. Volviendo al menú principal.")
                        break

                    choice_idx = int(choice) - 1

                    if choice_idx < 0 or choice_idx >= len(matches):
                        raise ValueError()

                    seleccion = matches[choice_idx]

                    if has_write_permission(seleccion.id):
                        return seleccion.id
                    else:
                        # Si elige uno que no tiene permisos, lo dejamos volver a intentar
                        break

                except (ValueError, IndexError):
                    console.print("[red]Selección inválida. Inténtalo de nuevo.[/red]")

        elif option == "3":
            user_input = typer.prompt("Escribe el identificador")
            if not is_direct_identifier(user_input):
                UI.error("Formato inválido.")
                continue

            target = normalize_chat_id(user_input)
            if has_write_permission(target):
                return target

        elif option == "4":
            return None

        else:
            UI.error("Opción inválida.")


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
        command= f"{Commands.PROFILE_CREATE}"
        UI.tip(f"Crea un perfil usando el siguiente comando:", commands=command, spacing="top")
        return

    active_profile = manager.get_active_profile_name()
    DisplayProfile.render_profiles_table(manager, active_profile, profiles, quiet)

    if not active_profile:
        UI.warn("No hay un perfil activo en el sistema.")
        UI.tip("Activa un perfil usando el comando:", commands=f"{Commands.PROFILE_SWITCH} [NOMBRE]", spacing="top")




@app.command("create")
def create_profile(
    ctx: typer.Context,
    profile_name: str = typer.Option(..., prompt=True),
    api_id: int = typer.Option(..., prompt=True),
    api_hash: str = typer.Option(..., prompt=True),
    chat_id: Optional[str] = typer.Option(
        VALUE_NOT_SET, help="Chat ID (opcional para saltar asistente)"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Forzar creación incluso si el perfil ya existe")
):
    """Crea una nueva identidad y la vincula con tu cuenta de Telegram."""

    state: CLIState = ctx.obj
    manager = state.manager

    if profile_name is None or not is_valid_profile_name(profile_name):
        UI.error("No se ha especificado un nombre de perfil valido.")
        raise typer.Exit(1)

    existing_profile = state.manager.get_profile(profile_name)
    if existing_profile:
        if force is False:
            UI.error(f"No se puede crear el perfil '{profile_name}'.")

            DisplayProfile.show_profile_conflict(existing_profile)

            DisplayProfile.show_delete_hint(existing_profile.name)
            raise typer.Exit(1)
        else:
            manager.delete_profile(existing_profile)
            UI.warn(f"Perfil '{profile_name}' existente eliminado por opción '--force'.")

    DisplayProfile.announce_start_profile_creation(profile_name)

    with tempfile.TemporaryDirectory() as temp_dir:
        auth = AuthLogic(
            profile_name=profile_name,
            api_id=api_id,
            api_hash=api_hash,
            chat_id=chat_id,
            manager=manager,
            temp_dir=temp_dir,
        )
        auth.initialize_profile()

    DisplayProfile.announce_profile_creation(profile_name)

    DisplayProfile.announce_start_destination_setup()

    assert chat_id is not None
    is_chat_id_specified = chat_id != VALUE_NOT_SET

    with TelegramSession.from_profile(profile_name, manager) as client:
        access_service = ChatAccessService(client)
        if is_chat_id_specified:
            if not is_direct_identifier(chat_id):
                UI.error(f"El {chat_id=} no es un identificador (ID, username, link).")
                raise typer.Exit(1)

            chat_id_normalized = normalize_chat_id(chat_id)
            report = access_service.verify_access(chat_id_normalized)

            if report.is_ready and report.chat:
                UI.success("Tienes permisos de escritura.")
                manager.set_setting(profile_name, "chat_id", report.chat.id)
                UI.success(f"Configuración 'chat_id' actualizada.")
            elif report.status == AccessStatus.NOT_FOUND:
                DisplayGeneric.warn_report_access_not_found(chat_id_normalized)
                raise typer.Exit(1)
            else:
                DisplayGeneric.warn_report_access_permissions()
                raise typer.Exit(1)
        else:
            resolved_chat_id = _run_destination_wizard(client)
            if resolved_chat_id:
                manager.set_setting(profile_name, "chat_id", resolved_chat_id)
                UI.success("Configuración 'chat_id' actualizada.")
            else:
                UI.info("Configuración de destino omitida. Puedes hacerlo luego.")



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

    current_active = manager.get_active_profile_name()
    if str(current_active).lower() == name.lower():
        UI.info(f"El perfil '[bold]{name}[/]' ya es el perfil activo.")
        return

    try:
        manager.set_profile_name_as_active(name)
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
