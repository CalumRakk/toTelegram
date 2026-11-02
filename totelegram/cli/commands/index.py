import logging
from typing import cast

import typer
from pyrogram import enums
from pyrogram.types import Message

from totelegram.cli.state import CLIState
from totelegram.cli.ui import UI, console
from totelegram.exceptions import (
    ConfigurationError,
    TargetAmbiguousError,
    TargetNotSetError,
)
from totelegram.models import TelegramUser
from totelegram.packaging.importer import SnapshotImporter
from totelegram.packaging.indexer import TapeIndexService
from totelegram.telegram.resolver import TargetResolver
from totelegram.telegram.types import TargetResolutionStatus

logger = logging.getLogger(__name__)


def index_tapes(
    ctx: typer.Context,
    target: str | None = typer.Option(
        None,
        "--target",
        "-t",
        envvar="TOTELEGRAM_TARGET",
        help="Chat o canal en Telegram donde buscar las cintas archivadas (o define TOTELEGRAM_TARGET).",
    ),
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

    if not target:
        raise TargetNotSetError()

    UI.separator()

    with state.scope() as (client, db):
        resolver = TargetResolver(client, db)

        with UI.loading(f"Resolviendo destino '[bold]{target}[/]'..."):
            resolution = resolver.resolve(target)

        if resolution.status == TargetResolutionStatus.RESOLVED:
            assert resolution.chat_id is not None
            resolved_chat_id = resolution.chat_id
        elif resolution.status == TargetResolutionStatus.AMBIGUOUS:
            raise TargetAmbiguousError(target, resolution.candidates)
        else:
            raise ConfigurationError(
                message=resolution.error_message
                or f"No se pudo resolver el destino '{target}'.",
                hint=resolution.hint,
                exit_code=1,
            )

        indexer = TapeIndexService(client, db)

        with UI.loading(
            f"Buscando cintas archivadas (#type_tape) en '{resolution.title}'..."
        ):
            report = indexer.check_unindexed_tapes(resolved_chat_id)

        if force:
            try:
                messages_to_process = cast(
                    list[Message],
                    list(
                        client.search_messages(
                            chat_id=resolved_chat_id,
                            query="#type_tape",
                            filter=enums.MessagesFilter.DOCUMENT,
                            limit=200,
                        )
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

        if not yes:
            console.print()
            confirmed = typer.confirm(
                f"¿Deseas descargar e indexar estas {total_targets} cintas en SQLite?",
                default=True,
            )
            if not confirmed:
                UI.info("Operación cancelada por el usuario.")
                raise typer.Exit(0)

        owner = None
        try:
            me = client.get_me()
            if me:
                owner = TelegramUser.get_or_create_from_tg(me)
        except Exception:  # noqa: S110
            pass

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

        UI.separator()
        if succeeded > 0:
            UI.success(
                f"Indexación completada: [bold]{succeeded}[/] cintas archivadas registradas con éxito."
            )
            UI.info(
                "Ahora puedes buscar archivos dentro de estas carpetas o extraerlos quirúrgicamente con 'download --file'."
            )

        if total_errors:
            UI.error(f"Se produjeron errores en [bold]{len(total_errors)}[/] cintas:")
            for fname, err in total_errors:
                console.print(f"  [red]• {fname}:[/] [dim]{err}[/]")
            raise typer.Exit(1)
