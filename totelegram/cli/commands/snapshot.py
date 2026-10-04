import logging
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional, Tuple

import typer
from rich.table import Table

from totelegram.cli.state import CLIState
from totelegram.cli.ui import UI, DisplaySnapshotPlan, console
from totelegram.packaging.importer import (
    ImportPlan,
    PlanAction,
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
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Aplica el plan en la base de datos sin solicitar confirmación.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Solo muestra el plan de importación sin realizar modificaciones.",
    ),
):
    state: CLIState = ctx.obj
    manager = state.manager
    profile_name = manager.resolve_profile_name(state.profile_name, strict=True)
    assert profile_name is not None
    _ = manager.get_settings(profile_name)

    UI.separator()
    with console.status("[dim]Buscando archivos snapshot...[/]"):
        candidates = SnapshotInspector.find_all_candidates(paths)

    if not candidates:
        UI.warn("No se encontraron archivos snapshot en las rutas proporcionadas.")
        raise typer.Exit(0)

    inspections = [SnapshotInspector.inspect_file(p) for p in candidates]

    # Desempaquetamos tanto client como db
    with state.scope() as (client, db):
        with console.status(
            "[bold green]Analizando disponibilidad en la base de datos...[/]"
        ):
            plan = SnapshotImportPlanner.plan(inspections)

        # Caso Fast-Exit: 100% de snapshots sincronizados
        if (
            not plan.actionable_items
            and not plan.conflicts
            and not plan.needs_upgrade_items
            and not plan.corrupted_items
        ):
            DisplaySnapshotPlan.show_fast_exit(plan.total_scanned)
            raise typer.Exit(0)

        # Renderizar el flujo completo
        DisplaySnapshotPlan.show_balance(plan)
        DisplaySnapshotPlan.show_action_table(plan)
        DisplaySnapshotPlan.show_inconsistencies_tree(plan)
        DisplaySnapshotPlan.show_alerts(plan, sample_path=str(paths[0]))

        # Si no hay nada accionable (solo había advertencias/upgrades)
        if not plan.actionable_items:
            UI.info("No hay acciones aplicables en la base de datos.")
            raise typer.Exit(0)

        # Modo Dry-Run
        if dry_run:
            console.print()
            UI.info("Modo [bold]--dry-run[/] activo. No se aplicaron cambios.")
            raise typer.Exit(0)

        # Confirmación interactiva
        if not yes:
            console.print()
            confirmed = typer.confirm(
                f"¿Deseas aplicar estos cambios e importar {len(plan.actionable_items)} snapshots a la DB?",
                default=True,
            )
            if not confirmed:
                UI.info("Operación cancelada por el usuario.")
                raise typer.Exit(0)

        # Proceder a aplicar el plan (Fase 4 & 5)
        from totelegram.models import TelegramUser
        from totelegram.packaging.importer import SnapshotImporter

        owner = None
        if client:
            try:
                me = client.get_me()
                if me:
                    owner = TelegramUser.get_or_create_from_tg(me)
            except Exception:
                pass

        console.print()
        with console.status(
            "[bold green]Aplicando cambios en la base de datos...[/]"
        ) as status:

            def on_progress(current: int, total: int, filename: str):
                status.update(
                    f"[bold green]Importando ({current}/{total}):[/] {filename}"
                )

            succeeded, errors = SnapshotImporter.apply_plan(
                plan=plan,
                db=db,
                owner=owner,
                on_progress=on_progress,
            )

        # Resumen de ejecución
        UI.separator()
        if succeeded > 0:
            UI.success(
                f"Importación completada: [bold]{succeeded}[/] snapshot(s) aplicados exitosamente a la base de datos."
            )

        if errors:
            UI.error(f"Se produjeron errores en [bold]{len(errors)}[/] snapshot(s):")
            for fname, err in errors:
                console.print(f"  [red]• {fname}:[/] [dim]{err}[/]")
            raise typer.Exit(1)


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

    with console.status("[dim]Buscando archivos snapshot...[/]"):
        candidates = SnapshotInspector.find_all_candidates(paths)

    if not candidates:
        UI.warn("No se encontraron snapshots para migrar.")
        raise typer.Exit(0)

    # Base root para determinar rutas relativas
    base_root = paths[0] if paths[0].is_dir() else paths[0].parent

    UI.separator()
    UI.info(f"Encontrados [bold]{len(candidates)}[/] snapshots para procesar.")
    if output_dir:
        UI.info(
            f"Directorio de salida: [bold cyan]{output_dir}[/] (Estructura relativa activada)"
        )
    else:
        UI.info(
            "Modo in-place: Los nuevos snapshots se guardarán junto a los originales."
        )

    with maybe_telegram_session(state, profile_name) as tg_client:
        if tg_client:
            UI.success(
                f"Sesión de Telegram activa ([bold green]{profile_name}[/]) para enriquecimiento JIT."
            )
        else:
            UI.info("Operando en modo offline (sin sesión de Telegram activa).")

        migrated = 0
        skipped_already_v5 = 0
        error_list: List[Tuple[str, str]] = []

        with console.status("[bold green]Migrando snapshots a v5.0...[/]") as status:
            for idx, file_path in enumerate(candidates, 1):
                status.update(
                    f"[bold green]Migrando ({idx}/{len(candidates)}):[/] {file_path.name}"
                )
                try:
                    raw_data = load_raw_snapshot(file_path)
                    if not isinstance(raw_data, dict):
                        error_list.append(
                            (
                                file_path.name,
                                "El archivo no contiene un objeto/diccionario válido.",
                            )
                        )
                        continue

                    # Si ya es v5.0 nativo, omitir para no re-procesar innecesariamente
                    if raw_data.get("manifest_version") == "5.0":
                        skipped_already_v5 += 1
                        continue

                    v5_dict, transition = SnapshotUpgrader.upgrade_to_dict(
                        raw_data, tg_client=tg_client
                    )

                    source_name = v5_dict["source"]["filename"]
                    out_name = f"{source_name}.json.xz"

                    if output_dir:
                        rel_dir = (
                            file_path.parent.relative_to(base_root)
                            if file_path.is_relative_to(base_root)
                            else Path()
                        )
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
                    error_list.append((file_path.name, str(e)))
                    logger.error(f"Error migrando {file_path.name}: {e}")

    UI.separator()
    UI.success(
        f"Migración finalizada: [bold]{migrated}[/] convertidos a v5.0 exitosamente."
    )
    if skipped_already_v5 > 0:
        UI.info(f"Omitidos [bold]{skipped_already_v5}[/] snapshots que ya eran v5.0.")

    if error_list:
        UI.warn(
            f"Se encontraron [bold]{len(error_list)}[/] errores durante la migración:"
        )
        err_table = Table(show_header=True)
        err_table.add_column("Archivo", style="bold red")
        err_table.add_column("Error")
        for f, err in error_list[:15]:
            err_table.add_row(f, err)
        console.print(err_table)
