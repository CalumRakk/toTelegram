import logging
from pathlib import Path

import typer
from rich.progress import Progress, SpinnerColumn, TextColumn

from totelegram.cli.state import CLIState
from totelegram.cli.ui import UI, console
from totelegram.engine.forwarder import ForwardError, SnapshotForwarder
from totelegram.models import TelegramChat, TelegramUser
from totelegram.packaging.snapshot import SnapshotService
from totelegram.schemas import VALUE_NOT_SET
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
        help="Nombre, @username o ID del chat de destino (sobrescribe el destino por defecto).",
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
    manager = state.manager

    profile_name = manager.resolve_profile_name(state.profile_name, strict=True)
    assert profile_name is not None
    settings = manager.get_settings(profile_name)

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
        resolved_chat_id: int | str | None = None

        if target:
            with UI.loading(f"Resolviendo destino '[bold]{target}[/]'..."):
                resolution = resolver.resolve(target)
            if resolution.status == TargetResolutionStatus.RESOLVED:
                resolved_chat_id = resolution.chat_id
            else:
                UI.error(
                    resolution.error_message
                    or f"No se pudo resolver el destino '{target}'."
                )
                raise typer.Exit(1)
        elif settings.chat_id != VALUE_NOT_SET:
            resolved_chat_id = settings.chat_id
        else:
            UI.error("No se ha especificado un chat de destino.")
            UI.tip(
                "Especifica un destino usando --target <chat> o configura uno por defecto:",
                commands=['totelegram forward ... --target "Mi Canal"'],
            )
            raise typer.Exit(1)

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

        # Ejecución del reenvío
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
            except Exception as e:
                UI.error(f"Error crítico durante el reenvío: {e}")
                logger.exception("Error en comando forward")
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
