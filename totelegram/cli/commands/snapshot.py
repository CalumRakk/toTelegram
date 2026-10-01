import logging
from pathlib import Path
from typing import List

import typer
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from totelegram.cli.state import CLIState
from totelegram.cli.ui import UI, console
from totelegram.database import DatabaseSession, normalize_database_url
from totelegram.models import TelegramUser
from totelegram.packaging.importer import (
    InspectionStatus,
    PlanAction,
    SnapshotImporter,
    SnapshotImportPlanner,
    SnapshotInspector,
)

logger = logging.getLogger(__name__)

app = typer.Typer(help="Gestión e importación de snapshots de subida (*.json.xz).")


def _format_size(size_bytes: int) -> str:
    """Convierte bytes a un formato legible."""
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def _render_plan_table(plan, show_all: bool = False):
    """Muestra la tabla del plan con estilos y límites para lotes grandes."""
    table = Table(
        title="Plan de Importación de Snapshots", show_header=True, expand=True
    )
    table.add_column("Acción", style="bold", width=14)
    table.add_column("Archivo / Contenido", style="cyan")
    table.add_column("Tamaño", justify="right", width=10)
    table.add_column("Partes", justify="center", width=8)
    table.add_column("Chat ID", style="dim", width=15)
    table.add_column("Detalle", style="bright_black")

    action_styles = {
        PlanAction.CREATE_NEW: ("[green]NUEVO[/]", "green"),
        PlanAction.CREATE_JOB: ("[cyan]RAMIFICAR[/]", "cyan"),
        PlanAction.MERGE_REMOTES: ("[yellow]FUSIONAR[/]", "yellow"),
        PlanAction.ALREADY_EXISTS: ("[dim]OMITIR[/]", "dim"),
        PlanAction.CONFLICT: ("[red]CONFLICTO[/]", "red"),
    }

    # Mostrar primero las acciones que generan cambios
    sorted_items = sorted(
        plan.items,
        key=lambda x: (
            0
            if x.action
            in (PlanAction.CREATE_NEW, PlanAction.CREATE_JOB, PlanAction.MERGE_REMOTES)
            else 1
        ),
    )

    max_display = len(sorted_items) if show_all else 20
    displayed_items = sorted_items[:max_display]

    for item in displayed_items:
        label, _ = action_styles.get(item.action, (item.action.value, "white"))
        filename = item.snapshot_path.name
        size_str = _format_size(item.total_size) if item.total_size > 0 else "--"
        parts_str = str(item.parts_count) if item.parts_count > 0 else "--"
        chat_str = str(item.chat_id) if item.chat_id else "--"

        table.add_row(label, filename, size_str, parts_str, chat_str, item.message)

    console.print(table)

    if not show_all and len(sorted_items) > max_display:
        console.print(
            f"  [dim]... y {len(sorted_items) - max_display} snapshots más idénticos u omitidos.[/]\n"
        )


