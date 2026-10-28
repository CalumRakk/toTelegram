import logging

import typer

from totelegram.cli.state import CLIState
from totelegram.cli.ui import UI
from totelegram.cli.views.settings import DisplayTarget
from totelegram.database import DatabaseSession
from totelegram.schemas import VALUE_NOT_SET
from totelegram.telegram.resolver import TargetResolver
from totelegram.telegram.types import TargetResolutionStatus

logger = logging.getLogger(__name__)


def manage_target(
    ctx: typer.Context,
    query: str | None = typer.Argument(
        None,
        help="Nombre, @username, link o ID del chat destino a configurar. Déjalo vacío para ver el destino actual.",
    ),
    clear: bool = typer.Option(
        False,
        "--clear",
        "-c",
        help="Elimina el chat destino configurado del perfil activo (lo deja pendiente).",
    ),
    depth: int = typer.Option(
        100,
        "--depth",
        "-d",
        help="Profundidad de escaneo de diálogos recientes si se busca por nombre.",
    ),
):
    """
    Gestiona el chat de destino por defecto para las subidas del perfil activo.
    Permite consultar el destino actual, asignar uno nuevo mediante búsqueda inteligente o limpiarlo.
    """
    state: CLIState = ctx.obj
    manager = state.manager

    profile_name = manager.resolve_profile_name(state.profile_name, strict=False)
    if not profile_name:
        UI.error("No hay un perfil activo configurado.")
        UI.tip(
            "Crea o selecciona un perfil primero usando 'profile list' o 'profile switch'."
        )
        raise typer.Exit(1)

    # Limpieza del target (--clear)
    if clear:
        changed = manager.set_setting(profile_name, "chat_id", VALUE_NOT_SET)
        if changed:
            UI.success("Chat destino eliminado del perfil activo.")
            UI.info(
                "Ahora las subidas requerirán la opción explícita '--target <chat>'."
            )
        else:
            UI.info("El chat destino ya se encontraba sin configurar (NOT_SET).")
        raise typer.Exit(0)

    settings = manager.get_settings(profile_name)

    # Inspección del destino actual (sin argumentos)
    if query is None:
        if settings.chat_id == VALUE_NOT_SET:
            DisplayTarget.show_no_target_warning()
            raise typer.Exit(0)

        UI.separator()
        with (
            state.get_telegram_session(profile_name) as client,
            DatabaseSession(manager.database_path, auto_init_schema=True) as db,
        ):
            resolver = TargetResolver(client, db)
            with UI.loading("Verificando destino actual y permisos en Telegram..."):
                resolution = resolver.resolve(settings.chat_id, depth=depth)

            DisplayTarget.show_current_status(
                profile_name, resolution, settings.chat_id
            )
        raise typer.Exit(0)

    # Asignación o Búsqueda de nuevo destino
    UI.separator()
    with (
        state.get_telegram_session(profile_name) as client,
        DatabaseSession(manager.database_path, auto_init_schema=True) as db,
    ):
        resolver = TargetResolver(client, db)

        with UI.loading(f"Resolviendo destino '{query}'..."):
            resolution = resolver.resolve(query, depth=depth)

        # Caso 3A: Resuelto con éxito (sin ambigüedades)
        if resolution.status == TargetResolutionStatus.RESOLVED:
            assert resolution.chat_id is not None
            manager.set_setting(profile_name, "chat_id", resolution.chat_id)
            UI.success(
                f"Chat destino actualizado: [bold cyan]{resolution.title}[/] "
                f"[dim](ID: {resolution.chat_id})[/]"
            )
            raise typer.Exit(0)

        # Caso 3B: Ambigüedad (múltiples coincidencias) -> Selección interactiva
        if resolution.status == TargetResolutionStatus.AMBIGUOUS:
            UI.warn(f"Se encontraron varias coincidencias para '{query}':")
            selected_match = DisplayTarget.prompt_candidate_selection(
                resolution.candidates
            )

            if selected_match is None:
                UI.info("Operación cancelada por el usuario.")
                raise typer.Exit(0)

            # Verificar permisos del seleccionado
            with UI.loading("Verificando permisos en el chat seleccionado..."):
                confirmed_res = resolver.resolve(selected_match.id)

            if confirmed_res.is_resolved:
                manager.set_setting(profile_name, "chat_id", selected_match.id)
                UI.success(
                    f"Chat destino asignado: [bold cyan]{selected_match.title}[/] "
                    f"[dim](ID: {selected_match.id})[/]"
                )
                raise typer.Exit(0)
            else:
                UI.error(f"No se pudo asignar el chat: {confirmed_res.error_message}")
                if confirmed_res.hint:
                    UI.tip(confirmed_res.hint)
                raise typer.Exit(1)

        # Caso 3C: Errores (No encontrado, sin permisos, etc.)
        UI.error(resolution.error_message or "No se pudo resolver el destino.")
        if resolution.hint:
            UI.tip(resolution.hint)
        raise typer.Exit(1)
