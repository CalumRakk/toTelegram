from pathlib import Path
from typing import Optional, cast

import typer
from filelock import FileLock, Timeout
from rich.table import Table

from totelegram.commands.profile import list_profiles
from totelegram.commands.profile_utils import UseOption
from totelegram.console import console
from totelegram.core.enums import JobStatus
from totelegram.core.registry import ProfileManager
from totelegram.core.setting import Settings, get_settings
from totelegram.services.chunking import ChunkingService
from totelegram.services.snapshot import SnapshotService
from totelegram.services.uploader import UploadService
from totelegram.store.database import DatabaseSession
from totelegram.store.models import Job, SourceFile
from totelegram.telegram import TelegramSession

pm = ProfileManager()


def resolver_target_path(target: Path) -> list[Path]:
    all_files = []
    if target.is_dir():
        console.print(f"🔍 Escaneando archivos en [bold cyan]{target}[/bold cyan]...")
        try:
            for p in target.rglob("*"):
                try:
                    if p.is_file():
                        all_files.append(p)
                except (PermissionError, OSError) as e:
                    console.print(
                        f"[yellow]⚠ Saltando ruta por permisos: {p} ({e})[/yellow]"
                    )

        except Exception as e:
            console.print(
                f"[bold red]Error crítico escaneando directorio: {e}[/bold red]"
            )
            raise typer.Exit(1)
    else:
        all_files = [target]

    if not all_files:
        console.print(f"[yellow]⚠ No se encontraron archivos en {target}[/yellow]")
        raise typer.Exit(0)

    return all_files


def summary_upload(all_files: list[Path], settings: Settings):
    to_upload = []
    excluded_count = 0
    total_size = 0

    with console.status("[bold green]Analizando archivos...[/bold green]"):
        for p in all_files:
            if settings.is_excluded(p):
                excluded_count += 1
                continue

            f_size = p.stat().st_size
            if f_size == 0 or f_size > settings.max_filesize_bytes:
                excluded_count += 1
                continue

            to_upload.append(p)
            total_size += f_size
    return {
        "to_upload": to_upload,
        "excluded_count": excluded_count,
        "total_size": total_size,
    }


def ui_print_summary(
    all_files: list[Path], to_upload: list[Path], excluded_count: int, total_size: int
):

    summary = Table(title="Resumen de Operación", show_header=False, box=None)
    summary.add_row("📂 Total encontrados", f"{len(all_files)}")
    summary.add_row(
        "✅ Listos para subir", f"[bold green]{len(to_upload)} archivos[/bold green]"
    )
    summary.add_row("🚫 Excluidos/Omitidos", f"[yellow]{excluded_count}[/yellow]")
    summary.add_row(
        "📊 Tamaño total", f"[bold blue]{total_size / (1024*1024):.2f} MB[/bold blue]"
    )

    console.print(summary)
    console.print("")


def upload_file(
    target: Path = typer.Argument(
        ..., exists=True, help="Archivo o directorio a subir"
    ),
    user: Optional[str] = UseOption,
    force: bool = typer.Option(
        False, "--force", "-f", help="Forzar subida sin confirmación"
    ),
):
    """Sube archivos a Telegram usando la configuración activa."""

    paths = resolver_target_path(target)

    # Resuelve el perfil
    try:
        profile_name = pm.resolve_name(user)
    except ValueError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        list_profiles(quiet=True)
        raise typer.Exit(code=1)

    summary = summary_upload(paths, get_settings(pm.get_path(profile_name)))
    ui_print_summary(all_files=paths, **summary)

    if len(summary["to_upload"]) > 20 and not force:
        if not typer.confirm("¿Deseas continuar con la subida de estos archivos?"):
            console.print("Operación cancelada por el usuario.")
            raise typer.Exit(code=0)

    console.print(
        f"Iniciando subida de [bold green]{len(summary['to_upload'])}[/bold green] archivos al perfil [bold cyan]{profile_name}[/bold cyan]..."
    )

    from pyrogram.types import User

    try:
        env_path = pm.get_path(profile_name)
        lock_path = ProfileManager.PROFILES_DIR / f"{profile_name}.lock"
        lock = FileLock(lock_path, timeout=0)

        with lock:
            settings = get_settings(env_path)
            chunker = ChunkingService(settings)
            with DatabaseSession(settings), TelegramSession(settings) as client:
                me = cast(User, client.get_me())
                current_tg_limit = (
                    settings.TG_MAX_SIZE_PREMIUM
                    if me.is_premium
                    else settings.TG_MAX_SIZE_NORMAL
                )
                chat_id = settings.chat_id
                user_id = me.id

                for path in summary["to_upload"]:
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
