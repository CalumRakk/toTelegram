import logging
from typing import List, cast

import typer
from pyrogram import enums
from pyrogram.types import Message

from totelegram.cli.state import CLIState
from totelegram.cli.ui import UI, console
from totelegram.models import TelegramUser
from totelegram.packaging.importer import SnapshotImporter
from totelegram.packaging.indexer import TapeIndexService
from totelegram.schemas import VALUE_NOT_SET, Commands

logger = logging.getLogger(__name__)


def index_tapes(
    ctx: typer.Context,
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Fuerza la re-indexación de todas las cintas remotas, incluso las ya registradas.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Aplica la indexación sin solicitar confirmación interactiva.",
    ),
):
    """
    Sincroniza e indexa en la base de datos local los archivos contenidos en cintas (.ttape) de Telegram.
    Permite la búsqueda rápida de archivos internos con 'download --file'.
    """
    state: CLIState = ctx.obj
    manager = state.manager
    profile_name = manager.resolve_profile_name(state.profile_name, strict=True)
    assert profile_name is not None
    settings = manager.get_settings(profile_name)

    if settings.chat_id == VALUE_NOT_SET:
        UI.error("El chat destino no está configurado.")
        UI.tip(
            "Configura el destino usando uno de estos comandos:",
            commands=[
                f"{Commands.CONFIG_SET} chat_id <ID>",
                f"{Commands.CONFIG_SEARCH} <NOMBRE>",
            ],
        )
        raise typer.Exit(1)

    UI.separator()

    with state.scope() as (client, db):
        indexer = TapeIndexService(client, db)

        with UI.loading("Buscando cintas archivadas (#type_tape) en Telegram..."):
            report = indexer.check_unindexed_tapes(settings.chat_id)

        # Determinar qué mensajes procesar
        if force:
            try:
                messages_to_process = cast(
                    List[Message],
                    list(
                        client.search_messages(
                            chat_id=settings.chat_id,
                            query="#type_tape",
                            filter=enums.MessagesFilter.DOCUMENT,
                            limit=200,
                        )  # type: ignore
                    ),
                )
            except Exception as e:
                UI.error(f"Error consultando Telegram: {e}")
                raise typer.Exit(1)
        else:
            if report.unindexed_count == 0:
                UI.success(
                    "Todas las cintas archivadas ya están indexadas en tu base de datos local."
                )
                UI.info(
                    f"Total en Telegram: [bold]{report.total_remote_tapes}[/] cintas."
                )
                raise typer.Exit(0)

            messages_to_process = report.unindexed_messages

        total_targets = len(messages_to_process)
        UI.info(
            f"Se encontraron [bold cyan]{total_targets}[/] cintas pendientes de indexar "
            f"(de {report.total_remote_tapes} totales en Telegram)."
        )

        # Confirmación interactiva
        if not yes:
            console.print()
            confirmed = typer.confirm(
                f"¿Deseas descargar e indexar estas {total_targets} cintas en SQLite?",
                default=True,
            )
            if not confirmed:
                UI.info("Operación cancelada por el usuario.")
                raise typer.Exit(0)

        # Resolver el usuario actual de Telegram para trazabilidad de propiedad
        owner = None
        try:
            me = client.get_me()
            if me:
                owner = TelegramUser.get_or_create_from_tg(me)
        except Exception:  # noqa: S110
            pass

        # Proceso de descarga e inserción delegado en SnapshotImporter
        console.print()
        with console.status(
            "[bold green]Descargando e indexando cintas...[/]"
        ) as status:

            def on_progress(cur: int, tot: int, name: str):
                status.update(
                    f"[bold green]Indexando inventario ({cur}/{tot}):[/] {name}"
                )

            result = SnapshotImporter.rehydrate_from_messages(
                client=client,
                messages=messages_to_process,
                db=db,
                owner=owner,
                on_progress=on_progress,
            )

        succeeded = result.succeeded_count
        total_errors = result.errors

        # Balance final
        UI.separator()
        if succeeded > 0:
            UI.success(
                f"Indexación completada: [bold]{succeeded}[/] cintas archivadas registradas con éxito."
            )
            UI.info(
                "Ahora puedes buscar archivos dentro de estas carpetas o extraerlos quirúrgicamente."
            )

        if total_errors:
            UI.error(f"Se produjeron errores en [bold]{len(total_errors)}[/] cintas:")
            for fname, err in total_errors:
                console.print(f"  [red]• {fname}:[/] [dim]{err}[/]")
            raise typer.Exit(1)
