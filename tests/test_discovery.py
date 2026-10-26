import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from totelegram.database import DatabaseSession
from totelegram.discovery import DiscoveryService
from totelegram.models import (
    Job,
    Payload,
    RemotePayload,
    RemoteSnapshot,
    Source,
    TelegramChat,
    TelegramUser,
)
from totelegram.schemas import (
    AvailabilityState,
    JobStatus,
    SourceType,
    Strategy,
    StrategyConfig,
)


class TestDiscoveryService(unittest.TestCase):
    def setUp(self):
        self.db_session = DatabaseSession("sqlite:///:memory:", auto_init_schema=True)
        self.db = self.db_session.start()

        self.mock_client = MagicMock()
        self.discovery = DiscoveryService(self.mock_client, self.db)

        # Fixtures de chats y usuario
        self.chat_target = TelegramChat.create(
            id=-100111111, title="Chat Destino", type="channel"
        )
        self.chat_mirror = TelegramChat.create(
            id=-100222222, title="Chat Espejo", type="channel"
        )
        self.user = TelegramUser.create(
            id=12345, first_name="DiscoveryUser", is_premium=False
        )

        # Source de prueba
        self.source = Source.create(
            path_str="/tmp/video_discovery.mp4",
            md5sum="hash_discovery_123",
            size=2000,
            mtime=1700000000.0,
            mimetype="video/mp4",
            type=SourceType.FILE,
        )

        # Job en chat destino
        self.job_target = Job.create(
            source=self.source,
            chat=self.chat_target,
            strategy=Strategy.CHUNKED,
            status=JobStatus.PENDING,
            config=StrategyConfig(
                tg_max_size=1000, user_is_premium=False, app_version="0.9.15"
            ),
        )

        # Payloads esperados (2 partes de 1000 bytes)
        self.p0 = Payload.create(
            job=self.job_target,
            sequence_index=0,
            start_offset=0,
            end_offset=1000,
            size=1000,
            filename="video.mp4.01-02",
            filename_short="h.mp4.01-02",
        )
        self.p1 = Payload.create(
            job=self.job_target,
            sequence_index=1,
            start_offset=1000,
            end_offset=2000,
            size=1000,
            filename="video.mp4.02-02",
            filename_short="h.mp4.02-02",
        )

    def tearDown(self):
        self.db_session.close()

    def test_heuristic_search_skips_small_files(self):
        """Archivos menores al umbral (20 MB) no deben consultar a la API de búsqueda de Telegram."""
        # self.source mide 2000 bytes (< 20 MB)
        duplicates = self.discovery._search_potential_duplicates(self.job_target)

        self.assertEqual(len(duplicates), 0)
        self.mock_client.search_messages.assert_not_called()

    def test_heuristic_search_queries_telegram_for_large_files(self):
        """Archivos grandes (>= 20 MB) deben buscar por nombre limpio en el chat de destino."""
        large_source = Source.create(
            path_str="/tmp/pelicula_vacaciones_2024.1080p.mkv",
            md5sum="hash_large_file_unique",
            size=50 * 1024 * 1024,  # 50 MB (> 20 MB)
            mtime=1700000000.0,
            mimetype="video/x-matroska",
            type=SourceType.FILE,
        )
        large_job = Job.create(
            source=large_source,
            chat=self.chat_target,
            strategy=Strategy.SINGLE,
            status=JobStatus.PENDING,
            config=StrategyConfig(
                tg_max_size=2000 * 1024 * 1024,
                user_is_premium=False,
                app_version="0.9.25",
            ),
        )

        # Mock del mensaje encontrado en Telegram
        mock_msg = MagicMock()
        mock_msg.id = 777
        mock_msg.date = datetime.now(timezone.utc)
        mock_msg.caption = "Subido el mes pasado"
        mock_msg.document = MagicMock(
            file_name="pelicula_vacaciones_2024.mkv",
            file_size=52 * 1024 * 1024,
        )
        self.mock_client.search_messages.return_value = [mock_msg]

        duplicates = self.discovery._search_potential_duplicates(large_job)

        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0].message_id, 777)
        self.assertEqual(duplicates[0].file_name, "pelicula_vacaciones_2024.mkv")
        self.assertEqual(duplicates[0].file_size, 52 * 1024 * 1024)
        self.mock_client.search_messages.assert_called_once()

    def test_heuristic_search_ignores_manifest_or_system_captions(self):
        """Los mensajes de snapshot o locks del sistema (#totelegram) deben ser ignorados en los duplicados."""
        large_source = Source.create(
            path_str="/tmp/backup_documentos_2024.tar",
            md5sum="hash_large_backup_123",
            size=30 * 1024 * 1024,
            mtime=1700000000.0,
            mimetype="application/x-tar",
            type=SourceType.FOLDER,
        )
        large_job = Job.create(
            source=large_source,
            chat=self.chat_target,
            strategy=Strategy.SINGLE,
            status=JobStatus.PENDING,
            config=StrategyConfig(
                tg_max_size=2000 * 1024 * 1024,
                user_is_premium=False,
                app_version="0.9.25",
            ),
        )

        mock_system_msg = MagicMock()
        mock_system_msg.id = 888
        mock_system_msg.caption = "🏷 #totelegram_manifest #src_hash_large_backup_123"
        mock_system_msg.document = MagicMock(
            file_name="backup_documentos_2024.ttape", file_size=1000
        )

        self.mock_client.search_messages.return_value = [mock_system_msg]

        duplicates = self.discovery._search_potential_duplicates(large_job)
        self.assertEqual(len(duplicates), 0)

    def test_investigate_includes_potential_duplicates_when_needs_upload(self):
        """investigate() debe poblar el campo potential_duplicates del reporte."""
        large_source = Source.create(
            path_str="/tmp/video_cumpleanos_2023.mp4",
            md5sum="hash_cumple_unique_123",
            size=40 * 1024 * 1024,
            mtime=1700000000.0,
            mimetype="video/mp4",
            type=SourceType.FILE,
        )
        large_job = Job.create(
            source=large_source,
            chat=self.chat_target,
            strategy=Strategy.SINGLE,
            status=JobStatus.PENDING,
            config=StrategyConfig(
                tg_max_size=2000 * 1024 * 1024,
                user_is_premium=False,
                app_version="0.9.25",
            ),
        )

        mock_msg = MagicMock()
        mock_msg.id = 901
        mock_msg.date = datetime.now(timezone.utc)
        mock_msg.caption = ""
        mock_msg.document = MagicMock(
            file_name="video_cumpleanos_2023.mp4", file_size=40 * 1024 * 1024
        )

        # search_messages se llama tanto en _discover_and_hydrate_jit como en _search_potential_duplicates
        # Retornamos vacío para la búsqueda por hashtag, y mock_msg para la búsqueda por nombre.
        self.mock_client.search_messages.side_effect = [[], [mock_msg]]

        report = self.discovery.investigate(large_job)
        self.assertEqual(report.state, AvailabilityState.NEEDS_UPLOAD)
        self.assertEqual(len(report.potential_duplicates), 1)
        self.assertEqual(report.potential_duplicates[0].message_id, 901)

    def _create_mock_tg_message(self, message_id: int, chat_id: int, file_size: int):
        msg = MagicMock()
        msg.id = message_id
        msg.chat = MagicMock(id=chat_id)
        msg.empty = False
        msg.document = MagicMock(file_size=file_size, file_id=f"file_id_{message_id}")
        msg.__str__.return_value = json.dumps(  # type: ignore
            {
                "id": message_id,
                "chat": {"id": chat_id},
                "document": {
                    "file_size": file_size,
                    "file_id": f"file_id_{message_id}",
                },
            }
        )
        return msg

    def test_investigate_returns_needs_upload_when_no_prior_uploads(self):
        """Si el archivo no existe en el destino ni en otros chats, requiere subida completa."""
        report = self.discovery.investigate(self.job_target)
        self.assertEqual(report.state, AvailabilityState.NEEDS_UPLOAD)
        self.assertEqual(len(report.remotes), 0)

    def test_investigate_returns_fulfilled_when_all_parts_exist_locally(self):
        """Si todas las piezas existen en el destino y están verificadas/frescas, retorna FULFILLED."""
        msg0 = self._create_mock_tg_message(101, self.chat_target.id, 1000)
        msg1 = self._create_mock_tg_message(102, self.chat_target.id, 1000)

        r0 = RemotePayload.register_upload(self.p0, msg0, self.user)
        r1 = RemotePayload.register_upload(self.p1, msg1, self.user)

        r0.mark_verified(msg0)
        r1.mark_verified(msg1)

        report = self.discovery.investigate(self.job_target)
        self.assertEqual(report.state, AvailabilityState.FULFILLED)

    def test_investigate_returns_can_forward_when_mirror_exists_in_another_chat(self):
        """Si existe un Job completado con Snapshot en otro chat, retorna CAN_FORWARD con sus remotos."""
        job_mirror = Job.create(
            source=self.source,
            chat=self.chat_mirror,
            strategy=Strategy.CHUNKED,
            status=JobStatus.UPLOADED,
            last_verified_at=datetime.now(timezone.utc),
            config=StrategyConfig(
                tg_max_size=1000, user_is_premium=False, app_version="0.9.15"
            ),
        )
        RemoteSnapshot.register(
            job=job_mirror,
            message_id=8888,
            chat=self.chat_mirror,
            owner=self.user,
        )
        mp0 = Payload.create(
            job=job_mirror,
            sequence_index=0,
            start_offset=0,
            end_offset=1000,
            size=1000,
            filename="video.mp4.01-02",
            filename_short="h.mp4.01-02",
        )
        mp1 = Payload.create(
            job=job_mirror,
            sequence_index=1,
            start_offset=1000,
            end_offset=2000,
            size=1000,
            filename="video.mp4.02-02",
            filename_short="h.mp4.02-02",
        )

        msg0 = self._create_mock_tg_message(201, self.chat_mirror.id, 1000)
        msg1 = self._create_mock_tg_message(202, self.chat_mirror.id, 1000)

        r0 = RemotePayload.register_upload(mp0, msg0, self.user)
        r1 = RemotePayload.register_upload(mp1, msg1, self.user)
        r0.mark_verified(msg0)
        r1.mark_verified(msg1)

        report = self.discovery.investigate(self.job_target)

        self.assertEqual(report.state, AvailabilityState.CAN_FORWARD)
        self.assertTrue(report.can_forward)
        self.assertEqual(len(report.remotes), 2)
        self.assertEqual(report.remotes[0].message_id, 201)
        self.assertEqual(report.remotes[1].message_id, 202)

    def test_investigate_skips_incomplete_mirror(self):
        """Un Job espejo al que le falte alguna pieza no debe ser considerado para Smart Forward."""
        job_mirror = Job.create(
            source=self.source,
            chat=self.chat_mirror,
            strategy=Strategy.CHUNKED,
            status=JobStatus.UPLOADED,
            last_verified_at=datetime.now(timezone.utc),
            config=StrategyConfig(
                tg_max_size=1000, user_is_premium=False, app_version="0.9.15"
            ),
        )
        RemoteSnapshot.register(
            job=job_mirror,
            message_id=8888,
            chat=self.chat_mirror,
            owner=self.user,
        )
        # Se crean las 2 piezas del Job espejo
        mp0 = Payload.create(
            job=job_mirror,
            sequence_index=0,
            start_offset=0,
            end_offset=1000,
            size=1000,
            filename="video.mp4.01-02",
            filename_short="h.mp4.01-02",
        )
        _ = Payload.create(
            job=job_mirror,
            sequence_index=1,
            start_offset=1000,
            end_offset=2000,
            size=1000,
            filename="video.mp4.02-02",
            filename_short="h.mp4.02-02",
        )

        # Solo se sube 1 de las 2 piezas (mp0 sí, mp1 no)
        msg0 = self._create_mock_tg_message(201, self.chat_mirror.id, 1000)
        r0 = RemotePayload.register_upload(mp0, msg0, self.user)
        r0.mark_verified(msg0)

        report = self.discovery.investigate(self.job_target)
        self.assertEqual(report.state, AvailabilityState.NEEDS_UPLOAD)

    def test_investigate_ignores_mirror_without_snapshot(self):
        """Un Job en otro chat sin snapshot_message_id debe ser ignorado por completo."""
        job_mirror = Job.create(
            source=self.source,
            chat=self.chat_mirror,
            strategy=Strategy.CHUNKED,
            status=JobStatus.UPLOADED,
            config=StrategyConfig(
                tg_max_size=1000, user_is_premium=False, app_version="0.9.15"
            ),
        )
        mp0 = Payload.create(
            job=job_mirror,
            sequence_index=0,
            start_offset=0,
            end_offset=1000,
            size=1000,
            filename="video.mp4.01-02",
            filename_short="h.mp4.01-02",
        )
        msg0 = self._create_mock_tg_message(201, self.chat_mirror.id, 1000)
        RemotePayload.register_upload(mp0, msg0, self.user)

        report = self.discovery.investigate(self.job_target)

        # No se considera para reenviar porque no tiene un Snapshot que lo respalde
        self.assertEqual(report.state, AvailabilityState.NEEDS_UPLOAD)
        self.assertEqual(len(report.remotes), 0)

    def test_jit_batch_marks_orphaned_when_message_deleted_in_telegram(self):
        """La validación JIT debe orfanar el registro si Telegram informa que el mensaje no existe o está vacío."""
        msg0 = self._create_mock_tg_message(301, self.chat_target.id, 1000)
        r0 = RemotePayload.register_upload(self.p0, msg0, self.user)

        # Forzar que el remote no sea "fresh" (vencido en el pasado)
        r0.last_verified_at = datetime.now(timezone.utc) - timedelta(minutes=30)
        r0.save()

        # Simular que Telegram devuelve un mensaje vacío (eliminado)
        deleted_msg = MagicMock(id=301, empty=True, document=None)
        self.mock_client.get_messages.return_value = [deleted_msg]

        with patch("time.sleep", return_value=None):
            is_valid = self.discovery._validate_jit_batch([r0])

        self.assertFalse(is_valid)
        r0_refreshed = RemotePayload.get_by_id(r0.id)
        self.assertTrue(r0_refreshed.is_orphaned)

    def test_jit_batch_marks_orphaned_when_file_size_mismatches(self):
        """La validación JIT protege contra edición maliciosa de archivos en Telegram."""
        msg0 = self._create_mock_tg_message(401, self.chat_target.id, 1000)
        r0 = RemotePayload.register_upload(self.p0, msg0, self.user)
        r0.last_verified_at = datetime.now(timezone.utc) - timedelta(minutes=30)
        r0.save()

        # Mensaje alterado: tamaño reportado por Telegram es 500 en vez de 1000
        altered_msg = self._create_mock_tg_message(401, self.chat_target.id, 500)
        self.mock_client.get_messages.return_value = [altered_msg]

        with patch("time.sleep", return_value=None):
            is_valid = self.discovery._validate_jit_batch([r0])

        self.assertFalse(is_valid)
        r0_refreshed = RemotePayload.get_by_id(r0.id)
        self.assertTrue(r0_refreshed.is_orphaned)

    def test_partial_resume_orphans_deleted_chunks(self):
        """Si un Job incompleto tiene piezas que fueron borradas de Telegram,
        la investigación JIT debe marcarlas como huérfanas antes de reanudar."""
        # Se subió la parte 0 pero no la parte 1
        msg0 = self._create_mock_tg_message(901, self.chat_target.id, 1000)
        r0 = RemotePayload.register_upload(self.p0, msg0, self.user)

        # Forzar que r0 haya expirado en frescura
        r0.last_verified_at = datetime.now(timezone.utc) - timedelta(minutes=30)
        r0.save()

        # Simular que Telegram informa que el mensaje 901 fue borrado (vacío)
        deleted_msg = MagicMock(id=901, empty=True, document=None)
        self.mock_client.get_messages.return_value = [deleted_msg]

        with patch("time.sleep", return_value=None):
            report = self.discovery.investigate(self.job_target)

        # Debe requerir subida completa porque la pieza 0 murió
        self.assertEqual(report.state, AvailabilityState.NEEDS_UPLOAD)

        # La pieza 0 debe haber quedado huérfana en la base de datos
        r0_refreshed = RemotePayload.get_by_id(r0.id)
        self.assertTrue(r0_refreshed.is_orphaned)


if __name__ == "__main__":
    unittest.main()
