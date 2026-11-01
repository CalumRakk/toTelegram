from pathlib import Path
from typing import cast

from pyrogram.types import Message

from tests_real.base import RealTelegramTestCase
from totelegram.engine.downloader import DownloadEngine
from totelegram.engine.pipeline import JobPipeline
from totelegram.models import RemotePayload
from totelegram.packaging.schemas import TTAPE_EXTENSION
from totelegram.packaging.snapshot import SnapshotService
from totelegram.schemas import SourceType


class TestRealTapeRestore(RealTelegramTestCase):
    """
    Prueba de integración para el flujo de backup de directorios (Cintas TAR .ttape):
    1. Empaquetado y subida particionada de una carpeta con estructura interna.
    2. Descarga del contenedor .ttape desde Telegram.
    3. Extracción quirúrgica (--file): descargar solo los bytes de un archivo específico.
    4. Restauración completa de todo el árbol de directorios con integridad 100%.
    """

    def test_backup_folder_and_surgical_restore(self):

        # PREPARACIÓN DEL ÁRBOL DE DIRECTORIOS

        # Estructura:
        # backup_test/
        # ├── doc1.txt            (~60 KB)
        # └── sub/
        #     └── heavy_video.bin (~3.5 MB) -> Forzará la creación de al menos 2 volúmenes (corte a 2 MB)
        folder_dir = self.create_temp_dir() / "backup_test"
        sub_dir = folder_dir / "sub"
        sub_dir.mkdir(parents=True)

        file1_path = self.create_dummy_file(
            "doc1.txt", 60 * 1024, parent_dir=folder_dir
        )
        md5_file1 = self.calculate_md5(file1_path)

        file2_path = self.create_dummy_file(
            "heavy_video.bin", 3500 * 1024, parent_dir=sub_dir
        )
        md5_file2 = self.calculate_md5(file2_path)

        # SUBIDA FÍSICA COMO CINTA TAR (JobPipeline)

        upload_session = self.build_upload_session()
        pipeline = JobPipeline(session=upload_session)

        result = pipeline.process(
            path=folder_dir,
            is_last_in_batch=True,
            force=True,
            auto_truncate=True,
        )

        self.assertTrue(result.is_completed, "El job de la carpeta debió completarse.")
        self.assertTrue(
            result.snapshot_generated, "Se debió generar el contenedor .ttape."
        )
        job = result.job
        self.assertTrue(job.source.is_folder, "El Source debe ser de tipo FOLDER.")

        # Registrar mensajes de volúmenes TAR para limpieza automática
        chunk_remotes = list(
            RemotePayload.select().where(RemotePayload.payload << job.payloads)
        )
        self.assertGreaterEqual(
            len(chunk_remotes),
            2,
            f"Se esperaban al menos 2 volúmenes TAR (corte a 2 MB), se generaron {len(chunk_remotes)}.",
        )
        self.track_messages([r.message_id for r in chunk_remotes])

        # Registrar mensaje del snapshot (.ttape) para limpieza
        self.assertIsNotNone(
            job.snapshot_message_id, "El Job debe tener snapshot_message_id."
        )
        self.track_message(job.snapshot_message_id)

        # VERIFICACIÓN DEL SNAPSHOT EN TELEGRAM (.ttape)

        restore_staging_dir = self.create_temp_dir()

        snapshot_msg = cast(
            Message,
            self.client.get_messages(self.test_chat_id, job.snapshot_message_id),
        )
        self.assertIsNotNone(
            snapshot_msg.document, "El snapshot en Telegram debe ser un documento."
        )
        self.assertTrue(
            snapshot_msg.document.file_name.endswith(TTAPE_EXTENSION),
            f"El archivo debe tener extensión {TTAPE_EXTENSION}, recibido: {snapshot_msg.document.file_name}",
        )

        # Descargar el .ttape
        downloaded_ttape_path = restore_staging_dir / snapshot_msg.document.file_name
        dl_res = self.client.download_media(
            message=snapshot_msg,
            file_name=str(downloaded_ttape_path),
        )
        self.assertIsNotNone(dl_res, "Fallo al descargar el .ttape desde Telegram.")

        # Cargar manifiesto del .ttape
        manifest = SnapshotService.load_snapshot(Path(dl_res))
        self.assertEqual(manifest.source.type, SourceType.FOLDER)
        self.assertIsNotNone(
            manifest.source.tape_catalog, "Debe contener el TapeCatalog de TarTape."
        )

        engine = DownloadEngine(client=self.client)

        # EXTRACCIÓN QUIRÚRGICA: doc1.txt (archivo pequeño al inicio de la cinta)

        surgical_dir = self.create_temp_dir()

        report_file1 = engine.restore_single_file(
            manifest=manifest,
            relative_path="doc1.txt",
            output_dir=surgical_dir,
            snapshot_path=Path(dl_res),
            force=True,
        )
        self.assertTrue(
            report_file1.is_success, "La extracción quirúrgica de doc1.txt falló."
        )

        restored_doc1 = surgical_dir / "doc1.txt"
        self.assertTrue(
            restored_doc1.exists(), f"El archivo quirúrgico no existe: {restored_doc1}"
        )
        self.assertEqual(
            self.calculate_md5(restored_doc1),
            md5_file1,
            "El hash MD5 de doc1.txt extraído quirúrgicamente no coincide.",
        )

        # EXTRACCIÓN QUIRÚRGICA: sub/heavy_video.bin (archivo anidado pesado)

        report_file2 = engine.restore_single_file(
            manifest=manifest,
            relative_path="sub/heavy_video.bin",
            output_dir=surgical_dir,
            snapshot_path=Path(dl_res),
            force=True,
        )
        self.assertTrue(
            report_file2.is_success,
            "La extracción quirúrgica de heavy_video.bin falló.",
        )

        restored_heavy = surgical_dir / "sub" / "heavy_video.bin"
        self.assertTrue(
            restored_heavy.exists(),
            f"El archivo quirúrgico no existe: {restored_heavy}",
        )
        self.assertEqual(
            self.calculate_md5(restored_heavy),
            md5_file2,
            "El hash MD5 de sub/heavy_video.bin extraído quirúrgicamente no coincide.",
        )

        # RESTAURACIÓN COMPLETA DE LA CARPETA

        full_restore_dir = self.create_temp_dir()

        report_full = engine.restore(
            manifest=manifest,
            output_dir=full_restore_dir,
            snapshot_path=Path(dl_res),
            force=True,
        )
        self.assertTrue(
            report_full.is_success,
            f"La restauración completa falló: {report_full.message}",
        )

        # Comprobar árbol desempaquetado en disco
        restored_root = full_restore_dir / folder_dir.name
        self.assertTrue(
            restored_root.is_dir(),
            f"No se encontró el directorio raíz restaurado: {restored_root}",
        )

        restored_full_doc1 = restored_root / "doc1.txt"
        restored_full_heavy = restored_root / "sub" / "heavy_video.bin"

        self.assertTrue(restored_full_doc1.exists())
        self.assertTrue(restored_full_heavy.exists())

        self.assertEqual(
            self.calculate_md5(restored_full_doc1),
            md5_file1,
            "MD5 de doc1.txt tras restauración completa no coincide.",
        )
        self.assertEqual(
            self.calculate_md5(restored_full_heavy),
            md5_file2,
            "MD5 de heavy_video.bin tras restauración completa no coincide.",
        )
