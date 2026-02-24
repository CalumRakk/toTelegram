from pathlib import Path
from typing import List

import typer

from totelegram.commands.config import _get_config_tools
from totelegram.commands.views import DisplayUpload
from totelegram.console import UI, console
from totelegram.core.enums import AvailabilityState, DuplicatePolicy
from totelegram.core.plans import AskUserPlan
from totelegram.core.schemas import CLIState, ScanReport
from totelegram.services.uploader import UploadService
from totelegram.store.models import Job
from totelegram.utils import is_excluded


def _scan_and_filter(
    target: Path | List[Path], patterns: list[str], max_filesize_bytes: int
) -> ScanReport:
    """
    Escanea el objetivo y aplica reglas de exclusión.
    Retorna un ScanReport con los archivos válidos y las omisiones categorizadas.
    """
    report = ScanReport()

    def has_snapshot(file_path: Path) -> bool:
        filename_plus_ext = file_path.with_name(f"{file_path.name}.json.xz")
        stem_plus_ext = file_path.with_name(f"{file_path.stem}.json.xz")
        return filename_plus_ext.exists() or stem_plus_ext.exists()

    def process_candidate(p: Path):
        # report.total_scanned += 1
        # if p.name.endswith(".json.xz"):
        #     report.snapshots_found += 1

        # Exclusión por Patrón (user config)
        if is_excluded(p, patterns):
            report.skipped_by_exclusion.append(p)
            return

        # Exclusión por Snapshot
        if has_snapshot(p):
            report.skipped_by_snapshot.append(p)
            return

        # Exclusión por Tamaño
        if p.stat().st_size > max_filesize_bytes:
            report.skipped_by_size.append(p)
            return

        # Si pasa todo, es un archivo válido
        report.found.append(p)

    if isinstance(target, list):
        candidates = target
    elif isinstance(target, Path):
        if target.is_file():
            candidates = [target]
        else:
            candidates = list(target.rglob("*"))
    else:
        raise ValueError(f"Invalid target type: {type(target)}")

    for p in candidates:
        if p.is_file():
            process_candidate(p)

    return report


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
    ctx: typer.Context,
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
    state: CLIState = ctx.obj
    profile_name, service = _get_config_tools(ctx)

    settings = state.manager.get_settings(profile_name)

    exclusion_patterns = settings.all_exclusion_patterns()
    with console.status(f"[dim]Escaneando {target}...[/dim]"):
        scan_report = _scan_and_filter(
            target, exclusion_patterns, settings.max_filesize_bytes
        )

    all_paths = scan_report.found
    if not all_paths:
        if target.is_dir():
            UI.warn("No se encontraron archivos válidos para procesar.")
        raise typer.Exit(0)

    if target.is_dir():
        # Si es una carpeta, muestra lo que encontro.
        DisplayUpload.announces_total_files_found(scan_report)

    # Reporta la exclusion de un archivo o miles.
    verbose = False if scan_report.total_files > 7 else True
    DisplayUpload.show_skip_report(
        scan_report,
        verbose,
    )
    print(f"\nprocessando {len(all_paths)} archivos")

    # _print_upload_summary(all_paths)

    # if len(all_paths) > 7 and not force:
    #     if not typer.confirm(f"\n¿Deseas procesar estos {len(all_paths)} archivos?"):
    #         UI.info("Operación cancelada.")
    #         return

    # if not all_paths:
    #     if target.is_dir():
    #         UI.warn("No se encontraron archivos para procesar.")
    #     raise typer.Exit(0)

    # _print_upload_summary(all_paths)
    # if len(all_paths) > 7 and not force:
    #     if not typer.confirm(f"¿Deseas procesar estos {len(all_paths)} archivos?"):
    #         return

    # console.print(f"\n[bold cyan]Iniciando sesión:[/bold cyan] {profile_name}\n")
    # with DatabaseSession(settings.database_path), TelegramSession(settings) as client:
    #     from pyrogram.types import Chat, User

    #     tg_chat = cast(Chat, client.get_chat(settings.chat_id))
    #     chat_db, _ = TelegramChat.get_or_create_from_tg(tg_chat)
    #     chunker = ChunkingService(work_dir=settings.worktable)
    #     discovery = DiscoveryService(client)
    #     uploader = UploadService(
    #         client=client,
    #         chunk_service=chunker,
    #         upload_limit_rate_kbps=settings.upload_limit_rate_kbps,
    #         max_filename_length=settings.max_filename_length,
    #         discovery=discovery,
    #     )
    #     me = cast(User, client.get_me())

    #     UI.info(
    #         f"Destino: [bold cyan]{tg_chat.title or 'Privado'}[/] [dim](Id: {settings.chat_id})[/dim]"
    #     )
    #     for path in all_paths:
    #         source = SourceFile.get_or_create_from_path(
    #             path, settings.worktable
    #         )  # TODO: avisar
    #         job = Job.get_or_none(Job.source == source, Job.chat == chat_db)

    #         if not job:
    #             job = Job.create_contract(source, chat_db, me.is_premium, settings)
    #             UI.info(f"Estrategia fijada: [bold]{job.strategy.value}[/]")
    #             if job.strategy == Strategy.SINGLE:
    #                 UI.info(f"[bold]Archivo único:[/bold] [blue]{path.name}[/blue]")
    #             else:
    #                 parts_count = discovery._get_expected_count(job)
    #                 UI.info(
    #                     f"[bold]Fragmentando en {parts_count} partes:[/bold] [blue]{path.name}[/blue]"
    #                 )

    #         report = discovery.investigate(job)
    #         plan = PolicyExpert.determine_plan(report, settings.duplicate_policy)
    #         if isinstance(plan, SkipPlan):
    #             UI.info(f"[dim]{plan.reason}[/dim]")
    #             if plan.is_already_fulfilled:
    #                 job.set_uploaded()

    #         elif isinstance(plan, PhysicalUploadPlan):
    #             uploader.execute_physical_upload(job)

    #         elif isinstance(plan, AskUserPlan):
    #             if plan.state == AvailabilityState.REMOTE_RESTRICTED:
    #                 # TODO: ¿Si existe en varios lugares?¿como sé cual es el mejor?
    #                 if typer.confirm("Existe pero no tienes acceso. ¿Subir de nuevo?"):
    #                     uploader.execute_physical_upload(job)
    #             else:
    #                 _handle_redundancy_interaction(job, uploader, plan)

    #         SnapshotService.generate_snapshot(job)


