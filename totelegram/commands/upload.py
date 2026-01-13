from pathlib import Path
from typing import Optional, cast

import typer
from filelock import FileLock, Timeout

from totelegram.commands.profile import list_profiles
from totelegram.commands.profile_utils import UseOption
from totelegram.console import console
from totelegram.core.enums import JobStatus
from totelegram.core.registry import ProfileManager
from totelegram.core.setting import get_settings
from totelegram.services.chunking import ChunkingService
from totelegram.services.snapshot import SnapshotService
from totelegram.services.uploader import UploadService
from totelegram.store.database import DatabaseSession
from totelegram.store.models import Job, SourceFile
from totelegram.telegram import TelegramSession

pm = ProfileManager()


def upload_file(
    target: Path = typer.Argument(
        ..., exists=True, help="Archivo o directorio a subir"
    ),
    user: Optional[str] = UseOption,
):
    """Sube archivos a Telegram usando la configuración activa."""
    from pyrogram.types import User

    try:
        profile_name = pm.resolve_name(user)
    except ValueError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        list_profiles(quiet=True)
        raise typer.Exit(code=1)

    try:
        env_path = pm.get_path(profile_name)
        lock_path = ProfileManager.PROFILES_DIR / f"{profile_name}.lock"
        lock = FileLock(lock_path, timeout=0)

        with lock:
            settings = get_settings(env_path)
            chunker = ChunkingService(settings)
            paths = list(target.glob("*")) if target.is_dir() else [target]
            if not paths:
                console.print("[yellow]No se encontraron archivos para subir.[/yellow]")
                return []

            console.print(f"Iniciando subida para el perfil: [green]{target}[/green]")
            with DatabaseSession(settings), TelegramSession(settings) as client:
                me = cast(User, client.get_me())
                current_tg_limit = (
                    settings.TG_MAX_SIZE_PREMIUM
                    if me.is_premium
                    else settings.TG_MAX_SIZE_NORMAL
                )
                chat_id = settings.chat_id
                user_id = me.id

                for path in paths:
                    if settings.is_excluded(path):
                        console.print(f"[dim yellow]Excluido:[/dim yellow] {path.name}")
                        continue
                    if path.stat().st_size > settings.max_filesize_bytes:
                        console.print(
                            f"[red]Saltando {path.name}: Excede el límite de seguridad del usuario.[/red]"
                        )
                        continue

                    uploader = UploadService(client, settings)
                    try:
                        source = SourceFile.get_or_create_from_path(path)
                        job = Job.get_or_create_from_source(
                            source, chat_id, current_tg_limit, user_id
                        )
                        if job.status == JobStatus.UPLOADED:
                            console.print(
                                f"Job {job.id} ya completado. Verificando snapshot..."
                            )
                            SnapshotService.generate_snapshot(job)
                            continue

                        payloads = chunker.process_job(job)
                        for payload in payloads:
                            uploader.upload_payload(payload)

                        job.set_uploaded()
                        console.print(f"Job {job.id} finalizado con éxito.")
                        SnapshotService.generate_snapshot(job)

                    except Exception as e:
                        console.print(f"Error procesando ruta {path}: {e}")
                        continue
    except Timeout:
        console.print(
            f"[bold yellow]El perfil '{profile_name}' ya está en uso.[/bold yellow]"
        )
        console.print(
            "Hay otra instancia de toTelegram subiendo archivos con este perfil."
        )
        raise typer.Exit(code=1)
