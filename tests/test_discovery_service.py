import unittest
from unittest.mock import MagicMock

from totelegram.common.enums import AvailabilityState
from totelegram.logic.discovery import DiscoveryService
from totelegram.manager.database import DatabaseSession
from totelegram.manager.models import (
    Job,
    Payload,
    RemotePayload,
    SourceFile,
    TelegramChat,
    TelegramUser,
)


class TestDiscoveryInvestigation(unittest.TestCase):
    """
    Prueba el motor de 'Inteligencia Colectiva' (DiscoveryService).
    Valida la capacidad del sistema para deducir el estado de un archivo
    usando la base de datos y la comprobación en tiempo real (JIT).
    """

    def setUp(self):
        self.test_db = DatabaseSession(":memory:")
        self.test_db.start()

        # Cliente de Pyrogram
        self.mock_client = MagicMock()
        self.discovery = DiscoveryService(self.mock_client)

        self.user = TelegramUser.create(id=1, first_name="User")
        self.chat_target = TelegramChat.create(id=100, title="Target", type="private")
        self.chat_source = TelegramChat.create(id=200, title="Source", type="channel")

    def tearDown(self):
        self.test_db.close()

    def _simulate_jit(self, success=True):
        """
        Simula la respuesta de la API de Telegram para la validación Just-In-Time (JIT).
        El DiscoveryService comprueba que los mensajes sigan existiendo físicamente en Telegram.
        """
        if not success:
            msg_empty = MagicMock()
            msg_empty.empty = True
            self.mock_client.get_messages.return_value = [msg_empty]
            return

        def side_effect(chat_id, message_ids):
            # Pyrogram puede recibir un solo ID o una lista. Lo estandarizamos:
            ids = (
                message_ids if isinstance(message_ids, (list, tuple)) else [message_ids]
            )
            messages = []

            for mid in ids:
                remote = RemotePayload.get_or_none(RemotePayload.message_id == mid)

                m = MagicMock()
                m.id = mid
                m.empty = False

                if remote:
                    # Hacemos que el Mock devuelva exactamente el tamaño esperado
                    # para superar la validación de integridad anti-edición.
                    m.document.file_size = remote.payload.size
                else:
                    m.document.file_size = -1

                messages.append(m)

            # Pyrogram devuelve un objeto si pides uno, o lista si pides varios
            return messages if isinstance(message_ids, list) else messages[0]

        self.mock_client.get_messages.side_effect = side_effect

    def test_system_new_file(self):
        """Validar que un archivo completamente nuevo se detecta como SYSTEM_NEW."""
        source = SourceFile.create(
            path_str="new.mp4", md5sum="1", size=50, mtime=1, mimetype="v"
        )

        job = Job.create_contract(source, self.chat_target, False, 100)

        report = self.discovery.investigate(job)
        self.assertEqual(report.state, AvailabilityState.SYSTEM_NEW)

    def test_remote_mirror_proactive(self):
        """
        Validar que si un archivo ya se subió 100% en el chat de Origen,
        intentar subirlo al chat de Destino se detecta como REMOTE_MIRROR.
        """
        source = SourceFile.create(
            path_str="video.mp4", md5sum="abc", size=50, mtime=1, mimetype="v"
        )

        # Job original
        job_old = Job.create_contract(source, self.chat_source, False, 100)
        p_old = Payload.create(job=job_old, sequence_index=0, md5sum="abc-p1", size=50)
        RemotePayload.create(
            payload=p_old,
            message_id=777,
            chat=self.chat_source,
            owner=self.user,
            json_metadata={},
        )

        # El usuario intenta subir el mismo archivo al mismo chat.
        job_new = Job.create_contract(source, self.chat_target, False, 100)

        self._simulate_jit(success=True)

        report = self.discovery.investigate(job_new)

        self.assertEqual(report.state, AvailabilityState.REMOTE_MIRROR)
        self.assertIsNotNone(report.remotes)
        assert report.remotes is not None and len(report.remotes) > 0
        self.assertEqual(report.remotes[0].message_id, 777)

    def test_remote_puzzle_proactive(self):
        """
        Valida que el sistema pueda sumar piezas dispersas por diferentes
        chats para completar un archivo (REMOTE_PUZZLE).
        """
        # Archivo de 150 bytes, límite de 100 bytes (Se necesitan 2 piezas.)
        source = SourceFile.create(
            path_str="file.zip", md5sum="xyz", size=150, mtime=1, mimetype="v"
        )

        # Subimos Pieza 0 en Chat A
        chat_a = TelegramChat.create(id=300, title="A", type="private")
        job_a = Job.create_contract(source, chat_a, False, 100)
        p0 = Payload.create(job=job_a, sequence_index=0, md5sum="part0", size=100)
        RemotePayload.create(
            payload=p0, message_id=10, chat=chat_a, owner=self.user, json_metadata={}
        )

        # Subimos Pieza 1 en Chat B
        chat_b = TelegramChat.create(id=400, title="B", type="private")
        job_b = Job.create_contract(source, chat_b, False, 100)
        p1 = Payload.create(job=job_b, sequence_index=1, md5sum="part1", size=50)
        RemotePayload.create(
            payload=p1, message_id=20, chat=chat_b, owner=self.user, json_metadata={}
        )

        # Intentamos enviar el archivo original al Chat Target
        job_target = Job.create_contract(source, self.chat_target, False, 100)

        self._simulate_jit(success=True)

        report = self.discovery.investigate(job_target)

        # El sistema debió predecir que faltaban 2 piezas y encontrarlas en distintos chats
        self.assertEqual(report.state, AvailabilityState.REMOTE_PUZZLE)
        self.assertIsNotNone(report.remotes)
        self.assertEqual(len(report.remotes), 2)  # type: ignore

    def test_remote_restricted_jit_fails(self):
        """
        Validar que, aunque la base de datos diga que existe, si la API de Telegram
        nos dice que el mensaje se borró (empty=True), devolvemos REMOTE_RESTRICTED.
        """
        source = SourceFile.create(
            path_str="test.txt", md5sum="abc", size=10, mtime=1, mimetype="t"
        )
        job_old = Job.create_contract(source, self.chat_source, False, 100)
        p = Payload.create(job=job_old, sequence_index=0, md5sum="p", size=10)
        RemotePayload.create(
            payload=p,
            message_id=1,
            chat=self.chat_source,
            owner=self.user,
            json_metadata={},
        )

        job_target = Job.create_contract(source, self.chat_target, False, 100)

        # Telegram responde que el mensaje fue borrado (o no existe)
        self._simulate_jit(success=False)

        report = self.discovery.investigate(job_target)

        self.assertEqual(report.state, AvailabilityState.REMOTE_RESTRICTED)


if __name__ == "__main__":
    unittest.main()
