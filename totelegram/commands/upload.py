from pathlib import Path
from typing import Optional, cast

import typer
from rich.table import Table

from totelegram.commands.profile import list_profiles
from totelegram.commands.profile_utils import UseOption
from totelegram.console import console
from totelegram.core.enums import AvailabilityState, DuplicatePolicy
from totelegram.core.registry import ProfileManager
from totelegram.core.setting import get_settings
from totelegram.services.discovery import DiscoveryService
from totelegram.services.snapshot import SnapshotService
from totelegram.services.uploader import UploadService
from totelegram.store.database import DatabaseSession
from totelegram.store.models import Job, SourceFile
from totelegram.telegram import TelegramSession
from totelegram.utils import is_excluded

pm = ProfileManager()


def upload_file(
    target: Path = typer.Argument(
        ..., exists=True, help="Archivo o directorio a procesar."
    ),
    user: Optional[str] = UseOption,
    policy: DuplicatePolicy = typer.Option(
        DuplicatePolicy.STRICT,
        "--policy",
        "-p",
        help="Política frente a archivos ya existentes en el ecosistema.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Salta confirmaciones y procesa archivos segun la politica y reclas del programa..",
    ),
):
    """
    Sube archivos o directorios a Telegram.
    Aplica inteligencia colectiva para evitar transferencias redundantes.
    """
    try:
        profile_name = pm.resolve_name(user)
        env_path = pm.get_path(profile_name)
        settings = get_settings(env_path)
    except ValueError as e:
        console.print(f"[bold red]Error de perfil:[/bold red] {e}")
        list_profiles(quiet=True)
        raise typer.Exit(code=1)

    all_paths = _resolver_target_paths(target, settings)
    if not all_paths:
        console.print(
            "[yellow]No se encontraron archivos válidos para procesar.[/yellow]"
        )
        raise typer.Exit(0)

    _print_upload_summary(all_paths, target)
    if len(all_paths) > 7 and not force:
        if not typer.confirm(f"¿Deseas procesar estos {len(all_paths)} archivos?"):
            raise typer.Exit(0)

    console.print(f"\n[bold cyan]Iniciando sesión:[/bold cyan] {profile_name}\n")
    with DatabaseSession(settings), TelegramSession(settings) as client:
        from pyrogram.types import User

        uploader = UploadService(client, settings)
        discovery = DiscoveryService(client)
        me = cast(User, client.get_me())
        tg_limit = (
            settings.TG_MAX_SIZE_PREMIUM
            if me.is_premium
            else settings.TG_MAX_SIZE_NORMAL
        )

        for path in all_paths:
            console.print(f"\n[bold]Procesando:[/bold] [blue]{path.name}[/blue]")

            try:
                source = SourceFile.get_or_create_from_path(path)
                job = Job.get_or_create_from_source(
                    source=source, user_is_premium=me.is_premium, tg_max_size=tg_limit
                )

                state, remotes = discovery.investigate(job)

                if state == AvailabilityState.FULFILLED:
                    console.print("[dim]✔ Ya disponible en el destino.[/dim]")
                    job.set_uploaded()

                elif state in [
                    AvailabilityState.REMOTE_MIRROR,
                    AvailabilityState.REMOTE_PUZZLE,
                ]:
                    if policy == DuplicatePolicy.STRICT:
                        console.print(
                            "[yellow]Omitido (STRICT): ya existe en el ecosistema.[/yellow]"
                        )
                        continue

                    action = "f"  # Default smart forward
                    if policy == DuplicatePolicy.SMART:
                        console.print(
                            f"[blue]💡 Encontrado en otro chat ({len(remotes)} partes).[/blue]"
                        )
                        action = typer.prompt(
                            "¿Acción? [f] Forward / [u] Upload / [s] Skip", default="f"
                        ).lower()

                    if action == "f":
                        uploader.execute_smart_forward(job, remotes)
                    elif action == "u":
                        uploader.execute_physical_upload(job)
                    else:
                        console.print("[dim]Saltado por el usuario.[/dim]")
                elif state == AvailabilityState.REMOTE_RESTRICTED:
                    if policy == DuplicatePolicy.OVERWRITE or typer.confirm(
                        "Existe pero no tienes acceso. ¿Subir de nuevo?"
                    ):
                        uploader.execute_physical_upload(job)
                else:  # SYSTEM_NEW
                    uploader.execute_physical_upload(job)

                SnapshotService.generate_snapshot(job)

            except Exception as e:
                console.print(
                    f"[bold red]✘ Error procesando {path.name}:[/bold red] {e}"
                )
                if not force:
                    if not typer.confirm("¿Deseas continuar con el siguiente archivo?"):
                        break

    console.print(
        f"\n[bold green]✔ Tarea finalizada perfl {profile_name}.[/bold green]\n"
    )


def _resolver_target_paths(target: Path, settings) -> list[Path]:
    """Escanea el objetivo y aplica reglas de exclusión."""
    found = []

    with console.status(f"[dim]Escaneando {target}...[/dim]"):
        if target.is_file():
            if not is_excluded(target, settings):
                found.append(target)
        else:
            for p in target.rglob("*"):
                if p.is_file() and not is_excluded(p, settings):
                    if p.stat().st_size <= settings.max_filesize_bytes:
                        found.append(p)
    return found


def _print_upload_summary(paths: list[Path], target: Path):
    """TABLA de resumen de lo que se va a procesar."""
    total_size = sum(p.stat().st_size for p in paths)

    table = Table(title="Preparación de Subida", show_header=False, box=None)
    table.add_row("📂 Origen:", f"[bold white]{target}[/bold white]")
    table.add_row("📄 Archivos encontrados:", f"[bold cyan]{len(paths)}[/bold cyan]")
    table.add_row(
        "📊 Tamaño total:",
        f"[bold magenta]{total_size / (1024**2):.2f} MB[/bold magenta]",
    )

    console.print(table)
