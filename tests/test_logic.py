import unittest
from unittest.mock import MagicMock

from peewee import SqliteDatabase

from totelegram.core.setting import Settings
from totelegram.services.discovery import DiscoveryService
from totelegram.store.models import (
    Job,
    Payload,
    RemotePayload,
    SourceFile,
    TelegramChat,
    TelegramUser,
    db_proxy,
)


class TestLogicEngine(unittest.TestCase):
    def setUp(self):
        """Configuración de base de datos volátil y mocks."""
        self.test_db = SqliteDatabase(":memory:")
        db_proxy.initialize(self.test_db)
        db_proxy.create_tables(
            [SourceFile, Job, TelegramChat, Payload, RemotePayload, TelegramUser]
        )

        # Mock del cliente de Pyrogram
        self.mock_client = MagicMock()
        self.discovery = DiscoveryService(self.mock_client)

        # Settings de prueba
        self.settings = Settings(
            profile_name="test_logic",
            chat_id=100,
            api_id=123,
            api_hash="hash",
            TG_MAX_SIZE_NORMAL=100,
        )

        # Entidades base
        self.user = TelegramUser.create(id=1, first_name="User")
        self.chat_target = TelegramChat.create(id=100, title="Target", type="private")
        self.chat_source = TelegramChat.create(id=200, title="Source", type="channel")

    def tearDown(self):
        self.test_db.close()

    # def _simulate_jit(self, success=True):
    #     """
    #     Simula respuesta de Telegram para validación JIT de forma dinámica.
    #     """
    #     if not success:
    #         msg_empty = MagicMock()
    #         msg_empty.empty = True
    #         self.mock_client.get_messages.return_value = [msg_empty]
    #         return

    #     def side_effect(chat_id, message_ids):
    #         # Si nos piden una lista de IDs, devolvemos una lista de Mocks coherentes
    #         ids = (
    #             message_ids if isinstance(message_ids, (list, tuple)) else [message_ids]
    #         )
    #         messages = []

    #         for mid in ids:
    #             # Buscamos en la DB el payload que corresponde a este mensaje para sacar su tamaño real
    #             remote = RemotePayload.get_or_none(RemotePayload.message_id == mid)

    #             m = MagicMock()
    #             m.id = mid
    #             m.empty = False
    #             if remote:
    #                 # Hacemos que el Mock de Telegram coincida exactamente con lo que espera el JIT
    #                 m.document.file_size = remote.payload.size
    #             else:
    #                 m.document.file_size = -1  # Provocaría fallo si el ID no existe

    #             messages.append(m)

    #         # Pyrogram devuelve un objeto si pides uno, o lista si pides varios
    #         return messages if isinstance(message_ids, list) else messages[0]

    #     self.mock_client.get_messages.side_effect = side_effect

    # def test_case_new_file(self):
    #     """Validar que un archivo nuevo siempre pide upload físico."""
    #     source = SourceFile.create(
    #         path_str="new.mp4", md5sum="1", size=50, mtime=1, mimetype="v"
    #     )
    #     job = Job.create_contract(source, self.chat_target, False, self.settings)

    #     report = self.discovery.investigate(job)
    #     self.assertEqual(report.state, AvailabilityState.SYSTEM_NEW)

    # def test_case_mirror_proactive(self):

    #     # El archivo existe en el ecosistema (Chat Source)
    #     source = SourceFile.create(
    #         path_str="video.mp4", md5sum="abc", size=50, mtime=1, mimetype="v"
    #     )

    #     # Job original que 'creó' la existencia en el Chat Source
    #     job_old = Job.create_contract(source, self.chat_source, False, self.settings)
    #     p_old = Payload.create(job=job_old, sequence_index=0, md5sum="abc-p1", size=50)
    #     RemotePayload.create(
    #         payload=p_old,
    #         message_id=777,
    #         chat=self.chat_source,
    #         owner=self.user,
    #         json_metadata={},
    #     )

    #     # Intentamos subirlo a Chat Target (Job Nuevo)
    #     job_new = Job.create_contract(source, self.chat_target, False, self.settings)

    #     # El sistema debe predecir que espera 1 parte basada en el contrato.

    #     self._simulate_jit(success=True)

    #     report = self.discovery.investigate(job_new)

    #     self.assertEqual(report.state, AvailabilityState.REMOTE_MIRROR)
    #     assert report.remotes is not None
    #     self.assertEqual(report.remotes[0].message_id, 777)

    # def test_case_puzzle_proactive(self):
    #     """Validar que el puzzle se detecta prediciendo el número de partes."""

    #     # Archivo de 150 bytes, límite de 100 bytes -> Se esperan 2 partes.
    #     source = SourceFile.create(
    #         path_str="file.zip", md5sum="xyz", size=150, mtime=1, mimetype="v"
    #     )

    #     # Subimos parte 0 en Chat A
    #     chat_a = TelegramChat.create(id=300, title="A", type="private")
    #     job_a = Job.create_contract(source, chat_a, False, self.settings)
    #     p0 = Payload.create(job=job_a, sequence_index=0, md5sum="part0", size=100)
    #     RemotePayload.create(
    #         payload=p0, message_id=10, chat=chat_a, owner=self.user, json_metadata={}
    #     )

    #     # Subimos parte 1 en Chat B
    #     chat_b = TelegramChat.create(id=400, title="B", type="private")
    #     job_b = Job.create_contract(source, chat_b, False, self.settings)
    #     p1 = Payload.create(job=job_b, sequence_index=1, md5sum="part1", size=50)
    #     RemotePayload.create(
    #         payload=p1, message_id=20, chat=chat_b, owner=self.user, json_metadata={}
    #     )

    #     # Nuevamente, intentamos subirlo a Chat Target
    #     job_target = Job.create_contract(source, self.chat_target, False, self.settings)

    #     self._simulate_jit(success=True)

    #     report = self.discovery.investigate(job_target)

    #     # El sistema debió predecir que faltaban 2 piezas y encontrarlas todas
    #     self.assertEqual(report.state, AvailabilityState.REMOTE_PUZZLE)
    #     assert report.remotes is not None
    #     self.assertEqual(len(report.remotes), 2)

    # def test_case_restricted_jit_fails(self):
    #     """Validar que si JIT falla, el estado es RESTRICTED aunque la DB diga que existe."""
    #     source = SourceFile.create(
    #         path_str="test.txt", md5sum="abc", size=10, mtime=1, mimetype="t"
    #     )
    #     job_old = Job.create_contract(source, self.chat_source, False, self.settings)
    #     p = Payload.create(job=job_old, sequence_index=0, md5sum="p", size=10)
    #     RemotePayload.create(
    #         payload=p,
    #         message_id=1,
    #         chat=self.chat_source,
    #         owner=self.user,
    #         json_metadata={},
    #     )

    #     job_target = Job.create_contract(source, self.chat_target, False, self.settings)

    #     # Telegram responde que el mensaje fue borrado (empty=True)
    #     self._simulate_jit(success=False)

    #     report = self.discovery.investigate(job_target)
    #     self.assertEqual(report.state, AvailabilityState.REMOTE_RESTRICTED)


if __name__ == "__main__":
    unittest.main()
