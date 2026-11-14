import logging

from rich.table import Table
from rich.tree import Tree
from tartape.units import format_size

from totelegram.cli.ui import UI, console, log_table
from totelegram.i18n import _
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
        UI.print(_("[bold blue]BALANCE DEL ESCANEO:[/bold blue]"), indent=False)
        UI.print(
            _("• Total procesados:     [bold]{count}[/] snapshots").format(
                count=plan.total_scanned
            )
        )

        if plan.synced_count > 0:
            UI.print(
                _(
                    "• Ya sincronizados:     [bold green]{count}[/] completos al 100% [dim](sin cambios requeridos)[/]"
                ).format(count=plan.synced_count)
            )

        if actionable_count > 0:
            UI.print(
                _(
                    "• Acciones pendientes:  [bold cyan]{count}[/] snapshots por aplicar"
                ).format(count=actionable_count)
            )
        else:
            UI.print(_("• Acciones pendientes:  [dim]0 snapshots por aplicar[/dim]"))

        if warnings_count > 0:
            UI.print(
                _(
                    "• Con advertencias:     [bold yellow]{warnings}[/] snapshots "
                    "[dim]({conflicts} conflictos, {outdated} desactualizados, {corrupted} corruptos)[/]"
                ).format(
                    warnings=warnings_count,
                    conflicts=conflicts_count,
                    outdated=len(plan.needs_upgrade_items),
                    corrupted=len(plan.corrupted_items),
                )
            )

    @classmethod
    def show_fast_exit(cls, total: int) -> None:
        console.print()
        UI.success(
            _(
                "Todos los snapshots ([bold]{total}[/]) ya se encuentran al 100% sincronizados en la base de datos."
            ).format(total=total)
        )
        UI.info(_("Base de datos al día. No se requirieron cambios."))
        console.print()

    @classmethod
    def show_action_table(cls, plan: ImportPlan) -> None:
        actionables = plan.actionable_items
        if not actionables and not plan.conflicts:
            return

        table_title = _("ACCIONES A REALIZAR EN BASE DE DATOS")
        table = Table(
            title=f"\n{table_title}",
            show_header=True,
            header_style="bold cyan",
            title_style="bold blue",
            title_justify="left",
        )
        table.add_column(_("Acción"), style="bold", no_wrap=True)
        table.add_column(_("Estructura"), justify="center")
        table.add_column(_("Recurso"))
        table.add_column(_("Tamaño"), justify="right")
        table.add_column(_("Chat Destino"), style="dim")
        table.add_column(_("Operaciones en BD"), style="bold magenta")

        action_badges = {
            PlanAction.CREATE_NEW: _("[green]NUEVO[/]"),
            PlanAction.CREATE_JOB: _("[cyan]RAMIFICAR[/]"),
            PlanAction.MERGE_REMOTES: _("[yellow]PARCIAL[/]"),
            PlanAction.CONFLICT: _("[bold red]CONFLICTO[/]"),
        }

        for item in actionables:
            source = item.manifest.source
            total_parts = item.total_parts
            structure_str = (
                _("1 parte")
                if total_parts == 1
                else _("{n} partes").format(n=total_parts)
            )

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
            structure_str = (
                _("1 parte")
                if total_parts == 1
                else _("{n} partes").format(n=total_parts)
            )

            table.add_row(
                action_badges[PlanAction.CONFLICT],
                structure_str,
                source.filename,
                format_size(source.size),
                str(conf.target_chat_id),
                _("[bold red]BLOQUEADO (Inconsistente)[/]"),
            )

        console.print(table)
        log_table(table, table_title)

        legend = _(
            "Leyenda BD: +Source (Origen), +Job (Trabajo), +P (Payloads/Piezas), +Remotes (Mensajes en TG), +Files (Cinta TAR)"
        )
        console.print(f"  [dim]{legend}[/]\n")
        ui_logger.info(f"  {legend}")

    @classmethod
    def show_inconsistencies_tree(cls, plan: ImportPlan) -> None:
        partials = plan.partial_items
        conflicts = plan.conflicts

        if not partials and not conflicts:
            return

        UI.print(
            _("[bold blue]DETALLE DE INCONSISTENCIAS Y DISCREPANCIAS:[/bold blue]"),
            indent=False,
        )
        ui_logger.info("=== DETALLE DE INCONSISTENCIAS Y DISCREPANCIAS ===")

        for item in partials:
            parts_label = (
                _("1 parte")
                if item.total_parts == 1
                else _("{n} partes").format(n=item.total_parts)
            )
            root = Tree(
                f"[cyan]•[/] [bold]{item.manifest.source.filename}[/] "
                f"[dim]({parts_label})[/dim]"
            )
            ui_logger.info(f"• {item.manifest.source.filename} ({parts_label})")

            for p in item.parts_diff:
                part_label = _("Parte {cur}/{tot} (msg: {msg_id})").format(
                    cur=p.sequence + 1, tot=p.total_parts, msg_id=p.message_id
                )
                if p.action == PartAction.ALREADY_EXISTS:
                    msg = _("✓ {part} ya registrada en DB (omitida)").format(
                        part=part_label
                    )
                    root.add(f"[dim]{msg}[/]")
                    ui_logger.info(f"    {msg}")
                elif p.action == PartAction.WILL_LINK:
                    msg = _("+ {part} será vinculada a la DB").format(part=part_label)
                    root.add(f"[green]{msg}[/]")
                    ui_logger.info(f"    {msg}")
                elif p.action == PartAction.CONFLICT:
                    msg = _("✗ {part}: {detail}").format(
                        part=part_label, detail=p.detail
                    )
                    root.add(f"[bold red]{msg}[/]")
                    ui_logger.warning(f"    {msg}")

            if item.mutations.members_count > 0:
                tar_msg = _(
                    "+ {count} archivos de la cinta TAR serán indexados en base de datos"
                ).format(count=item.mutations.members_count)
                root.add(f"[bold cyan]{tar_msg}[/]")
                ui_logger.info(f"    {tar_msg}")

            console.print(root)
            console.print()

        for conf in conflicts:
            short_md5 = (
                conf.manifest.source.md5sum[:12]
                if conf.manifest.source.md5sum
                else _("desconocido")
            )
            root = Tree(
                f"[yellow]•[/] [bold]{conf.manifest.source.filename}[/] [dim](MD5: {short_md5}...)[/]"
            )
            root.add(_("[red]Conflicto:[/] {reason}").format(reason=conf.reason))
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

        UI.print(_("[bold red]ATENCIÓN REQUERIDA:[/bold red]"), indent=False)
        ui_logger.warning("=== ATENCIÓN REQUERIDA ===")

        if plan.ahead_items:
            count = len(plan.ahead_items)
            console.print(
                _(
                    "  [bold red]▲[/] [bold]{count}[/] snapshot(s) generados por una versión más reciente de toTelegram:"
                ).format(count=count)
            )
            for insp in plan.ahead_items[:5]:
                console.print(
                    f"      [bright_black]• {insp.path.name}[/] [dim](v{insp.detected_version})[/]"
                )

            UI.educational_tip(
                message=_(
                    "Tu versión de toTelegram no puede leer estos snapshots porque fueron creados con una versión más nueva.\n"
                    "Actualiza toTelegram para habilitar la compatibilidad."
                ),
                title=_("Actualización requerida"),
                commands=["pip install --upgrade totelegram"],
                spacing="top",
                border_style="red",
            )
            console.print()

        if plan.needs_upgrade_items:
            count = len(plan.needs_upgrade_items)
            console.print(
                _(
                    "  [yellow]![/] [bold]{count}[/] snapshot(s) en formato antiguo no son compatibles con este estándar:"
                ).format(count=count)
            )
            for insp in plan.needs_upgrade_items[:5]:
                ver = insp.detected_version or _("antigua")
                console.print(
                    f"      [bright_black]• {insp.path.name}[/] [dim](versión detectada: {ver})[/]"
                )

            if count > 5:
                console.print(
                    _("      [dim]... y {count} archivos más.[/dim]").format(
                        count=count - 5
                    )
                )

            UI.educational_tip(
                message=_(
                    "Esta versión de toTelegram opera exclusivamente con el estándar v6.0 (.ttape y .json.xz).\n"
                    "Los snapshots de versiones descontinuadas deben regenerarse o volver a subirse."
                ),
                title=_("Snapshots descontinuados"),
                spacing="top",
                border_style="yellow",
            )
            console.print()

        if plan.corrupted_items:
            count = len(plan.corrupted_items)
            console.print(
                _(
                    "  [bold red]X[/] [bold]{count}[/] snapshot(s) corruptos o con formato inválido:"
                ).format(count=count)
            )
            for insp in plan.corrupted_items[:5]:
                err = insp.error_detail or _("Error de parsing")
                console.print(f"      [red]• {insp.path.name}:[/] [dim]{err}[/]")

            if count > 5:
                console.print(
                    _("      [dim]... y {count} archivos más.[/dim]").format(
                        count=count - 5
                    )
                )
            console.print()
