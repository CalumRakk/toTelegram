"""
Verifica contra los servidores reales de Telegram la inteligencia de reanudación
del DiscoveryService:
1. RESUMED_IN_PLACE: Reanudación de subidas parciales sin re-subir partes existentes.
2. SEAL_HEALED: Auto-sellado de snapshots cuando las partes físicas ya están en Telegram.
"""

import logging
from pathlib import Path
from typing import BinaryIO, cast

from pyrogram.types import Message

from tests_real.base import RealTelegramTestCase
from totelegram.database import db_transaction
from totelegram.discovery import DiscoveryService
from totelegram.engine.downloader import DownloadEngine
from totelegram.engine.factory import PayloadStreamFactory
from totelegram.engine.pipeline import JobPipeline
from totelegram.engine.planner import JobPlanner
from totelegram.models import Payload, RemotePayload, RemoteSnapshot
from totelegram.packaging.snapshot import SnapshotService
from totelegram.schemas import ExecutionDecision

logger = logging.getLogger(__name__)


class TestRealResumption(RealTelegramTestCase):
    """
    Prueba de integración real para las decisiones de reanudación y auto-sanación.
    """

    def test_resume_in_place_partial_upload(self):
        """
        Simula una subida interrumpida en la parte 1 de 3.
        Verifica que el pipeline reanude subiendo solo las partes 2 y 3,
        y que el archivo final reensamblado sea matemáticamente idéntico.
        """
        # 1. Preparar archivo de 5 MB (con corte a 2 MB -> 3 partes)
        file_size_bytes = 5 * 1024 * 1024
        file_name = "test_resumption_video.bin"
        source_file = self.create_dummy_file(file_name, file_size_bytes)
        original_md5 = self.calculate_md5(source_file)

        upload_session = self.build_upload_session()

        # 2. Planificar el Job y preparar sus 3 payloads
        job = JobPlanner.plan(
            path=source_file,
            session=upload_session,
            force=True,
        )
        with db_transaction(self.db):
            job.prepare_chunks(source_file, upload_session.settings)

        payloads = list(job.payloads.order_by(Payload.sequence_index))
        self.assertEqual(len(payloads), 3, "Deben existir 3 payloads planificados.")

        # 3. SIMULAR FALLA: Subir físicamente ÚNICAMENTE la Parte 1
        part_1 = payloads[0]
        with PayloadStreamFactory.create_stream(part_1, source_file) as stream_ctx:
            sent_msg_1 = cast(
                Message,
                self.client.send_document(
                    chat_id=self.test_chat_id,
                    document=cast(BinaryIO, stream_ctx.stream),
                    file_name=stream_ctx.filename,
                    caption=stream_ctx.caption,
                    force_document=True,
                ),
            )

        self.track_message(sent_msg_1.id)

        with db_transaction(self.db):
            part_1.md5sum = stream_ctx.md5sum
            part_1.save(only=[Payload.md5sum, Payload.updated_at])
            RemotePayload.register_upload(part_1, sent_msg_1, upload_session.owner)

        # 4. INVESTIGACIÓN JIT (DiscoveryService)
        discovery = DiscoveryService(client=self.client, db=self.db)
        report = discovery.investigate(
            job=job,
            force=False,
            account_max_chunk_size=upload_session.settings.tg_max_size_normal,
        )

        self.assertEqual(
            report.decision,
            ExecutionDecision.RESUMED_IN_PLACE,
            "El motor debió resolver RESUMED_IN_PLACE.",
        )
        self.assertEqual(
            len(report.valid_remotes),
            1,
            "Debe reconocer exactamente 1 parte ya existente en Telegram.",
        )
        self.assertEqual(
            report.valid_remotes[0].message_id,
            sent_msg_1.id,
            "La parte reconocida debe ser el mensaje 1.",
        )

        # 5. EJECUTAR PIPELINE COMPLETO (Reanudación)
        pipeline = JobPipeline(session=upload_session)
        result = pipeline.process(
            path=source_file,
            is_last_in_batch=True,
            force=False,  # Sin force para respetar lo ya subido
        )

        self.assertTrue(result.is_completed, "El job debió completarse exitosamente.")
        self.assertTrue(result.snapshot_generated, "Se debió generar el snapshot.")

        # VERIFICACIÓN: Solo se debieron transferir 2 piezas (partes 2 y 3)
        self.assertIsNotNone(result.strategy_result)
        assert result.strategy_result is not None
        self.assertEqual(
            result.strategy_result.pieces_processed,
            2,
            "Solo debieron subirse 2 piezas (las partes 2 y 3 faltantes).",
        )

        # Registrar mensajes de las nuevas piezas para limpieza
        all_remotes = list(
            RemotePayload.select().where(RemotePayload.payload << job.payloads)
        )
        self.assertEqual(len(all_remotes), 3, "Deben existir las 3 partes en BD.")
        self.track_messages(
            [r.message_id for r in all_remotes if r.message_id != sent_msg_1.id]
        )
        self.track_message(job.snapshot_message_id)

        # 6. RESTAURACIÓN Y VERIFICACIÓN CRIPTOGRÁFICA
        restore_dir = self.create_temp_dir()
        snapshot_msg = cast(
            Message,
            self.client.get_messages(self.test_chat_id, job.snapshot_message_id),
        )
        downloaded_snap = self.client.download_media(
            message=snapshot_msg,
            file_name=str(restore_dir / snapshot_msg.document.file_name),
        )

        manifest = SnapshotService.load_snapshot(Path(downloaded_snap))
        self.assertEqual(
            len(manifest.parts), 3, "El manifiesto debe incluir las 3 partes."
        )

        engine = DownloadEngine(client=self.client)
        dl_report = engine.restore(
            manifest=manifest,
            output_dir=restore_dir,
            snapshot_path=Path(downloaded_snap),
            force=True,
        )
        self.assertTrue(dl_report.is_success)

        restored_file = restore_dir / file_name
        self.assertEqual(
            self.calculate_md5(restored_file),
            original_md5,
            "El archivo reensamblado desde trozos combinados (previo + reanudados) no coincide.",
        )

    def test_seal_healed_missing_snapshot(self):
        """
        Verifica que cuando todas las piezas físicas ya están en Telegram pero
        falta el snapshot (SEAL_HEALED), el motor lo selle sin transferir bytes.
        """
        # 1. Preparar archivo de 4 MB (2 partes de 2 MB)
        file_size_bytes = 4 * 1024 * 1024
        file_name = "test_seal_healed.bin"
        source_file = self.create_dummy_file(file_name, file_size_bytes)

        upload_session = self.build_upload_session()
        pipeline = JobPipeline(session=upload_session)

        # 2. Subir normalmente
        result = pipeline.process(
            path=source_file,
            is_last_in_batch=True,
            force=True,
        )
        self.assertTrue(result.is_completed)
        job = result.job

        # Registrar mensajes de trozos y snapshot original
        chunk_remotes = list(
            RemotePayload.select().where(RemotePayload.payload << job.payloads)
        )
        self.track_messages([r.message_id for r in chunk_remotes])
        old_snap_id = job.snapshot_message_id
        self.track_message(old_snap_id)

        # 3. SIMULAR PÉRDIDA DE SNAPSHOT:
        # Borramos el RemoteSnapshot de la base de datos Y de Telegram
        # (para evitar que la búsqueda JIT lo rehidrate como ALREADY_FULFILLED)
        if old_snap_id:
            try:
                self.client.delete_messages(self.test_chat_id, old_snap_id)
            except Exception as e:
                logger.warning(f"Aviso limpiando snapshot previo en Telegram: {e}")

        with db_transaction(self.db):
            RemoteSnapshot.delete().where(RemoteSnapshot.job == job).execute()

        # 4. INVESTIGACIÓN: Debe resolver SEAL_HEALED
        discovery = DiscoveryService(client=self.client, db=self.db)
        report = discovery.investigate(
            job=job,
            force=False,
            account_max_chunk_size=upload_session.settings.tg_max_size_normal,
        )

        self.assertEqual(
            report.decision,
            ExecutionDecision.SEAL_HEALED,
            "El motor debió resolver SEAL_HEALED porque todas las partes físicas están vivas.",
        )

        # 5. EJECUTAR PIPELINE: Debe sellar con 0 bytes de archivo transferidos
        new_result = pipeline.process(
            path=source_file,
            is_last_in_batch=True,
            force=False,
        )

        self.assertTrue(new_result.is_completed)
        self.assertTrue(
            new_result.snapshot_generated, "Debió regenerar y publicar el snapshot."
        )
        assert new_result.strategy_result is not None
        self.assertFalse(
            new_result.strategy_result.did_upload_bytes,
            "No debió transferir bytes de archivo físico (did_upload_bytes = False).",
        )

        # Registrar nuevo snapshot para limpieza
        self.track_message(new_result.job.snapshot_message_id)
