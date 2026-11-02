import logging
from pathlib import Path

import typer
from rich.progress import Progress, SpinnerColumn, TextColumn

from totelegram.cli.state import CLIState
from totelegram.cli.ui import UI, console
from totelegram.engine.forwarder import ForwardError, SnapshotForwarder
from totelegram.exceptions import (
    ConfigurationError,
    TargetAmbiguousError,
    TargetNotSetError,
)
from totelegram.models import TelegramChat, TelegramUser
from totelegram.packaging.snapshot import SnapshotService
from totelegram.telegram.resolver import TargetResolver
from totelegram.telegram.types import TargetResolutionStatus

logger = logging.getLogger(__name__)


def forward_snapshot(
    ctx: typer.Context,
    snapshot_path: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Ruta al snapshot (.json.xz o .ttape) a replicar.",
    ),
    target: str | None = typer.Option(
        None,
        "--target",
        "-t",
        envvar="TOTELEGRAM_TARGET",
        help="Destino en Telegram hacia donde replicar (o define TOTELEGRAM_TARGET).",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Omite confirmaciones interactivas.",
    ),
):
    """
    Reenvía/replica un archivo o carpeta ya respaldada hacia otro chat de Telegram
    de forma instantánea y sin consumir ancho de banda de subida (0 bytes transferidos desde tu PC).
    """
    state: CLIState = ctx.obj

    if not target:
        raise TargetNotSetError()

    # Cargar y validar el snapshot original
    with UI.loading(f"Cargando snapshot: [bold]{snapshot_path.name}[/]..."):
        try:
            manifest = SnapshotService.load_snapshot(snapshot_path)
        except Exception as e:
            UI.error(f"Error al leer el snapshot: {e}")
            raise typer.Exit(1)

    UI.separator()
    UI.print(
        f"Replicando recurso: [bold cyan]{manifest.source.filename}[/]",
        indent=False,
    )
    UI.info(
        f"Origen: [dim]Chat ID {manifest.chat_id}[/] • "
        f"Partes a replicar: [bold]{len(manifest.parts)}[/]"
    )

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

        # Pre-flight de destino
        try:
            tg_chat = client.get_chat(resolved_chat_id)
            target_chat_db, _ = TelegramChat.get_or_create_from_chat(tg_chat)
            me = client.get_me()
            owner = TelegramUser.get_or_create_from_tg(me)
        except Exception as e:
            UI.error(f"Error de acceso al chat destino ({resolved_chat_id}): {e}")
            raise typer.Exit(1)

        target_title = tg_chat.title or tg_chat.username or str(tg_chat.id)
        UI.info(f"Destino: [bold green]{target_title}[/] [dim]({target_chat_db.id})[/]")

        if manifest.chat_id == target_chat_db.id:
            UI.warn("El chat de origen y destino son idénticos. Nada por hacer.")
            raise typer.Exit(0)

        # Confirmación interactiva
        if not yes:
            console.print()
            confirmed = typer.confirm(
                f"¿Deseas replicar {len(manifest.parts)} partes hacia '{target_title}'?",
                default=True,
            )
            if not confirmed:
                UI.info("Operación cancelada por el usuario.")
                raise typer.Exit(0)

        forwarder = SnapshotForwarder(client)

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.fields[label]}[/]"),
            TextColumn("[white]{task.fields[filename]}[/]"),
            console=console,
            transient=True,
        ) as progress:
            task_id = progress.add_task(
                "forwarding",
                total=len(manifest.parts),
                label="Replicando",
                filename="",
            )

            def on_progress(current: int, total: int, part_filename: str):
                progress.update(
                    task_id,
                    completed=current,
                    label=f"[{current}/{total}]",
                    filename=part_filename,
                )

            try:
                report = forwarder.forward(
                    snapshot_path=snapshot_path,
                    target_chat=target_chat_db,
                    owner=owner,
                    on_progress=on_progress,
                )
            except ForwardError as e:
                UI.error(str(e))
                raise typer.Exit(1)

        UI.separator()
        UI.success(
            f"Replicación completada exitosamente hacia [bold green]{target_title}[/]."
        )
        UI.info(
            f"Partes replicadas: [bold]{report.total_parts}[/] (0 bytes transferidos desde tu PC)."
        )
        UI.info(
            f"Nuevo Snapshot publicado en Telegram [bold cyan](Msg ID: {report.new_snapshot_message_id})[/]"
        )