@app.command("import")
def import_snapshots(
    ctx: typer.Context,
    paths: List[Path] = typer.Argument(
        ...,
        exists=True,
        help="Rutas a archivos .json.xz o carpetas que los contengan.",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        "-a",
        help="Aplica los cambios directamente a la base de datos sin preguntar.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Ejecuta únicamente el análisis y muestra el plan sin modificar la base de datos.",
    ),
    show_unknown: bool = typer.Option(
        False,
        "--show-unknown",
        help="Muestra la lista de snapshots con versiones no reconocidas y sus claves.",
    ),
    show_all: bool = typer.Option(
        False,
        "--show-all",
        help="Muestra todas las filas del plan en lugar de resumir.",
    ),
):
    """
    Inspecciona y alimenta la base de datos a partir de archivos de snapshot (*.json.xz).
    """
    state: CLIState = ctx.obj
    profile_name = state.manager.resolve_profile_name(state.profile_name, strict=True)
    assert profile_name is not None
    settings = state.manager.get_settings(profile_name)

    # 1. Escaneo e Inspección de Archivos (Fase 2)
    with console.status("[bold green]Escaneando y clasificando snapshots..."):
        inspections = SnapshotInspector.scan_paths(paths)

    if not inspections:
        UI.warn(
            "No se encontraron archivos de snapshot (*.json.xz) en las rutas indicadas."
        )
        raise typer.Exit(0)

    # Resumen preliminar de inspección
    valid_inspections = [
        i for i in inspections if i.status == InspectionStatus.CURRENT_VERSION
    ]
    unknown_inspections = [
        i for i in inspections if i.status == InspectionStatus.UNKNOWN_VERSION
    ]
    corrupted_inspections = [
        i for i in inspections if i.status == InspectionStatus.CORRUPTED
    ]

    db_url = normalize_database_url(settings.database_url, state.manager.database_path)

    # 2. Planificación contra la Base de Datos (Fase 3 - Solo Lectura)
    with DatabaseSession(db_url, auto_init_schema=True) as db:
        with console.status(
            "[bold blue]Consultando base de datos y construyendo el PLAN..."
        ):
            plan = SnapshotImportPlanner.build_plan(valid_inspections, db)

        UI.separator()
        _render_plan_table(plan, show_all=show_all)

        # Resumen consolidado del PLAN
        ready_to_import = sum(
            1
            for i in plan.items
            if i.action
            in (PlanAction.CREATE_NEW, PlanAction.CREATE_JOB, PlanAction.MERGE_REMOTES)
        )
        already_present = sum(
            1 for i in plan.items if i.action == PlanAction.ALREADY_EXISTS
        )
        conflicts = sum(1 for i in plan.items if i.action == PlanAction.CONFLICT)

        console.print("[bold]Resumen del Diagnóstico:[/bold]")
        console.print(
            f"  • Listos para importar:       [bold green]{ready_to_import:>6}[/]"
        )
        console.print(
            f"  • Ya registrados (No-Op):     [dim white]{already_present:>6}[/]"
        )
        if conflicts > 0:
            console.print(
                f"  • En conflicto (Omitidos):    [bold red]{conflicts:>6}[/]"
            )
        if unknown_inspections:
            console.print(
                f"  • Versiones desconocidas:     [bold yellow]{len(unknown_inspections):>6}[/] [dim](Omitidos sin riesgo)[/dim]"
            )
        if corrupted_inspections:
            console.print(
                f"  • Archivos corruptos:         [bold red]{len(corrupted_inspections):>6}[/]"
            )
        console.print()

        # Si se solicitó ver detalles de versiones desconocidas
        if show_unknown and unknown_inspections:
            unk_table = Table(title="Snapshots con Versiones Desconocidas", expand=True)
            unk_table.add_column("Archivo", style="yellow")
            unk_table.add_column("Claves Encontradas", style="cyan")
            unk_table.add_column("Detalle Error", style="dim")
            for u in unknown_inspections[:30]:
                keys_str = ", ".join(u.root_keys[:6]) if u.root_keys else "N/A"
                unk_table.add_row(u.path.name, keys_str, u.error_message or "")
            console.print(unk_table)
            if len(unknown_inspections) > 30:
                console.print(f"  [dim]... y {len(unknown_inspections) - 30} más.[/]\n")

        # Salir si es modo dry-run
        if dry_run:
            UI.info(
                "Modo --dry-run activo. No se aplicaron cambios a la base de datos."
            )
            raise typer.Exit(0)

        if ready_to_import == 0:
            UI.info(
                "No hay acciones pendientes por aplicar. La base de datos ya está al día."
            )
            raise typer.Exit(0)

        # Confirmación interactiva si no viene --apply
        if not apply:
            confirmed = typer.confirm(
                f"¿Deseas aplicar este plan e importar {ready_to_import} snapshots a la base de datos?",
                default=True,
            )
            if not confirmed:
                UI.warn("Operación cancelada por el usuario.")
                raise typer.Exit(0)

        # 3. Aplicación Transaccional (Fase 4 - Escritura)
        account_id = settings.telegram_account_id or 1
        owner, _ = TelegramUser.get_or_create(
            id=account_id,
            defaults={
                "first_name": profile_name,
                "username": profile_name,
                "is_premium": False,
            },
        )

        importer = SnapshotImporter(db, default_owner=owner)

        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]Importando:[/] {task.fields[filename]}"),
            BarColumn(bar_width=None),
            "[progress.percentage]{task.percentage:>3.0f}%",
            "•",
            TextColumn("[dim]({task.completed}/{task.total})[/]"),
            TimeRemainingColumn(),
            console=console,
            transient=True,
        )

        task_id = progress.add_task(
            "import", total=ready_to_import, filename="Iniciando..."
        )

        def _on_tick(current: int, total: int, filename: str):
            progress.update(task_id, completed=current, filename=filename)

        with progress:
            report = importer.apply(plan, on_progress=_on_tick)

        UI.separator()
        if report.successful > 0:
            UI.success(
                f"Se importaron [bold]{report.successful}[/] snapshots correctamente a la base de datos."
            )
        if report.failed > 0:
            UI.error(
                f"Ocurrieron errores al procesar [bold]{report.failed}[/] snapshots. Revisa los logs para más detalles."
            )
