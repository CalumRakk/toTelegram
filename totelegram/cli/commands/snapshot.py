import logging
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional, Tuple

import typer
from rich.panel import Panel
from rich.table import Table

from totelegram.cli.state import CLIState
from totelegram.cli.ui import UI, console
from totelegram.models import TelegramUser
from totelegram.packaging.importer import (
    ImportPlan,
    PlanAction,
    SnapshotImporter,
    SnapshotImportPlanner,
    SnapshotInspector,
)

logger = logging.getLogger(__name__)

app = typer.Typer(help="Herramientas para inspeccionar e importar snapshots.")


@contextmanager
def maybe_telegram_session(state: CLIState, profile_name: Optional[str]):
    """Mantiene la sesión de Telegram viva durante toda la migración si hay un perfil disponible."""
    if profile_name:
        try:
            with state.get_telegram_session(profile_name) as client:
                yield client
                return
        except Exception as e:
            UI.warn(
                f"No se pudo iniciar la sesión de Telegram ({e}). Continuando en modo offline."
            )
    yield None


def _format_size(size_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size_bytes) < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0  # type: ignore
    return f"{size_bytes:.1f} PB"


def _display_plan_table(plan: ImportPlan):
    table = Table(title="Plan de Importación de Snapshots (v5.0)", show_header=True)
    table.add_column("Acción", style="bold", no_wrap=True)
    table.add_column("Recurso")
    table.add_column("Tamaño", justify="right")
    table.add_column("Partes", justify="center")
    table.add_column("Chat ID", style="dim")
    table.add_column("Detalle")

    action_styles = {
        PlanAction.CREATE_NEW: "[green]NUEVO[/]",
        PlanAction.CREATE_JOB: "[cyan]RAMIFICAR[/]",
        PlanAction.MERGE_REMOTES: "[yellow]COMPLETAR[/]",
        PlanAction.ALREADY_EXISTS: "[dim]OMITIR[/]",
        PlanAction.CONFLICT: "[bold red]CONFLICTO[/]",
    }

    sorted_items = sorted(
        plan.items,
        key=lambda x: (
            0
            if x.action
            in (PlanAction.CREATE_NEW, PlanAction.CREATE_JOB, PlanAction.MERGE_REMOTES)
            else 1
            if x.action == PlanAction.CONFLICT
            else 2
        ),
    )

    displayed_noop = 0
    max_noop_display = 5

    for item in sorted_items:
        label = action_styles[item.action]
        source = item.manifest.source

        if item.action == PlanAction.ALREADY_EXISTS:
            displayed_noop += 1
            if displayed_noop > max_noop_display:
                continue

        table.add_row(
            label,
            source.filename,
            _format_size(source.size),
            str(item.total_parts),
            str(item.target_chat_id),
            item.reason,
        )

    if displayed_noop > max_noop_display:
        omitted = displayed_noop - max_noop_display
        table.add_row(
            "[dim]OMITIR[/]",
            f"[dim]... y {omitted} snapshots más ya completos e idénticos en DB[/dim]",
            "-",
            "-",
            "-",
            "[dim]Sin cambios requeridos[/dim]",
        )

    console.print(table)


def _display_needs_upgrade_table(needs_upgrade_items):
    table = Table(
        title="Snapshots en Versiones Anteriores (Requieren Upgrade a v5.0)",
        show_header=True,
    )
    table.add_column("Archivo", style="yellow")
    table.add_column("Versión Detectada", justify="center")
    table.add_column("Acción Requerida")

    for u in needs_upgrade_items[:15]:
        table.add_row(
            u.path.name,
            u.detected_version or "desconocida",
            "Ejecutar 'totelegram snapshot upgrade' para convertir a v5.0 (.json.xz)",
        )

    if len(needs_upgrade_items) > 15:
        table.add_row(
            "...", "...", f"... y {len(needs_upgrade_items) - 15} archivos más."
        )

    console.print(table)


