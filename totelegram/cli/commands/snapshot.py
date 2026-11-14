import logging
from pathlib import Path

import typer

from totelegram.cli.state import CLIState
from totelegram.cli.ui import UI, console
from totelegram.cli.views.snapshots import DisplaySnapshotPlan
from totelegram.i18n import _
from totelegram.packaging.importer import (
    SnapshotImportPlanner,
    SnapshotInspector,
)

logger = logging.getLogger(__name__)

app = typer.Typer(help=_("Herramientas para inspeccionar e importar snapshots v6.0."))


@app.command(
    "import",
    help=_(
        "Inspecciona e importa snapshots v6.0 a la base de datos de manera incremental y segura."
    ),
)
def import_snapshots(
    ctx: typer.Context,
    paths: list[Path] = typer.Argument(
        ...,
        exists=True,
        help=_(
            "Rutas a archivos (.json.xz o .ttape) o carpetas que contengan snapshots v6.0."
        ),
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help=_("Aplica el plan en la base de datos sin solicitar confirmación."),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help=_("Solo muestra el plan de importación sin realizar modificaciones."),
    ),
):
    state: CLIState = ctx.obj

    UI.separator()
    with console.status(_("[dim]Buscando archivos snapshot v6.0...[/]")):
        candidates = SnapshotInspector.find_all_candidates(paths)

    if not candidates:
        UI.warn(
            _(
                "No se encontraron archivos snapshot v6.0 (.ttape o .json.xz) en las rutas proporcionadas."
            )
        )
        raise typer.Exit(0)

    inspections = [SnapshotInspector.inspect_file(p) for p in candidates]

    with state.scope() as (client, db):  # type: ignore
        with console.status(
            _("[bold green]Analizando disponibilidad en la base de datos...[/]")
        ):
            plan = SnapshotImportPlanner.plan(inspections)

        # Fast-Exit (100% de snapshots sincronizados al día)
        if (
            not plan.actionable_items
            and not plan.conflicts
            and not plan.needs_upgrade_items
            and not plan.corrupted_items
        ):
            DisplaySnapshotPlan.show_fast_exit(plan.total_scanned)
            raise typer.Exit(0)

        # Renderizado de balance, acciones e inconsistencias
        DisplaySnapshotPlan.show_balance(plan)
        DisplaySnapshotPlan.show_action_table(plan)
        DisplaySnapshotPlan.show_inconsistencies_tree(plan)
        DisplaySnapshotPlan.show_alerts(plan, sample_path=str(paths[0]))

        if not plan.actionable_items:
            console.print()
            UI.info(_("No hay acciones aplicables en la base de datos."))
            raise typer.Exit(0)

        if dry_run:
            console.print()
            UI.info(
                _("Modo [bold]--dry-run[/] activo. No se aplicaron modificaciones.")
            )
            raise typer.Exit(0)

        if not yes:
            console.print()
            conflict_warning = (
                _(" [yellow]({count} en conflicto serán ignorados)[/]").format(
                    count=len(plan.conflicts)
                )
                if plan.conflicts
                else ""
            )
            prompt_msg = _(
                "¿Deseas aplicar estos cambios e importar [bold]{count}[/] snapshot(s) a la BD?{conflicts}"
            ).format(count=len(plan.actionable_items), conflicts=conflict_warning)
            confirmed = typer.confirm(prompt_msg, default=True)
            if not confirmed:
                UI.info(_("Operación cancelada por el usuario."))
                raise typer.Exit(0)

        from totelegram.models import TelegramUser
        from totelegram.packaging.importer import SnapshotImporter

        owner = None
        if client:
            try:
                me = client.get_me()
                if me:
                    owner = TelegramUser.get_or_create_from_tg(me)
            except Exception:  # noqa: S110
                pass

        console.print()
        with console.status(
            _("[bold green]Aplicando cambios en la base de datos...[/]")
        ) as status:

            def on_progress(current: int, total: int, filename: str):
                status.update(
                    _("[bold green]Importando ({current}/{total}):[/] {name}").format(
                        current=current, total=total, name=filename
                    )
                )

            succeeded, errors = SnapshotImporter.apply_plan(
                plan=plan,
                db=db,
                owner=owner,
                on_progress=on_progress,
                client=client,
            )

        UI.separator()
        if succeeded > 0:
            UI.success(
                _(
                    "Importación completada: [bold]{count}[/] snapshot(s) aplicados exitosamente a la base de datos."
                ).format(count=succeeded)
            )

        if errors:
            UI.error(
                _("Se produjeron errores en [bold]{count}[/] snapshot(s):").format(
                    count=len(errors)
                )
            )
            for fname, err in errors:
                console.print(f"  [red]• {fname}:[/] [dim]{err}[/]")
            raise typer.Exit(1)
