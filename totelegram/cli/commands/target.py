import logging
from typing import cast

import typer
from pyrogram.types import User

from totelegram.cli.state import CLIState
from totelegram.cli.ui import UI
from totelegram.cli.views.settings import DisplayTarget
from totelegram.models import TelegramChat
from totelegram.telegram.access import ChatAccessService
from totelegram.telegram.resolver import TargetResolver
from totelegram.telegram.types import TargetResolutionStatus

logger = logging.getLogger(__name__)


def manage_target(
    ctx: typer.Context,
    query: str | None = typer.Argument(
        None,
        help="Nombre, @username, link o ID del chat a inspeccionar. Déjalo vacío para listar destinos conocidos.",
    ),
    list_all: bool = typer.Option(
        False,
        "--list",
        "-l",
        help="Muestra todos los canales y grupos conocidos en tu base de datos local.",
    ),
    depth: int = typer.Option(
        100,
        "--depth",
        "-d",
        help="Profundidad de escaneo en diálogos recientes si buscas por nombre.",
    ),
):
    """
    Inspecciona y diagnostica destinos de Telegram.
    Verifica permisos de subida, capacidades de tamaño y resuelve nombres ambiguos.
    """
    state: CLIState = ctx.obj

    # MODO A: Listar destinos conocidos en SQLite si no se pasa argumento o se pide --list
    if query is None or list_all:
        UI.separator()
        with state.scope() as (_client, _db):
            known_chats = list(
                TelegramChat.select().order_by(TelegramChat.last_verified.desc())  # type: ignore
            )
            DisplayTarget.show_known_targets(known_chats)
            raise typer.Exit(0)

    # MODO B: Diagnóstico y resolución del destino consultado
    UI.separator()
    with state.scope() as (client, db):
        resolver = TargetResolver(client, db)
        access_service = ChatAccessService(client)

        with UI.loading(f"Resolviendo e inspeccionando destino '[bold]{query}[/]'..."):
            resolution = resolver.resolve(query, depth=depth)

        # 1. Resuelto con éxito
        if resolution.status == TargetResolutionStatus.RESOLVED:
            assert resolution.chat_id is not None
            me = cast(User, client.get_me())
            is_premium = getattr(me, "is_premium", False)
            account_max_size = (
                state.settings.tg_max_size_premium
                if is_premium
                else state.settings.tg_max_size_normal
            )
            can_delete = access_service.can_delete_messages(resolution.chat_id)

            DisplayTarget.show_diagnostic(
                resolution=resolution,
                can_delete=can_delete,
                account_max_size=account_max_size,
                is_premium=is_premium,
            )
            raise typer.Exit(0)

        # 2. Ambigüedad detectada (múltiples chats)
        if resolution.status == TargetResolutionStatus.AMBIGUOUS:
            DisplayTarget.show_ambiguous_targets(query, resolution.candidates)
            raise typer.Exit(1)

        # 3. No encontrado o sin permisos
        UI.error(
            resolution.error_message or f"No se pudo resolver el destino '{query}'."
        )
        if resolution.hint:
            UI.tip(resolution.hint)
        raise typer.Exit(1)