@app.command("import")
def import_snapshots(
    ctx: typer.Context,
    paths: List[Path] = typer.Argument(
        ...,
        exists=True,
        help="Rutas a archivos (.json.xz) o carpetas que contengan snapshots v5.0.",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        "-a",
        help="Aplica el plan en la base de datos sin solicitar confirmación.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Solo muestra el plan de importación sin realizar modificaciones.",
    ),
):
    """
    Inspecciona y alimenta la base de datos a partir de snapshots UploadManifest v5.0.
    Si se detectan snapshots de versiones anteriores, se indicará cómo actualizarlos con upgrade.
    """
    state: CLIState = ctx.obj
    manager = state.manager

    profile_name = manager.resolve_profile_name(state.profile_name, strict=True)
    settings = manager.get_settings(profile_name)  # type: ignore

    UI.separator()
    with console.status("[dim]Buscando archivos snapshot...[/]"):
        candidates = SnapshotInspector.find_all_candidates(paths)

    if not candidates:
        UI.warn("No se encontraron archivos snapshot en las rutas proporcionadas.")
        raise typer.Exit(0)

    UI.info(f"Inspeccionando [bold]{len(candidates)}[/] snapshots...")

    inspections = [SnapshotInspector.inspect_file(p) for p in candidates]

    with state.scope() as (_, db):
        with console.status(
            "[bold green]Analizando disponibilidad en la base de datos...[/]"
        ):
            plan = SnapshotImportPlanner.plan(inspections)

            console.print()
            _display_plan_table(plan)

            summary_lines = [
                f"• [bold green]Listos para importar (v5.0):[/] {len(plan.actionable_items)}",
                f"• [dim]Ya registrados (No-Op):[/]        {plan.already_exists_count}",
                f"• [bold red]Conflictos de esquema:[/]        {plan.conflicts_count}",
                f"• [bold yellow]Requieren 'upgrade' a v5.0:[/]  {len(plan.needs_upgrade_items)}",
                f"• [bold red]Archivos corruptos:[/]          {len(plan.corrupted_items)}",
            ]
            console.print(
                Panel("\n".join(summary_lines), title="[bold blue]Resumen del Plan[/]")
            )

            if plan.needs_upgrade_items:
                console.print()
                _display_needs_upgrade_table(plan.needs_upgrade_items)
                first_path = paths[0]
                UI.tip(
                    "Para migrar estos archivos antiguos al formato v5.0 moderno, ejecuta:",
                    commands=[f'totelegram snapshot upgrade "{first_path}"'],
                    spacing="top",
                )

            if dry_run:
                UI.info("Modo [bold]--dry-run[/] activo. No se aplicaron cambios.")
                raise typer.Exit(0)

            if not plan.actionable_items:
                if not plan.needs_upgrade_items:
                    UI.success(
                        "No hay nuevos snapshots para importar. Base de datos al día."
                    )
                raise typer.Exit(0)

            if not apply:
                console.print()
                confirmed = typer.confirm(
                    f"¿Deseas aplicar este plan e importar {len(plan.actionable_items)} snapshots a la DB?",
                    default=True,
                )
                if not confirmed:
                    UI.info("Operación cancelada por el usuario.")
                    raise typer.Exit(0)

            owner_id = settings.telegram_account_id or 1
            owner, _ = TelegramUser.get_or_create(
                id=owner_id,
                defaults={"first_name": profile_name, "is_premium": False},
            )

            with console.status(
                "[bold green]Importando snapshots a la base de datos...[/]"
            ) as status:

                def _prog(cur, tot, name):
                    status.update(f"[bold green]Importando ({cur}/{tot}):[/] {name}")

                succeeded, failed = SnapshotImporter.apply_plan(
                    plan, db, owner, on_progress=_prog
                )

            UI.separator()
            if succeeded > 0:
                UI.success(
                    f"Se importaron [bold]{succeeded}[/] snapshots exitosamente a la base de datos."
                )
            if failed > 0:
                UI.error(f"Ocurrieron errores al importar [bold]{failed}[/] snapshots.")


