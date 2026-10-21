import logging
from pathlib import Path

import typer

from totelegram.cli.commands.config import _get_config_tools, handle_config_errors
from totelegram.cli.observer import RichDownloadObserver
from totelegram.cli.state import CLIState
from totelegram.cli.ui import UI
from totelegram.engine.downloader import DownloadEngine, DownloadIntegrityError
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
        help="Ruta al archivo snapshot (.json.xz o .ttape)",
    ),
    output_dir: Path | None = typer.Option(
        None,
        "--output-dir",
        "-o",
        help="Directorio donde se restaurará el contenido (por defecto: directorio del snapshot).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Sobrescribir archivos o carpetas existentes en el destino.",
    ),
    keep_cache: bool = typer.Option(
        False,
        "--keep-cache",
        "-k",
        help="Conservar los segmentos descargados tras una restauración exitosa.",
    ),
    file_path: str | None = typer.Option(
        None,
        "--file",
        help="Extracción quirúrgica: Ruta interna del archivo específico a restaurar (omite el resto del backup).",
    ),
):
    """Descarga y restaura un archivo o carpeta archivada a partir de su Snapshot (.json.xz o .ttape).

    Verifica la integridad de cada pieza y archivo on-the-fly con soporte de reanudación.
    """
    state: CLIState = ctx.obj
    profile_name, _ = _get_config_tools(ctx)

    # Cargar y deserializar el Snapshot de forma polimórfica (.ttape o .json.xz)
    with UI.loading(f"Cargando snapshot: [bold]{snapshot_path.name}[/]..."):
        try:
            manifest = SnapshotService.load_snapshot(snapshot_path)
        except Exception as e:
            UI.error(f"Error al leer el snapshot: {e}")
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
                "La opción '--file' solo se aplica a snapshots de carpetas archivadas (TAR)."
            )
            raise typer.Exit(1)

        format_badge = (
            "[dim](Contenedor .ttape)[/dim]"
            if is_ttape
            else "[dim](Snapshot v6.0)[/dim]"
        )
        UI.print(f"Extrayendo archivo puntual: [bold cyan]{file_path}[/]")
        UI.info(
            f"Desde backup: [bold]{source.filename}[/] {format_badge} "
            f"({len(manifest.parts)} partes totales en Telegram)"
        )
        UI.info(f"Destino: [bold green]{target_output_dir / file_path}[/]")

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
                UI.success(f"Archivo [bold]{file_path}[/] extraído exitosamente.")
                UI.info(f"Ubicación: [bold cyan]{report.output_path}[/]")
                UI.info(
                    f"Tráfico transferido: [dim]{report.total_bytes_downloaded / (1024 * 1024):.2f} MB[/dim]"
                )
                return

            except FileNotFoundError as e:
                UI.error(str(e))
                raise typer.Exit(1)
            except FileExistsError as e:
                UI.error(str(e))
                UI.tip("Usa la opción [bold]--force[/] para sobrescribir.")
                raise typer.Exit(1)
            except DownloadIntegrityError as e:
                UI.error(f"Fallo de integridad: {e}")
                raise typer.Exit(1)

    # MODO B: Restauración completa estándar
    item_desc = "Carpeta" if is_folder else "Archivo"
    UI.print(f"Restaurando {item_desc}: [bold cyan]{source.filename}[/]")
    UI.info(
        f"Estrategia: [bold]{manifest.strategy.value}[/] ({len(manifest.parts)} partes en Telegram)"
    )
    UI.info(f"Destino: [bold green]{target_output_dir / source.filename}[/]")

    if force:
        UI.warn(
            "Modo --force activo: Los archivos existentes en el destino serán sobrescritos."
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
                f"{item_desc} [bold]{source.filename}[/] restaurado exitosamente."
            )
            UI.info(f"Ubicación: [bold cyan]{report.output_path}[/]")
            if is_folder:
                UI.info(f"Archivos verificados: [bold]{report.files_extracted}[/]")
            UI.info(
                f"Total transferido: [dim]{report.total_bytes_downloaded / (1024 * 1024):.2f} MB[/dim]"
            )

        except FileExistsError as e:
            UI.error(str(e))
            UI.tip("Usa la opción [bold]--force[/] para sobrescribir.")
            raise typer.Exit(1)
        except DownloadIntegrityError as e:
            UI.error(f"Fallo de integridad durante la restauración: {e}")
            raise typer.Exit(1)
        except Exception as e:
            UI.error(f"Error inesperado durante la descarga: {e}")
            logger.exception("Error crítico en comando download")
            raise typer.Exit(1)
