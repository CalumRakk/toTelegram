import logging

from rich.table import Table
from rich.tree import Tree
from tartape.units import format_size

from totelegram.cli.ui import UI, console, log_table
from totelegram.packaging.importer import ImportPlan, PartAction, PlanAction

ui_logger = logging.getLogger("totelegram.ui")


class DisplaySnapshotPlan:
    """Presentador visual desacoplado para el plan de importación de snapshots."""

    @classmethod
    def show_balance(cls, plan: ImportPlan) -> None:
        actionable_count = len(plan.actionable_items)
        conflicts_count = len(plan.conflicts)
        warnings_count = (
            conflicts_count
            + len(plan.needs_upgrade_items)
            + len(plan.ahead_items)
            + len(plan.corrupted_items)
        )
        console.print()
        UI.print("[bold blue]BALANCE DEL ESCANEO:[/bold blue]", indent=False)
        UI.print(f"• Total procesados:     [bold]{plan.total_scanned}[/] snapshots")

        if plan.synced_count > 0:
            UI.print(
                f"• Ya sincronizados:     [bold green]{plan.synced_count}[/] completos al 100% [dim](sin cambios requeridos)[/]"
            )

        if actionable_count > 0:
            UI.print(
                f"• Acciones pendientes:  [bold cyan]{actionable_count}[/] snapshots por aplicar"
            )
        else:
            UI.print("• Acciones pendientes:  [dim]0 snapshots por aplicar[/dim]")

        if warnings_count > 0:
            UI.print(
                f"• Con advertencias:     [bold yellow]{warnings_count}[/] snapshots "
                f"[dim]({conflicts_count} conflictos, {len(plan.needs_upgrade_items)} desactualizados, {len(plan.corrupted_items)} corruptos)[/]"
            )

    @classmethod
    def show_fast_exit(cls, total: int) -> None:
        console.print()
        UI.success(
            f"Todos los snapshots ([bold]{total}[/]) ya se encuentran al 100% sincronizados en la base de datos."
        )
        UI.info("Base de datos al día. No se requirieron cambios.")
        console.print()

    @classmethod
    def show_action_table(cls, plan: ImportPlan) -> None:
        actionables = plan.actionable_items
        if not actionables and not plan.conflicts:
            return

        table = Table(
            title="\nACCIONES A REALIZAR EN BASE DE DATOS",
            show_header=True,
            header_style="bold cyan",
            title_style="bold blue",
            title_justify="left",
        )
        table.add_column("Acción", style="bold", no_wrap=True)
        table.add_column("Estructura", justify="center")
        table.add_column("Recurso")
        table.add_column("Tamaño", justify="right")
        table.add_column("Chat Destino", style="dim")
        table.add_column("Operaciones en BD", style="bold magenta")

        action_badges = {
            PlanAction.CREATE_NEW: "[green]NUEVO[/]",
            PlanAction.CREATE_JOB: "[cyan]RAMIFICAR[/]",
            PlanAction.MERGE_REMOTES: "[yellow]PARCIAL[/]",
            PlanAction.CONFLICT: "[bold red]CONFLICTO[/]",
        }

        for item in actionables:
            source = item.manifest.source
            total_parts = item.total_parts
            structure_str = "1 parte" if total_parts == 1 else f"{total_parts} partes"

            table.add_row(
                action_badges.get(item.action, str(item.action.value)),
                structure_str,
                source.filename,
                format_size(source.size),
                str(item.target_chat_id),
                item.mutations.to_badge(),
            )

        for conf in plan.conflicts:
            source = conf.manifest.source
            total_parts = conf.total_parts
            structure_str = "1 parte" if total_parts == 1 else f"{total_parts} partes"

            table.add_row(
                action_badges[PlanAction.CONFLICT],
                structure_str,
                source.filename,
                format_size(source.size),
                str(conf.target_chat_id),
                "[bold red]BLOQUEADO (Inconsistente)[/]",
            )

        console.print(table)
        log_table(table, "ACCIONES A REALIZAR EN BASE DE DATOS")

        legend = "Leyenda BD: +Source (Origen), +Job (Trabajo), +P (Payloads/Piezas), +Remotes (Mensajes en TG), +Files (Cinta TAR)"
        console.print(f"  [dim]{legend}[/]\n")
        ui_logger.info(f"  {legend}")

    @classmethod
    def show_inconsistencies_tree(cls, plan: ImportPlan) -> None:
        partials = plan.partial_items
        conflicts = plan.conflicts

        if not partials and not conflicts:
            return

        UI.print(
            "[bold blue]DETALLE DE INCONSISTENCIAS Y DISCREPANCIAS:[/bold blue]",
            indent=False,
        )
        ui_logger.info("=== DETALLE DE INCONSISTENCIAS Y DISCREPANCIAS ===")

        for item in partials:
            parts_label = (
                "1 parte" if item.total_parts == 1 else f"{item.total_parts} partes"
            )
            root = Tree(
                f"[cyan]•[/] [bold]{item.manifest.source.filename}[/] "
                f"[dim]({parts_label})[/dim]"
            )
            ui_logger.info(f"• {item.manifest.source.filename} ({parts_label})")

            for p in item.parts_diff:
                part_label = (
                    f"Parte {p.sequence + 1}/{p.total_parts} (msg: {p.message_id})"
                )
                if p.action == PartAction.ALREADY_EXISTS:
                    msg = f"✓ {part_label} ya registrada en DB (omitida)"
                    root.add(f"[dim]{msg}[/]")
                    ui_logger.info(f"    {msg}")
                elif p.action == PartAction.WILL_LINK:
                    msg = f"+ {part_label} será vinculada a la DB"
                    root.add(f"[green]{msg}[/]")
                    ui_logger.info(f"    {msg}")
                elif p.action == PartAction.CONFLICT:
                    msg = f"✗ {part_label}: {p.detail}"
                    root.add(f"[bold red]{msg}[/]")
                    ui_logger.warning(f"    {msg}")

            if item.mutations.members_count > 0:
                tar_msg = f"+ {item.mutations.members_count} archivos de la cinta TAR serán indexados en base de datos"
                root.add(f"[bold cyan]{tar_msg}[/]")
                ui_logger.info(f"    {tar_msg}")

            console.print(root)
            console.print()

        for conf in conflicts:
            short_md5 = (
                conf.manifest.source.md5sum[:12]
                if conf.manifest.source.md5sum
                else "desconocido"
            )
            root = Tree(
                f"[yellow]•[/] [bold]{conf.manifest.source.filename}[/] [dim](MD5: {short_md5}...)[/]"
            )
            root.add(f"[red]Conflicto:[/] {conf.reason}")
            console.print(root)
            console.print()
            ui_logger.warning(
                f"• CONFLICTO: {conf.manifest.source.filename} (MD5: {short_md5}...): {conf.reason}"
            )

    @classmethod
    def show_alerts(cls, plan: ImportPlan, sample_path: str | None = None) -> None:
        if (
            not plan.needs_upgrade_items
            and not plan.ahead_items
            and not plan.corrupted_items
        ):
            return

        UI.print("[bold red]ATENCIÓN REQUERIDA:[/bold red]", indent=False)
        ui_logger.warning("=== ATENCIÓN REQUERIDA ===")

        if plan.ahead_items:
            count = len(plan.ahead_items)
            console.print(
                f"  [bold red]▲[/] [bold]{count}[/] snapshot(s) generados por una versión más reciente de toTelegram:"
            )
            for insp in plan.ahead_items[:5]:
                console.print(
                    f"      [bright_black]• {insp.path.name}[/] [dim](v{insp.detected_version})[/]"
                )

            UI.educational_tip(
                message="Tu versión de toTelegram no puede leer estos snapshots porque fueron creados con una versión más nueva.\n"
                "Actualiza toTelegram para habilitar la compatibilidad.",
                title="Actualización requerida",
                commands=["pip install --upgrade totelegram"],
                spacing="top",
                border_style="red",
            )
            console.print()

        if plan.needs_upgrade_items:
            count = len(plan.needs_upgrade_items)
            console.print(
                f"  [yellow]![/] [bold]{count}[/] snapshot(s) en formato antiguo no son compatibles con este estándar:"
            )
            for insp in plan.needs_upgrade_items[:5]:
                ver = insp.detected_version or "antigua"
                console.print(
                    f"      [bright_black]• {insp.path.name}[/] [dim](versión detectada: {ver})[/]"
                )

            if count > 5:
                console.print(f"      [dim]... y {count - 5} archivos más.[/dim]")

            UI.educational_tip(
                message="Esta versión de toTelegram opera exclusivamente con el estándar v6.0 (.ttape y .json.xz).\n"
                "Los snapshots de versiones descontinuadas deben regenerarse o volver a subirse.",
                title="Snapshots descontinuados",
                spacing="top",
                border_style="yellow",
            )
            console.print()

        if plan.corrupted_items:
            count = len(plan.corrupted_items)
            console.print(
                f"  [bold red]X[/] [bold]{count}[/] snapshot(s) corruptos o con formato inválido:"
            )
            for insp in plan.corrupted_items[:5]:
                err = insp.error_detail or "Error de parsing"
                console.print(f"      [red]• {insp.path.name}:[/] [dim]{err}[/]")

            if count > 5:
                console.print(f"      [dim]... y {count - 5} archivos más.[/dim]")
            console.print()
