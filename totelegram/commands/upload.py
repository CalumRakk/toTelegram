from pathlib import Path
from typing import cast

import typer
from rich.markup import escape

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
        if target.is_dir():
            UI.warn("No se encontraron archivos para procesar.")
        raise typer.Exit(0)

    _print_upload_summary(all_paths)
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
    by_snapshot: list[Path],
    by_size: list[Path],
    by_exclusion: list[Path],
    patterns: list[str],
):
    """
    Muestra un reporte.
    - Pocos archivos (<5): Lista detallada línea por línea.
    - Muchos archivos: Bloques de resumen con ejemplos en una sola línea.
    """
    total_skipped = len(by_snapshot) + len(by_size) + len(by_exclusion)

    if total_skipped == 0:
        return

    # FORMATO DETALLADO (Pocos archivos)
    if total_skipped < 5:
        console.print()
        for p in by_snapshot:
            console.print(f"[yellow]Omitido (Ya tiene Snapshot):[/] {escape(p.name)}")
        for p in by_exclusion:
            console.print(
                f"[dim yellow]Omitido (Excluido por Patron):[/] {escape(p.name)}"
            )
        for p in by_size:
            console.print(f"[red]Omitido (Excede peso maximo):[/] {escape(p.name)}")
        console.print()
        return

    # FORMATO CONSOLIDADO (Muchos archivos)

    console.print(f"\n    Contenido omitido: ({total_skipped})")

    def print_block(label: str, style: str, files: list[Path], current_patterns=None):
        if not files:
            return

        count = len(files)

        # Formateo de patrones (Para quitar el None y la lista cruda)
        pat_str = ""
        if current_patterns:
            clean_list = ", ".join(current_patterns)
            pat_str = f"[dim]({clean_list})[/dim]"

        # Categoría, Cantidad y Patrones (si existen)
        console.print(f"\t[bold {style}]{label}:[/] [bold white]{count}[/] {pat_str}")

        # Mostramos hasta 3 ejemplos
        limit = 3
        for f in files[:limit]:
            console.print(f"\t    [dim]{escape(f.as_posix())}[/dim]", highlight=False)

        # Mostramos el resto. Si el resto es 1, lo mostramos.
        remaining = count - limit
        if remaining > 0:
            if remaining == 1:
                console.print(
                    f"\t    [dim]{escape(files[-1].name)}[/dim]", highlight=False
                )
            else:
                console.print(f"\t    [dim]{remaining} archivos mas...[/dim]")

    print_block("Ya tienen Snapshot", "yellow", by_snapshot)
    print_block("Excluidos por Patron", "yellow", by_exclusion, patterns)
    print_block("Exceden peso maximo", "red", by_size)

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

    stats = {"total": 0, "snapshots": 0}

    def has_snapshot(file_path: Path) -> bool:
        return file_path.with_name(f"{file_path.name}.json.xz").exists()

    with console.status(f"[dim]Escaneando {target}...[/dim]"):

        def process_candidate(p: Path):
            stats["total"] += 1
            if p.name.endswith(".json.xz"):
                stats["snapshots"] += 1

            # Exclusión por Patrón (user config)
            if is_excluded(p, patterns):
                skipped_by_exclusion.append(p)
                return

            # Exclusión por Snapshot
            if has_snapshot(p):
                skipped_by_snapshot.append(p)
                return

            # Exclusión por Tamaño
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

    # Imprime el escaneo si un directorio
    if target.is_dir():
        _print_scan_context(stats["total"], stats["snapshots"])

    # Imprime el reporte.
    _print_skip_report(
        skipped_by_snapshot, skipped_by_size, skipped_by_exclusion, patterns
    )

    return found


def _print_scan_context(total: int, snapshots: int):
    """
    Muestra un resumen rápido de lo encontrado en un directorio
    """
    content_files = total - snapshots

    console.print()

    summary = (
        f"\n[dim]Total archivos encontrado:[/dim] [bold]{total}[/bold] archivos  "
        f"({content_files} contenido, {snapshots} snapshots)"
    )
    console.print(summary)


def _print_upload_summary(paths: list[Path]):
    """TABLA de resumen de lo que se va a procesar (Sin Emojis)."""
    total_size = sum(p.stat().st_size for p in paths)
    total_size_mb = total_size / (1024**2)

    size_str = f"{total_size_mb:.2f} MB"
    if total_size_mb > 1024:
        size_str = f"{total_size_mb/1024:.2f} GB"

    console.print("Preparación de Subida...\n")

    string_files = "Archivos" if len(paths) > 1 else "Archivo"
    string_size = "Peso total" if len(paths) > 1 else "Peso"
    console.print(
        f"[bold cyan]>[/] {string_files}:", f"[bold cyan]{paths[0].name}[/bold cyan]"
    )

    console.print(
        f"[bold cyan]>[/] {string_size}:",
        f"[bold magenta]{size_str}[/bold magenta]",
    )