@app.command("upgrade")
def upgrade_snapshots(
    ctx: typer.Context,
    paths: List[Path] = typer.Argument(
        ...,
        exists=True,
        help="Rutas a archivos o carpetas con snapshots a migrar hacia v5.0.",
    ),
    output_dir: Optional[Path] = typer.Option(
        None,
        "--output-dir",
        "-o",
        help="Directorio destino. Conserva la jerarquía de carpetas relativas.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Simula la migración sin escribir archivos en disco.",
    ),
):
    """
    Migra snapshots antiguos (.yaml, .json, .xz) al estándar moderno UploadManifest v5.0 (.json.xz),
    preservando la estructura exacta de subcarpetas relativas y resolviendo metadatos con Telegram.
    """
    import json
    import lzma

    from totelegram.packaging.upgrader import SnapshotUpgrader, load_raw_snapshot

    state: CLIState = ctx.obj
    manager = state.manager
    profile_name = manager.resolve_profile_name(state.profile_name, strict=False)

    valid_exts = (".json", ".xz", ".yaml", ".yml")
    all_candidates: List[Tuple[Path, Path]] = []  # (file_path, base_root)

    # Recolectar archivos conservando el origen para la ruta relativa
    for target in paths:
        if target.is_file():
            if any(target.name.lower().endswith(ext) for ext in valid_exts):
                all_candidates.append((target, target.parent))
        elif target.is_dir():
            for f in target.rglob("*"):
                if f.is_file() and any(
                    f.name.lower().endswith(ext) for ext in valid_exts
                ):
                    all_candidates.append((f, target))

    if not all_candidates:
        UI.warn("No se encontraron snapshots para migrar.")
        raise typer.Exit(0)

    UI.separator()
    UI.info(f"Encontrados [bold]{len(all_candidates)}[/] snapshots para procesar.")
    if output_dir:
        UI.info(
            f"Directorio de salida: [bold cyan]{output_dir}[/] (Estructura relativa activada)"
        )
    else:
        UI.info(
            "Modo in-place: Los nuevos snapshots se guardarán junto a los originales."
        )

    # Mantener la sesión de Telegram ABIERTA durante todo el procesamiento
    with maybe_telegram_session(state, profile_name) as tg_client:
        if tg_client:
            UI.success(
                f"Sesión de Telegram activa ([bold green]{profile_name}[/]) para enriquecimiento JIT."
            )
        else:
            UI.info("Operando en modo offline (sin sesión de Telegram activa).")

        migrated = 0
        errors = 0

        with console.status("[bold green]Migrando snapshots a v5.0...[/]") as status:
            for idx, (file_path, base_root) in enumerate(all_candidates, 1):
                status.update(
                    f"[bold green]Migrando ({idx}/{len(all_candidates)}):[/] {file_path.name}"
                )
                try:
                    raw_data = load_raw_snapshot(file_path)
                    if not isinstance(raw_data, dict):
                        continue

                    v5_dict, transition = SnapshotUpgrader.upgrade_to_dict(
                        raw_data, tg_client=tg_client
                    )

                    source_name = v5_dict["source"]["filename"]
                    out_name = f"{source_name}.json.xz"

                    # Preservar estructura de directorios relativa
                    if output_dir:
                        rel_dir = file_path.parent.relative_to(base_root)
                        target_folder = output_dir / rel_dir
                    else:
                        target_folder = file_path.parent

                    out_path = target_folder / out_name

                    if not dry_run:
                        target_folder.mkdir(parents=True, exist_ok=True)
                        payload = json.dumps(
                            v5_dict, indent=2, ensure_ascii=False
                        ).encode("utf-8")
                        with lzma.open(out_path, "wb") as f_out:
                            f_out.write(payload)

                    migrated += 1

                except Exception as e:
                    errors += 1
                    logger.error(f"Error migrando {file_path.name}: {e}")

    UI.separator()
    UI.success(
        f"Migración finalizada: [bold]{migrated}[/] convertidos a v5.0 exitosamente."
    )
    if errors > 0:
        UI.warn(f"Se encontraron [bold]{errors}[/] errores durante la migración.")
