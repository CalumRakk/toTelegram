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
    UnsupportedTargetTypeError,
)
from totelegram.i18n import _
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
        help=_("Ruta al snapshot (.json.xz o .ttape) a replicar."),
    ),
    target: str | None = typer.Option(
        None,
        "--target",
        "-t",
        envvar="TOTELEGRAM_TARGET",
        help=_(
            "Destino en Telegram hacia donde replicar (o define TOTELEGRAM_TARGET)."
        ),
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help=_("Omite confirmaciones interactivas."),
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
    with UI.loading(
        _("Cargando snapshot: [bold]{name}[/]...").format(name=snapshot_path.name)
    ):
        try:
            manifest = SnapshotService.load_snapshot(snapshot_path)
        except Exception as e:
            UI.error(_("Error al leer el snapshot: {error}").format(error=e))
            raise typer.Exit(1)

    UI.separator()
    UI.print(
        _("Replicando recurso: [bold cyan]{name}[/]").format(
            name=manifest.source.filename
        ),
        indent=False,
    )
    UI.info(
        _(
            "Origen: [dim]Chat ID {chat_id}[/] • Partes a replicar: [bold]{parts}[/]"
        ).format(chat_id=manifest.chat_id, parts=len(manifest.parts))
    )

    with state.scope() as (client, db):
        resolver = TargetResolver(client, db)

        with UI.loading(
            _("Resolviendo destino '[bold]{target}[/]'...").format(target=target)
        ):
            resolution = resolver.resolve(target)

        if resolution.status == TargetResolutionStatus.RESOLVED:
            assert resolution.chat_id is not None
            resolved_chat_id = resolution.chat_id
        elif resolution.status == TargetResolutionStatus.AMBIGUOUS:
            raise TargetAmbiguousError(target, resolution.candidates)
        elif resolution.status == TargetResolutionStatus.UNSUPPORTED_TYPE:
            raise UnsupportedTargetTypeError(
                chat_id=resolution.chat_id or target,
                chat_title=resolution.title,
                chat_type=resolution.match.type if resolution.match else None,
            )
        else:
            raise ConfigurationError(
                message=resolution.error_message
                or _("No se pudo resolver el destino '{target}'.").format(
                    target=target
                ),
                hint=resolution.hint,
                exit_code=1,
            )

        # Pre-flight de destino
        try:
            tg_chat = client.get_chat(resolved_chat_id)
            target_chat_db, __ = TelegramChat.get_or_create_from_chat(tg_chat)
            me = client.get_me()
            owner = TelegramUser.get_or_create_from_tg(me)
        except Exception as e:
            UI.error(
                _("Error de acceso al chat destino ({chat_id}): {error}").format(
                    chat_id=resolved_chat_id, error=e
                )
            )
            raise typer.Exit(1)

        target_title = tg_chat.title or tg_chat.username or str(tg_chat.id)
        UI.info(
            _("Destino: [bold green]{title}[/] [dim]({chat_id})[/]").format(
                title=target_title, chat_id=target_chat_db.id
            )
        )

        if manifest.chat_id == target_chat_db.id:
            UI.warn(_("El chat de origen y destino son idénticos. Nada por hacer."))
            raise typer.Exit(0)

        # Confirmación interactiva
        if not yes:
            console.print()
            confirmed = typer.confirm(
                _("¿Deseas replicar {count} partes hacia '{target}'?").format(
                    count=len(manifest.parts), target=target_title
                ),
                default=True,
            )
            if not confirmed:
                UI.info(_("Operación cancelada por el usuario."))
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
                label=_("Replicando"),
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
            _(
                "Replicación completada exitosamente hacia [bold green]{title}[/]."
            ).format(title=target_title)
        )
        UI.info(
            _(
                "Partes replicadas: [bold]{count}[/] (0 bytes transferidos desde tu PC)."
            ).format(count=report.total_parts)
        )
        UI.info(
            _(
                "Nuevo Snapshot publicado en Telegram [bold cyan](Msg ID: {msg_id})[/]"
            ).format(msg_id=report.new_snapshot_message_id)
        )
