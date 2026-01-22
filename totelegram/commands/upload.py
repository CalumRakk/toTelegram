from pathlib import Path
from typing import cast

import typer
from rich.table import Table

from totelegram.commands.profile import list_profiles
from totelegram.console import UI, console
from totelegram.core.enums import (
    AvailabilityState,
    DuplicatePolicy,
    Strategy,
)
from totelegram.core.plans import AskUserPlan, PhysicalUploadPlan, SkipPlan
from totelegram.core.registry import ProfileManager
from totelegram.core.setting import get_settings
from totelegram.services.discovery import DiscoveryService
from totelegram.services.policy import PolicyExpert
from totelegram.services.snapshot import SnapshotService
from totelegram.services.uploader import UploadService
from totelegram.store.database import DatabaseSession
from totelegram.store.models import Job, SourceFile, TelegramChat
from totelegram.telegram import TelegramSession
from totelegram.utils import is_excluded

pm = ProfileManager()


def _handle_redundancy_interaction(
    job: Job, uploader: UploadService, plan: AskUserPlan
):
    source_chats = {r.chat_id for r in plan.remotes}

    if plan.state == AvailabilityState.REMOTE_PUZZLE:
        console.print(
            f"[bold cyan]Puzzle Detectado:[/bold cyan] Piezas encontradas en {len(source_chats)} chats diferentes."
        )
        for chat_id in source_chats:
            parts_here = [r for r in plan.remotes if r.chat_id == chat_id]
            console.print(f"  - Chat [dim]{chat_id}[/dim]: {len(parts_here)} partes.")
    else:
        console.print(
            f"[bold green]Espejo Detectado:[/bold green] Archivo íntegro en el chat {list(source_chats)[0]}."
        )

    action = typer.prompt(
        "¿Acción? [f] Forward (Unificar/Clonar) / [u] Upload / [s] Skip", default="f"
    ).lower()

    if action == "f":
        uploader.execute_smart_forward(job, plan.remotes)
    elif action == "u":
        uploader.execute_physical_upload(job)
    else:
        console.print("[dim]Saltado por el usuario.[/dim]")


def upload_file(
    target: Path = typer.Argument(
        ..., exists=True, help="Archivo o directorio a procesar."
    ),
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
    Sube archivos o archivos de un directorio a Telegram.
    """
    try:
        profile_name = pm.resolve_name()
        env_path = pm.get_path(profile_name)
        settings = get_settings(env_path)
    except ValueError as e:
        UI.error(f"Error de perfil: {e}")
        list_profiles(quiet=True)
        raise typer.Exit(code=1)

    all_paths = _resolver_target_paths(target, settings)
    if not all_paths:
        UI.warn("No se encontraron archivos válidos para procesar.")
        raise typer.Exit(0)

    _print_upload_summary(all_paths, target)
    if len(all_paths) > 7 and not force:
        if not typer.confirm(f"¿Deseas procesar estos {len(all_paths)} archivos?"):
            raise typer.Exit(0)

    console.print(f"\n[bold cyan]Iniciando sesión:[/bold cyan] {profile_name}\n")
    with DatabaseSession(settings), TelegramSession(settings) as client:
        from pyrogram.types import Chat, User

        tg_chat = cast(Chat, client.get_chat(settings.chat_id))
        chat_db, _ = TelegramChat.get_or_create_from_tg(tg_chat)

        uploader = UploadService(client, settings)
        discovery = DiscoveryService(client)
        me = cast(User, client.get_me())

        UI.info(
            f"Destino: [bold cyan]{tg_chat.title or 'Privado'}[/] [dim]({settings.chat_id})[/dim]"
        )
        for path in all_paths:
            source = SourceFile.get_or_create_from_path(path)
            job = Job.get_or_none(Job.source == source, Job.chat == chat_db)

            if not job:
                job = Job.create_contract(source, chat_db, me.is_premium, settings)
                UI.info(f"Estrategia fijada: [bold]{job.strategy.value}[/]")
                if job.strategy == Strategy.SINGLE:
                    UI.info(f"\n[bold]Archivo único:[/bold] [blue]{path.name}[/blue]")
                else:
                    parts_count = discovery._get_expected_count(job)
                    UI.info(
                        f"\n[bold]Fragmentando en {parts_count} partes:[/bold] [blue]{path.name}[/blue]"
                    )

            report = discovery.investigate(job)
            plan = PolicyExpert.determine_plan(report, settings.duplicate_policy)
            if isinstance(plan, SkipPlan):
                UI.info(f"[dim] {plan.reason}[/dim]")
                if plan.is_already_fulfilled:
                    job.set_uploaded()

            elif isinstance(plan, PhysicalUploadPlan):
                UI.info(f"[bold] {plan.reason}[/bold]")
                uploader.execute_physical_upload(job)

            elif isinstance(plan, AskUserPlan):
                if plan.state == AvailabilityState.REMOTE_RESTRICTED:
                    if typer.confirm("Existe pero no tienes acceso. ¿Subir de nuevo?"):
                        uploader.execute_physical_upload(job)
                else:
                    _handle_redundancy_interaction(job, uploader, plan)

            SnapshotService.generate_snapshot(job)

    UI.success(f"Tarea finalizada perfil [bold]{profile_name}[/].")


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
    """TABLA de resumen de lo que se va a procesar (Sin Emojis)."""
    total_size = sum(p.stat().st_size for p in paths)
    total_size_mb = total_size / (1024**2)

    size_str = f"{total_size_mb:.2f} MB"
    if total_size_mb > 1024:
        size_str = f"{total_size_mb/1024:.2f} GB"

    table = Table(title="Preparación de Subida", show_header=False, box=None)

    table.add_row("[bold cyan]>[/] Origen:", f"[bold white]{target}[/bold white]")
    table.add_row("[bold cyan]>[/] Archivos:", f"[bold cyan]{len(paths)}[/bold cyan]")
    table.add_row(
        "[bold cyan]>[/] Peso total:",
        f"[bold magenta]{size_str}[/bold magenta]",
    )

    console.print(table)
