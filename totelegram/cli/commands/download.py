import logging
from pathlib import Path
from typing import Optional

import typer

from totelegram.cli.commands.config import _get_config_tools, handle_config_errors
from totelegram.cli.observer import RichDownloadObserver
from totelegram.cli.state import CLIState
from totelegram.cli.ui import UI
from totelegram.engine.downloader import DownloadEngine, DownloadIntegrityError
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
        help="Ruta al archivo snapshot (.json.xz)",
    ),
    output_dir: Optional[Path] = typer.Option(
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
):
    """
    Descarga y restaura un archivo o carpeta archivada a partir de su Snapshot (.json.xz).
    Verifica la integridad de cada pieza y archivo on-the-fly.
    """
    state: CLIState = ctx.obj
    profile_name, _ = _get_config_tools(ctx)

    # Cargar y deserializar el Snapshot
    with UI.loading(f"Cargando snapshot: [bold]{snapshot_path.name}[/]..."):
        try:
            manifest = SnapshotService.load_snapshot(snapshot_path)
        except Exception as e:
            UI.error(f"Error al leer el snapshot: {e}")
            raise typer.Exit(1)

    # Determinar destino de restauración
    target_output_dir = (
        output_dir.resolve() if output_dir else snapshot_path.parent.resolve()
    )

    source = manifest.source
    is_folder = source.type == SourceType.FOLDER
    item_desc = "Carpeta" if is_folder else "Archivo"

    UI.separator()
    UI.print(f"Restaurando {item_desc}: [bold cyan]{source.filename}[/]")
    UI.info(
        f"Estrategia: [bold]{manifest.strategy.value}[/] ({len(manifest.parts)} partes en Telegram)"
    )
    UI.info(f"Destino: [bold green]{target_output_dir / source.filename}[/]")
    if is_folder and source.inventory:
        UI.info(f"Contenido: [bold]{len(source.inventory)}[/] archivos indexados")

    if force:
        UI.warn(
            "Modo --force activo: Los archivos existentes en el destino serán sobrescritos."
        )

    UI.separator()

    # Conectar a Telegram e iniciar el motor de descarga
    with state.get_telegram_session(profile_name) as client:
        observer = RichDownloadObserver()
        engine = DownloadEngine(client=client, observer=observer)  # type: ignore # TODO: tipear correctamente para evitar advertencia en VSCode

        try:
            report = engine.restore(
                manifest=manifest,
                output_dir=target_output_dir,
                force=force,
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
        except FileNotFoundError as e:
            UI.error(f"Recurso no encontrado: {e}")
            raise typer.Exit(1)
        except Exception as e:
            UI.error(f"Error inesperado durante la descarga: {e}")
            logger.exception("Error crítico en comando download")
            raise typer.Exit(1)