# def _scan_and_filter(
#     target: Path, patterns: list[str], max_filesize_bytes: int
# ) -> list[Path]:
#     """
#     Escanea el objetivo, aplica reglas y genera un reporte de omisiones.
#     """
#     found = []

#     # Listas para categorizar las omisiones
#     skipped_by_snapshot = []
#     skipped_by_size = []
#     skipped_by_exclusion = []

#     stats = {"total": 0, "snapshots": 0}

#     def has_snapshot(file_path: Path) -> bool:
#         filename_plus_ext = file_path.with_name(f"{file_path.name}.json.xz")
#         stem_plus_ext = file_path.with_name(f"{file_path.stem}.json.xz")
#         return filename_plus_ext.exists() or stem_plus_ext.exists()

#     with console.status(f"[dim]Escaneando {target}...[/dim]"):

#         def process_candidate(p: Path):
#             stats["total"] += 1
#             if p.name.endswith(".json.xz"):
#                 stats["snapshots"] += 1

#             # Exclusión por Patrón (user config)
#             if is_excluded(p, patterns):
#                 skipped_by_exclusion.append(p)
#                 return

#             # Exclusión por Snapshot
#             if has_snapshot(p):
#                 skipped_by_snapshot.append(p)
#                 return

#             # Exclusión por Tamaño
#             if p.stat().st_size > max_filesize_bytes:
#                 skipped_by_size.append(p)
#                 return

#             # Si pasa todo, es un archivo válido
#             found.append(p)

#         if target.is_file():
#             process_candidate(target)
#         else:
#             for p in target.rglob("*"):
#                 if p.is_file():
#                     process_candidate(p)

#     # Imprime el escaneo si un directorio
#     if target.is_dir():
#         _print_scan_context(stats["total"], stats["snapshots"])

#     # Imprime el reporte.
#     _print_skip_report(
#         skipped_by_snapshot, skipped_by_size, skipped_by_exclusion, patterns
#     )

#     return found


def _print_upload_summary(paths: list[Path]):
    """TABLA de resumen de lo que se va a procesar (Sin Emojis)."""
    total_size = sum(p.stat().st_size for p in paths)
    total_size_mb = total_size / (1024**2)

    size_str = f"{total_size_mb:.2f} MB"
    if total_size_mb > 1024:
        size_str = f"{total_size_mb/1024:.2f} GB"

    string_files = "Archivos" if len(paths) > 1 else "Archivo"
    string_size = "Peso total" if len(paths) > 1 else "Peso"
    console.print(
        f"[bold cyan]>[/] {string_files}:", f"[bold cyan]{paths[0].name}[/bold cyan]"
    )

    console.print(
        f"[bold cyan]>[/] {string_size}:",
        f"[bold magenta]{size_str}[/bold magenta]",
    )
