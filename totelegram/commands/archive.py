from pathlib import Path
from typing import cast

import typer
from rich.console import Console

from totelegram.commands.profile import list_profiles
from totelegram.console import UI
from totelegram.core.enums import DuplicatePolicy
from totelegram.core.plans import AskUserPlan, PhysicalUploadPlan, SkipPlan
from totelegram.core.registry import ProfileManager
from totelegram.core.setting import get_settings
from totelegram.services.chunking import ChunkingService
from totelegram.services.discovery import DiscoveryService
from totelegram.services.discovery_archive import ArchiveDiscoveryService
from totelegram.services.policy import PolicyExpert
from totelegram.services.snapshot import SnapshotService
from totelegram.services.uploader import UploadService
from totelegram.store.database import DatabaseSession
from totelegram.store.models import Job, TelegramChat
from totelegram.telegram import TelegramSession

console = Console()
app = typer.Typer(help="Comandos para archivado de carpetas (Modo Cinta).")


@app.command("folder")
def archive_folder(
    ctx: typer.Context,
    folder_path: Path = typer.Argument(
        ..., exists=True, file_okay=False, dir_okay=True, help="Carpeta a archivar."
    ),
    policy: DuplicatePolicy = typer.Option(
        DuplicatePolicy.SMART,
        "--policy",
        "-p",
        help="Política frente a carpetas ya existentes en el ecosistema.",
    ),
):
    """
    Convierte una carpeta en una Cinta de Datos (TAR) y la distribuye en volúmenes.
    """
    try:
        pm: ProfileManager = ctx.obj
        profile_name = pm.resolve_name()
        settings = get_settings(pm.get_path(profile_name))
    except ValueError as e:
        UI.error(f"Error de perfil: {e}")
        list_profiles(ctx, quiet=True)
        raise typer.Exit(code=1)

    console.print(
        f"\n[bold cyan]Iniciando Operación de Archivado:[/bold cyan] {folder_path.name}\n"
    )

    with DatabaseSession(settings.database_path), TelegramSession(settings) as client:
        archive_service = ArchiveDiscoveryService(
            root_path=folder_path,
            work_dir=settings.worktable,
            exclusion_patterns=settings.exclude_files + settings.exclude_files_default,
        )

        with console.status(
            "[bold green]Generando inventario y huella digital (T0)...[/bold green]"
        ):
            source, is_resume, is_ok = archive_service.discover_or_create_session()

        if is_resume and is_ok:
            UI.info(f"Sesión recuperada. Huella: [dim]{source.md5sum[:8]}...[/dim]")
            job = source.jobs.get()  # type: ignore
        elif is_resume is False and is_ok:
            assert source.inventory is not None
            UI.success(
                f"Inventario creado. Huella: [bold green]{source.md5sum[:8]}...[/bold green]"
            )
            UI.info(
                f"Contenido: {source.inventory.total_files} archivos, {source.size / (1024*1024):.2f} MB"
            )
            from pyrogram.types import Chat, User

            tg_chat = cast(Chat, client.get_chat(settings.chat_id))
            chat_db, _ = TelegramChat.get_or_create_from_tg(tg_chat)
            me = cast(User, client.get_me())

            job = Job.create_contract(source, chat_db, me.is_premium, settings)
        else:
            UI.error("Error al crear el inventario.")
            # TODO: Mejorar el manejo de errores
            raise typer.Exit(code=1)

        discovery = DiscoveryService(client)
        chunker = ChunkingService(work_dir=settings.worktable)

        if job.payloads.count() == 0:
            chunker.process_job(job)

        expected_volumes = job.payloads.count()
        UI.info(
            f"Estrategia: [bold]Cinta Virtual[/bold] en {expected_volumes} volúmenes."
        )

        report = discovery.investigate(job)
        plan = PolicyExpert.determine_plan(report, policy)
        uploader = UploadService(
            client=client,
            chunk_service=chunker,
            upload_limit_rate_kbps=settings.upload_limit_rate_kbps,
            discovery=discovery,
        )

        if isinstance(plan, SkipPlan):
            UI.info(f"[yellow]Omitido:[/yellow] {plan.reason}")
            if plan.is_already_fulfilled:
                job.set_uploaded()

        elif isinstance(plan, PhysicalUploadPlan):
            uploader.execute_upload_strategy(job)

        elif isinstance(plan, AskUserPlan):
            if typer.confirm("El archivo ya existe en la red. ¿Re-subir forzosamente?"):
                uploader.execute_upload_strategy(job)

        SnapshotService.generate_snapshot(job)
        UI.success("Manifiesto de recuperación generado correctamente.")
