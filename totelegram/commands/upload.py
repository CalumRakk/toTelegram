from pathlib import Path
from typing import Optional

import typer
from filelock import FileLock, Timeout

from totelegram.console import console
from totelegram.core.enums import JobStatus
from totelegram.core.profiles import PROFILES_DIR, ProfileManager
from totelegram.core.setting import get_settings
from totelegram.services.chunking import ChunkingService
from totelegram.services.snapshot import SnapshotService
from totelegram.services.uploader import UploadService
from totelegram.store.database import db_proxy, init_database
from totelegram.store.models import Job, SourceFile
from totelegram.telegram import telegram_client_context

app = typer.Typer(
    help="Sube archivos a Telegram usando la configuración activa.",
    add_completion=False,
    no_args_is_help=True,
)
pm = ProfileManager()


@app.command("upload")
def upload_file(
    target: Path = typer.Argument(
        ..., exists=True, help="Archivo o directorio a subir"
    ),
    profile_name: Optional[str] = typer.Option(
        None, "--profile", "-p", help="Usar un perfil específico temporalmente"
    ),
):
    """Sube archivos a Telegram usando la configuración activa."""

    try:
        if profile_name:
            try:
                if not pm.exists_profile(profile_name):
                    console.print(
                        f"[bold red]El perfil '{profile_name}' no existe.[/bold red]"
                    )
                    raise typer.Exit(code=1)
                env_path = pm.get_profile_path(profile_name)
            except ValueError:
                console.print(
                    f"[bold red]El perfil '{profile_name}' no existe.[/bold red]"
                )
                raise typer.Exit(code=1)
        else:
            try:
                profile_name = pm.get_name_active_profile()
                env_path = pm.get_profile_path(profile_name)
            except ValueError:
                console.print("[bold red]No hay perfil activo.[/bold red]")
                console.print("Ejecuta 'totelegram profile create' primero.")
                raise typer.Exit(code=1)

        console.print(f"[blue]Usando perfil: {profile_name}[/blue]")
        lock_path = PROFILES_DIR / f"{profile_name}.lock"
        lock = FileLock(lock_path, timeout=0)
        with lock:
            settings = get_settings(env_path)
            console.print(
                f"Iniciando subida usando configuración de: [bold]{settings.chat_id}[/bold]"
            )
            try:
                init_database(settings)
                chunker = ChunkingService(settings)
                paths = list(target.glob("*")) if target.is_dir() else [target]
                if not paths:
                    console.print(
                        "[yellow]No se encontraron archivos para subir.[/yellow]"
                    )
                    return []

                with telegram_client_context(settings) as client:
                    for path in paths:
                        if settings.is_excluded(path):
                            continue
                        uploader = UploadService(client, settings)
                        try:
                            source = SourceFile.get_or_create_from_path(path)
                            job = Job.get_or_create_from_source(source, settings)
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
            finally:
                if not db_proxy.is_closed():
                    db_proxy.close()
    except Timeout:
        console.print(
            f"[bold yellow]El perfil '{profile_name}' ya está en uso.[/bold yellow]"
        )
        console.print(
            "Hay otra instancia de toTelegram subiendo archivos con este perfil."
        )
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[bold red]Fallo crítico:[/bold red] {e}")
        raise typer.Exit(code=1)
