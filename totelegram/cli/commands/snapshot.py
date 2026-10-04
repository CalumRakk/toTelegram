import logging
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional, Tuple

import typer
from rich.table import Table

from totelegram.cli.state import CLIState
from totelegram.cli.ui import UI, DisplaySnapshotPlan, console
from totelegram.packaging.importer import (
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
    """
    Inspecciona e importa snapshots v5.0 a la base de datos de manera incremental y segura.
    Calcula un plan de ejecución determinista y solo aplica cambios confirmados.
    """
    state: CLIState = ctx.obj
    manager = state.manager
    profile_name = manager.resolve_profile_name(state.profile_name, strict=True)
    assert profile_name is not None
    _ = manager.get_settings(profile_name)

    UI.separator()
    with console.status("[dim]Buscando archivos snapshot...[/]"):
        candidates = SnapshotInspector.find_all_candidates(paths)

    # Caso límite 1: Directorio sin snapshots
    if not candidates:
        UI.warn("No se encontraron archivos snapshot en las rutas proporcionadas.")
        raise typer.Exit(0)

    inspections = [SnapshotInspector.inspect_file(p) for p in candidates]

    with state.scope() as (client, db):  # type: ignore
        with console.status(
            "[bold green]Analizando disponibilidad en la base de datos...[/]"
        ):
            plan = SnapshotImportPlanner.plan(inspections)

        # Caso límite 2: Fast-Exit (100% de snapshots sincronizados al día)
        if (
            not plan.actionable_items
            and not plan.conflicts
            and not plan.needs_upgrade_items
            and not plan.corrupted_items
        ):
            DisplaySnapshotPlan.show_fast_exit(plan.total_scanned)
            raise typer.Exit(0)

        # Renderizado del flujo acordado
        DisplaySnapshotPlan.show_balance(plan)
        DisplaySnapshotPlan.show_action_table(plan)
        DisplaySnapshotPlan.show_inconsistencies_tree(plan)
        DisplaySnapshotPlan.show_alerts(plan, sample_path=str(paths[0]))

        # Caso límite 3: Sin cambios aplicables (solo había corruptos o desactualizados)
        if not plan.actionable_items:
            console.print()
            UI.info("No hay acciones aplicables en la base de datos.")
            raise typer.Exit(0)

        # Caso límite 4: Modo Simulación (--dry-run)
        if dry_run:
            console.print()
            UI.info("Modo [bold]--dry-run[/] activo. No se aplicaron modificaciones.")
            raise typer.Exit(0)

        # Confirmación interactiva
        if not yes:
            console.print()
            conflict_warning = (
                f" [yellow]({len(plan.conflicts)} en conflicto serán ignorados)[/]"
                if plan.conflicts
                else ""
            )
            prompt_msg = (
                f"¿Deseas aplicar estos cambios e importar [bold]{len(plan.actionable_items)}[/] snapshot(s) a la BD?"
                f"{conflict_warning}"
            )
            confirmed = typer.confirm(prompt_msg, default=True)
            if not confirmed:
                UI.info("Operación cancelada por el usuario.")
                raise typer.Exit(0)

        # Ejecución transaccional en Base de Datos
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

        # Balance final de ejecución
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
