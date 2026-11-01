from pathlib import Path
from typing import cast

from pyrogram.types import Message

from tests_real.base import RealTelegramTestCase
from totelegram.engine.downloader import DownloadEngine
from totelegram.engine.pipeline import JobPipeline
from totelegram.models import RemotePayload
from totelegram.packaging.snapshot import SnapshotService


class TestRealFilePipeline(RealTelegramTestCase):
    """
    Verifica contra los servidores reales de Telegram que un archivo que excede
    el tamaño de corte (2 MB) se divida, suba y restaure con integridad del 100%.
    """

    def test_upload_and_restore_chunked_file(self):
        # PREPARACIÓN DE DATOS (5 MB con corte a 2 MB -> 3 partes)
        file_size_bytes = 5 * 1024 * 1024  # 5 MB
        file_name = "test_chunked_video.bin"

        source_file = self.create_dummy_file(
            filename=file_name,
            size_bytes=file_size_bytes,
        )
        original_md5 = self.calculate_md5(source_file)

        # EJECUCIÓN DE LA SUBIDA FÍSICA (JobPipeline)
        upload_session = self.build_upload_session()
        pipeline = JobPipeline(session=upload_session)

        result = pipeline.process(
            path=source_file,
            is_last_in_batch=True,
            force=True,
        )

        # COMPROBACIONES DEL PIPELINE Y BASE DE DATOS
        self.assertTrue(result.is_completed, "El job debería haberse completado.")
        self.assertTrue(
            result.snapshot_generated, "Se debió generar y publicar el snapshot."
        )

        job = result.job
        total_parts = job.payloads.count()
        self.assertEqual(
            total_parts,
            3,
            f"Se esperaban 3 partes (2MB + 2MB + 1MB), pero se generaron {total_parts}.",
        )

        # Registrar mensajes de los chunks para limpieza automática en tearDown
        chunk_remotes = list(
            RemotePayload.select().where(RemotePayload.payload << job.payloads)
        )
        self.assertEqual(len(chunk_remotes), 3, "Deben existir 3 RemotePayloads en BD.")

        chunk_msg_ids = [r.message_id for r in chunk_remotes]
        self.track_messages(chunk_msg_ids)

        # Registrar mensaje del snapshot para limpieza
        self.assertIsNotNone(
            job.snapshot_message_id, "El Job debe tener snapshot_message_id registrado."
        )
        self.track_message(job.snapshot_message_id)

        # VERIFICACIÓN FÍSICA EN TELEGRAM
        # Consultamos los mensajes reales desde la API de Telegram
        tg_messages = cast(
            list[Message],
            self.client.get_messages(self.test_chat_id, chunk_msg_ids),
        )

        for idx, msg in enumerate(tg_messages, 1):
            self.assertIsNotNone(
                msg, f"El mensaje del chunk {idx} no existe en Telegram."
            )
            self.assertIsNotNone(
                msg.document, f"El mensaje {msg.id} debe contener un documento."
            )
            self.assertIn(
                f"#part_0{idx}_03",
                msg.caption or "",
                f"El caption del chunk {idx} no contiene el tag de partición esperado.",
            )
            self.assertIn(
                f"#src_{original_md5[:32]}",
                msg.caption or "",
                "El caption debe contener la baliza del hash de origen.",
            )

        # RESTAURACIÓN Y REENSAMBLADO (DownloadEngine)
        restore_dir = self.create_temp_dir()

        # Descargamos el snapshot (.json.xz) directamente desde Telegram
        snapshot_msg = cast(
            Message,
            self.client.get_messages(self.test_chat_id, job.snapshot_message_id),
        )
        self.assertIsNotNone(
            snapshot_msg.document, "El snapshot en Telegram debe ser un documento."
        )

        temp_snapshot_path = restore_dir / snapshot_msg.document.file_name
        downloaded_snapshot = self.client.download_media(
            message=snapshot_msg,
            file_name=str(temp_snapshot_path),
        )
        self.assertIsNotNone(
            downloaded_snapshot, "Fallo al descargar el snapshot desde Telegram."
        )

        # Cargar manifiesto del snapshot descargado
        manifest = SnapshotService.load_snapshot(Path(downloaded_snapshot))
        self.assertEqual(len(manifest.parts), 3, "El manifiesto debe listar 3 partes.")

        # Restaurar archivo mediante el motor de descarga
        engine = DownloadEngine(client=self.client)
        report = engine.restore(
            manifest=manifest,
            output_dir=restore_dir,
            snapshot_path=Path(downloaded_snapshot),
            force=True,
        )

        self.assertTrue(
            report.is_success, f"La restauración reportó fallo: {report.message}"
        )

        restored_file = restore_dir / file_name
        self.assertTrue(
            restored_file.exists(),
            f"El archivo restaurado no existe en disco: {restored_file}",
        )
        self.assertEqual(
            restored_file.stat().st_size,
            file_size_bytes,
            "El tamaño del archivo restaurado no coincide con el original.",
        )

        # VERIFICACIÓN CRIPTOGRÁFICA
        restored_md5 = self.calculate_md5(restored_file)
        self.assertEqual(
            restored_md5,
            original_md5,
            f"Discrepancia criptográfica MD5. Original: {original_md5}, Restaurado: {restored_md5}",
        )
