import logging
from pathlib import Path
from typing import List

import typer

from totelegram.cli.state import CLIState
from totelegram.cli.ui import UI, DisplayLedger, console
from totelegram.packaging.ledger import FileAuditResult, ReceiptLedger
from totelegram.telegram.resolver import TargetResolver
from totelegram.telegram.types import TargetResolutionStatus

logger = logging.getLogger(__name__)


def prune_files(
    ctx: typer.Context,
    path: Path = typer.Argument(
        Path("."),
        exists=True,
        file_okay=False,
        dir_okay=True,
        help="Directorio local donde auditar y liberar espacio de archivos respaldados.",
    ),
    target: str | None = typer.Option(
        None,
        "--target",
        "-t",
        help="Solo liberar archivos que estén respaldados en este chat específico.",
    ),
    recursive: bool = typer.Option(
        False,
        "--recursive",
        "-r",
        help="Auditar y limpiar subdirectorios recursivamente.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Muestra qué archivos se eliminarían y cuánto espacio se liberaría sin tocar el disco.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Omite la confirmación interactiva antes de eliminar los archivos locales.",
    ),
):
    """
    Libera espacio en disco eliminando de forma segura los archivos locales que ya
    han sido respaldados al 100% en Telegram, conservando el recibo (.totelegram.json).
    """
    state: CLIState = ctx.obj
    resolved_target_id: int | str | None = None

    # Si se especificó --target, resolver chat
    if target:
        with state.scope() as (client, db):
            resolver = TargetResolver(client, db)
            res = resolver.resolve(target)
            if res.status == TargetResolutionStatus.RESOLVED:
                resolved_target_id = res.chat_id
            else:
                UI.error(
                    res.error_message or f"No se pudo resolver el destino '{target}'."
                )
                raise typer.Exit(1)

    target_dir = path.resolve()
    dirs_to_scan: List[Path] = [target_dir]

    if recursive:
        dirs_to_scan.extend(
            d
            for d in target_dir.rglob("*")
            if d.is_dir() and not d.name.startswith(".")
        )

    candidates: List[FileAuditResult] = []

    with console.status(
        "[bold cyan]Auditando recibos locales (.totelegram.json)...[/]"
    ):
        for d in dirs_to_scan:
            audit_list = ReceiptLedger.audit_directory(
                d, target_chat_id=resolved_target_id
            )
            for item in audit_list:
                if item.is_safe_to_prune:
                    candidates.append(item)

    if not candidates:
        target_info = f" para el destino '{target}'" if target else ""
        UI.info(
            f"No hay archivos respaldados que puedan liberarse de forma segura en '{target_dir.name}'{target_info}."
        )
        raise typer.Exit(0)

    total_bytes = sum(c.size for c in candidates)

    UI.separator()
    DisplayLedger.show_prune_candidates(candidates, total_bytes)

    if dry_run:
        console.print()
        UI.info(
            "Modo [bold]--dry-run[/] activo. No se eliminó ningún archivo de tu equipo."
        )
        raise typer.Exit(0)

    if not yes:
        console.print()
        confirmed = typer.confirm(
            f"¿Deseas eliminar estos {len(candidates)} archivos del disco local para liberar espacio?",
            default=False,
        )
        if not confirmed:
            UI.info("Operación cancelada por el usuario.")
            raise typer.Exit(0)

    # Eliminación física segura de los archivos locales
    deleted_count = 0
    freed_bytes = 0

    for c in candidates:
        if c.path and c.path.exists() and c.path.is_file():
            try:
                c.path.unlink()
                deleted_count += 1
                freed_bytes += c.size
                logger.debug(f"Archivo liberado de disco: {c.path}")
            except Exception as e:
                logger.warning(f"No se pudo eliminar {c.path.name}: {e}")

    console.print()
    DisplayLedger.show_prune_summary(deleted_count, freed_bytes)
