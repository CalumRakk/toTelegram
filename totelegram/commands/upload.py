from pathlib import Path
from typing import cast

import typer
from rich import box
from rich.markup import escape
from rich.table import Table

from totelegram.commands.profile import list_profiles
from totelegram.console import UI, console
from totelegram.core.enums import AvailabilityState, DuplicatePolicy, Strategy
from totelegram.core.plans import AskUserPlan, PhysicalUploadPlan, SkipPlan
from totelegram.core.registry import ProfileManager
from totelegram.core.setting import get_settings
from totelegram.services.chunking import ChunkingService
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

    exclusion_patterns = settings.exclude_files_default + settings.exclude_files
    all_paths = _resolver_target_paths(
        target, exclusion_patterns, settings.max_filesize_bytes
    )
    if not all_paths:
        UI.warn("No se encontraron archivos válidos para procesar.")
        raise typer.Exit(0)

    _print_upload_summary(all_paths, target)
    if len(all_paths) > 7 and not force:
        if not typer.confirm(f"¿Deseas procesar estos {len(all_paths)} archivos?"):
            raise typer.Exit(0)

    console.print(f"\n[bold cyan]Iniciando sesión:[/bold cyan] {profile_name}\n")
    with DatabaseSession(settings.database_path), TelegramSession(settings) as client:
        from pyrogram.types import Chat, User

        tg_chat = cast(Chat, client.get_chat(settings.chat_id))
        chat_db, _ = TelegramChat.get_or_create_from_tg(tg_chat)
        chunker = ChunkingService(
            work_dir=settings.worktable, chunk_size=settings.max_filesize_bytes
        )
        discovery = DiscoveryService(client)
        uploader = UploadService(
            client=client,
            chunk_service=chunker,
            upload_limit_rate_kbps=settings.upload_limit_rate_kbps,
            max_filename_length=settings.max_filename_length,
            discovery=discovery,
        )
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


def _print_skip_report(
    by_snapshot: list[Path], by_size: list[Path], by_exclusion: list[Path]
):
    """
    Muestra un reporte inteligente:
    - Si son pocos archivos (<5): Lista detallada línea por línea.
    - Si son muchos: Tabla resumen agrupada.
    """
    total_skipped = len(by_snapshot) + len(by_size) + len(by_exclusion)

    if total_skipped == 0:
        return

    # MODALIDAD DETALLADA (Pocos archivos)
    if total_skipped < 5:
        console.print()
        if by_snapshot:
            for p in by_snapshot:
                console.print(f"[yellow]Omitido (Snapshot):[/yellow] {escape(p.name)}")

        if by_exclusion:
            for p in by_exclusion:
                console.print(
                    f"[dim yellow]Omitido (Exclusión):[/dim yellow] {escape(p.name)}"
                )

        if by_size:
            for p in by_size:
                console.print(f"[red]Omitido (Tamaño):[/red] {escape(p.name)}")
        console.print()
        return

    # MODALIDAD RESUMEN (Muchos archivos - Tabla)

    table = Table(
        title=f"Resumen: {total_skipped} archivos omitidos",
        box=box.SIMPLE,
        show_header=True,
        header_style="bold magenta",
        expand=True,
    )

    table.add_column("Motivo", style="cyan", no_wrap=True)
    table.add_column("Cant.", justify="right", style="bold white", width=6)
    table.add_column("Ejemplos", style="dim")

    def format_examples(files: list[Path]) -> str:
        limit = 3
        names = [escape(f.name) for f in files[:limit]]
        remaining = len(files) - limit
        text = ", ".join(names)
        if remaining > 0:
            text += f", ... y {remaining} más"
        return text

    if by_snapshot:
        table.add_row(
            "Snapshot Existente", str(len(by_snapshot)), format_examples(by_snapshot)
        )

    if by_exclusion:
        table.add_row(
            "Patrón de Exclusión", str(len(by_exclusion)), format_examples(by_exclusion)
        )

    if by_size:
        table.add_row("Excede Tamaño Máx.", str(len(by_size)), format_examples(by_size))

    console.print()
    console.print(table)
    console.print()


def _resolver_target_paths(
    target: Path, patterns: list[str], max_filesize_bytes: int
) -> list[Path]:
    """
    Escanea el objetivo, aplica reglas y genera un reporte de omisiones.
    """
    found = []

    # Listas para categorizar las omisiones
    skipped_by_snapshot = []
    skipped_by_size = []
    skipped_by_exclusion = []

    def has_snapshot(file_path: Path) -> bool:
        return file_path.with_name(f"{file_path.name}.json.xz").exists()

    with console.status(f"[dim]Escaneando {target}...[/dim]"):

        def process_candidate(p: Path):
            # 1. Exclusión por Patrón (user config)
            if is_excluded(p, patterns):
                skipped_by_exclusion.append(p)
                return

            # 2. Exclusión por Snapshot (ya procesado)
            if has_snapshot(p):
                skipped_by_snapshot.append(p)
                return

            # 3. Exclusión por Tamaño (límite duro)
            # Nota: Si max_filesize_bytes actúa como filtro estricto
            if p.stat().st_size > max_filesize_bytes:
                skipped_by_size.append(p)
                return

            # Si pasa todo, es un archivo válido
            found.append(p)

        if target.is_file():
            process_candidate(target)
        else:
            for p in target.rglob("*"):
                if p.is_file():
                    process_candidate(p)

    # Generar el reporte visual antes de devolver los resultados
    _print_skip_report(skipped_by_snapshot, skipped_by_size, skipped_by_exclusion)

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
