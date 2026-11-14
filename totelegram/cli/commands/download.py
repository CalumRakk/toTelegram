import logging
from pathlib import Path

import typer

from totelegram.cli.commands.config import _get_config_tools, handle_config_errors
from totelegram.cli.observer import RichDownloadObserver
from totelegram.cli.state import CLIState
from totelegram.cli.ui import UI
from totelegram.engine.downloader import DownloadEngine, DownloadIntegrityError
from totelegram.i18n import _
from totelegram.packaging.schemas import TTAPE_EXTENSION
from totelegram.packaging.snapshot import SnapshotService
from totelegram.schemas import SourceType

logger = logging.getLogger(__name__)


@handle_config_errors
def download_snapshot(
    ctx: typer.Context,
    snapshot_path: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help=_("Ruta al archivo snapshot (.json.xz o .ttape)"),
    ),
    output_dir: Path | None = typer.Option(
        None,
        "--output-dir",
        "-o",
        help=_(
            "Directorio donde se restaurará el contenido (por defecto: directorio del snapshot)."
        ),
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help=_("Sobrescribir archivos o carpetas existentes en el destino."),
    ),
    keep_cache: bool = typer.Option(
        False,
        "--keep-cache",
        "-k",
        help=_("Conservar los segmentos descargados tras una restauración exitosa."),
    ),
    file_path: str | None = typer.Option(
        None,
        "--file",
        help=_(
            "Extracción quirúrgica: Ruta interna del archivo específico a restaurar (omite el resto del backup)."
        ),
    ),
):
    """Descarga y restaura un archivo o carpeta archivada a partir de su Snapshot (.json.xz o .ttape).

    Verifica la integridad de cada pieza y archivo on-the-fly con soporte de reanudación.
    """
    state: CLIState = ctx.obj
    profile_name, _ = _get_config_tools(ctx)

    # Cargar y deserializar el Snapshot de forma polimórfica (.ttape o .json.xz)
    with UI.loading(
        _("Cargando snapshot: [bold]{name}[/]...").format(name=snapshot_path.name)
    ):
        try:
            manifest = SnapshotService.load_snapshot(snapshot_path)
        except Exception as e:
            UI.error(_("Error al leer el snapshot: {error}").format(error=e))
            raise typer.Exit(1)

    target_output_dir = (
        output_dir.resolve() if output_dir else snapshot_path.parent.resolve()
    )

    source = manifest.source
    is_folder = source.type == SourceType.FOLDER
    is_ttape = snapshot_path.suffix == TTAPE_EXTENSION or snapshot_path.name.endswith(
        TTAPE_EXTENSION
    )

    UI.separator()

    # MODO A: Extracción quirúrgica de un solo archivo
    if file_path:
        if not is_folder:
            UI.error(
                _(
                    "La opción '--file' solo se aplica a snapshots de carpetas archivadas (TAR)."
                )
            )
            raise typer.Exit(1)

        format_badge = (
            _("[dim](Contenedor .ttape)[/dim]")
            if is_ttape
            else _("[dim](Snapshot v6.0)[/dim]")
        )
        UI.print(
            _("Extrayendo archivo puntual: [bold cyan]{file}[/]").format(file=file_path)
        )
        UI.info(
            _(
                "Desde backup: [bold]{name}[/] {badge} ({parts} partes totales en Telegram)"
            ).format(
                name=source.filename, badge=format_badge, parts=len(manifest.parts)
            )
        )
        UI.info(
            _("Destino: [bold green]{path}[/]").format(
                path=target_output_dir / file_path
            )
        )

        with state.get_telegram_session(profile_name) as client:
            observer = RichDownloadObserver()
            engine = DownloadEngine(client=client, observer=observer)  # type: ignore

            try:
                report = engine.restore_single_file(
                    manifest=manifest,
                    relative_path=file_path,
                    output_dir=target_output_dir,
                    snapshot_path=snapshot_path,
                    force=force,
                    keep_cache=keep_cache,
                )

                UI.separator()
                UI.success(
                    _("Archivo [bold]{file}[/] extraído exitosamente.").format(
                        file=file_path
                    )
                )
                UI.info(
                    _("Ubicación: [bold cyan]{path}[/]").format(path=report.output_path)
                )
                UI.info(
                    _("Tráfico transferido: [dim]{mb:.2f} MB[/dim]").format(
                        mb=report.total_bytes_downloaded / (1024 * 1024)
                    )
                )
                return

            except FileNotFoundError as e:
                UI.error(str(e))
                raise typer.Exit(1)
            except FileExistsError as e:
                UI.error(str(e))
                UI.tip(_("Usa la opción [bold]--force[/] para sobrescribir."))
                raise typer.Exit(1)
            except DownloadIntegrityError as e:
                UI.error(_("Fallo de integridad: {error}").format(error=e))
                raise typer.Exit(1)

    # MODO B: Restauración completa estándar
    item_desc = _("Carpeta") if is_folder else _("Archivo")
    total_parts = len(manifest.parts)
    parts_desc = _("Archivo único") if total_parts == 1 else _("Dividido en partes")
    parts_count_str = (
        _("1 parte en Telegram")
        if total_parts == 1
        else _("{count} partes en Telegram").format(count=total_parts)
    )

    UI.print(
        _("Restaurando {item}: [bold cyan]{name}[/]").format(
            item=item_desc, name=source.filename
        )
    )
    UI.info(
        _("Estructura: [bold]{desc}[/] ({count_str})").format(
            desc=parts_desc, count_str=parts_count_str
        )
    )
    UI.info(
        _("Destino: [bold green]{path}[/]").format(
            path=target_output_dir / source.filename
        )
    )
    if force:
        UI.warn(
            _(
                "Modo --force activo: Los archivos existentes en el destino serán sobrescritos."
            )
        )

    UI.separator()

    with state.get_telegram_session(profile_name) as client:
        observer = RichDownloadObserver()
        engine = DownloadEngine(client=client, observer=observer)  # type: ignore

        try:
            report = engine.restore(
                manifest=manifest,
                output_dir=target_output_dir,
                snapshot_path=snapshot_path,
                force=force,
                keep_cache=keep_cache,
            )

            UI.separator()
            UI.success(
                _("{item} [bold]{name}[/] restaurado exitosamente.").format(
                    item=item_desc, name=source.filename
                )
            )
            UI.info(
                _("Ubicación: [bold cyan]{path}[/]").format(path=report.output_path)
            )
            if is_folder:
                UI.info(
                    _("Archivos verificados: [bold]{count}[/]").format(
                        count=report.files_extracted
                    )
                )
            UI.info(
                _("Total transferido: [dim]{mb:.2f} MB[/dim]").format(
                    mb=report.total_bytes_downloaded / (1024 * 1024)
                )
            )

        except FileExistsError as e:
            UI.error(str(e))
            UI.tip(_("Usa la opción [bold]--force[/] para sobrescribir."))
            raise typer.Exit(1)
        except DownloadIntegrityError as e:
            UI.error(
                _("Fallo de integridad durante la restauración: {error}").format(
                    error=e
                )
            )
            raise typer.Exit(1)
        except Exception as e:
            UI.error(_("Error inesperado durante la descarga: {error}").format(error=e))
            logger.exception("Error crítico en comando download")
            raise typer.Exit(1)
