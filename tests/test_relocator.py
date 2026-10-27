import json
import unittest
from unittest.mock import MagicMock

from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.errors import RPCError
from pyrogram.types import Chat, ChatMember, ChatPrivileges

from totelegram.database import DatabaseSession
from totelegram.engine.relocator import ChunkRelocationError, ChunkRelocator
from totelegram.models import (
    Job,
    Payload,
    RemotePayload,
    Source,
    TelegramChat,
    TelegramUser,
)
from totelegram.schemas import SourceType
from totelegram.telegram.access import ChatAccessService
from totelegram.types import PermissionDeniedCleanupError


class TestPermissionsAndRelocator(unittest.TestCase):
    def setUp(self):
        self.db_session = DatabaseSession("sqlite:///:memory:", auto_init_schema=True)
        self.db = self.db_session.start()

        self.mock_client = MagicMock()
        self.access_service = ChatAccessService(self.mock_client)

        self.chat = TelegramChat.create(
            id=-100123456, title="Canal Reubicación", type="channel"
        )
        self.user = TelegramUser.create(
            id=12345, first_name="RelocatorUser", is_premium=False
        )
        self.source = Source.create(
            path_str="/tmp/test_relocate.bin",
            md5sum="hash_relocate_123",
            size=3000,
            mtime=1.0,
            mimetype="application/octet-stream",
            type=SourceType.FILE,
        )
        self.job = Job.create(source=self.source, chat=self.chat, chunk_size=1000)

        self.p0 = Payload.create(
            job=self.job,
            sequence_index=0,
            start_offset=0,
            end_offset=1000,
            size=1000,
            filename="test.bin.01-03",
            filename_short="h.bin.01-03",
        )
        self.p1 = Payload.create(
            job=self.job,
            sequence_index=1,
            start_offset=1000,
            end_offset=2000,
            size=1000,
            filename="test.bin.02-03",
            filename_short="h.bin.02-03",
        )

    def tearDown(self):
        self.db_session.close()

    def _create_mock_message(self, message_id: int):
        msg = MagicMock()
        msg.id = message_id
        msg.chat = MagicMock(id=self.chat.id)
        msg.__str__.return_value = json.dumps(
            {"id": message_id, "chat": {"id": self.chat.id}}
        )
        return msg

    # --- PRUEBAS DE PERMISOS (can_delete_messages) ---

    def test_can_delete_in_private_chat(self):
        """En chats privados siempre se permite borrar."""
        chat_private = MagicMock(spec=Chat, type=ChatType.PRIVATE, id=12345)
        self.assertTrue(self.access_service.can_delete_messages(chat_private))

    def test_can_delete_channel_owner(self):
        """El propietario del canal siempre puede borrar."""
        chat_channel = MagicMock(spec=Chat, type=ChatType.CHANNEL, id=-100111)
        mock_member = MagicMock(spec=ChatMember, status=ChatMemberStatus.OWNER)
        self.mock_client.get_chat_member.return_value = mock_member

        self.assertTrue(self.access_service.can_delete_messages(chat_channel))

    def test_can_delete_channel_admin_with_privilege(self):
        """Administrador con can_delete_messages=True puede borrar."""
        chat_channel = MagicMock(spec=Chat, type=ChatType.CHANNEL, id=-100111)
        privileges = MagicMock(spec=ChatPrivileges, can_delete_messages=True)
        mock_member = MagicMock(
            spec=ChatMember,
            status=ChatMemberStatus.ADMINISTRATOR,
            privileges=privileges,
        )
        self.mock_client.get_chat_member.return_value = mock_member

        self.assertTrue(self.access_service.can_delete_messages(chat_channel))

    def test_cannot_delete_channel_admin_without_privilege(self):
        """Administrador con can_delete_messages=False no puede borrar."""
        chat_channel = MagicMock(spec=Chat, type=ChatType.CHANNEL, id=-100111)
        privileges = MagicMock(spec=ChatPrivileges, can_delete_messages=False)
        mock_member = MagicMock(
            spec=ChatMember,
            status=ChatMemberStatus.ADMINISTRATOR,
            privileges=privileges,
        )
        self.mock_client.get_chat_member.return_value = mock_member

        self.assertFalse(self.access_service.can_delete_messages(chat_channel))

    def test_cannot_delete_regular_member(self):
        """Un miembro normal en canal o supergrupo no puede borrar."""
        chat_channel = MagicMock(spec=Chat, type=ChatType.CHANNEL, id=-100111)
        mock_member = MagicMock(spec=ChatMember, status=ChatMemberStatus.MEMBER)
        self.mock_client.get_chat_member.return_value = mock_member

        self.assertFalse(self.access_service.can_delete_messages(chat_channel))

    # --- PRUEBAS DE CHUNK RELOCATOR (ANTI-WASTE TOC) ---

    def test_relocate_success(self):
        """Copia mensajes al fondo, elimina los viejos y actualiza la DB."""
        r0 = RemotePayload.register_upload(
            self.p0, self._create_mock_message(101), self.user
        )
        r1 = RemotePayload.register_upload(
            self.p1, self._create_mock_message(102), self.user
        )

        msg_new_0 = self._create_mock_message(901)
        msg_new_1 = self._create_mock_message(902)
        self.mock_client.copy_message.side_effect = [msg_new_0, msg_new_1]

        relocated = ChunkRelocator.relocate(
            client=self.mock_client,
            db=self.db,
            chat_id=self.chat.id,
            remotes=[r0, r1],
        )

        self.assertEqual(len(relocated), 2)
        # Verificar que se copiaron en orden
        self.assertEqual(self.mock_client.copy_message.call_count, 2)
        # Verificar que se eliminaron los viejos (101 y 102)
        self.mock_client.delete_messages.assert_called_once_with(
            chat_id=self.chat.id, message_ids=[101, 102]
        )

        # Verificar que la DB se actualizó con los IDs nuevos
        r0_refreshed = RemotePayload.get_by_id(r0.id)
        r1_refreshed = RemotePayload.get_by_id(r1.id)
        self.assertEqual(r0_refreshed.message_id, 901)
        self.assertEqual(r1_refreshed.message_id, 902)

    def test_relocate_copy_failure_executes_rollback(self):
        """Si la copia falla a la mitad, destruye las copias creadas y lanza ChunkRelocationError."""
        r0 = RemotePayload.register_upload(
            self.p0, self._create_mock_message(201), self.user
        )
        r1 = RemotePayload.register_upload(
            self.p1, self._create_mock_message(202), self.user
        )

        msg_new_0 = self._create_mock_message(801)
        # Primera copia OK, segunda copia falla
        self.mock_client.copy_message.side_effect = [msg_new_0, RPCError("FLOOD")]

        with self.assertRaises(ChunkRelocationError):
            ChunkRelocator.relocate(
                client=self.mock_client,
                db=self.db,
                chat_id=self.chat.id,
                remotes=[r0, r1],
            )

        # Rollback: debe haber borrado la copia 801 para no dejar duplicados
        self.mock_client.delete_messages.assert_called_with(
            chat_id=self.chat.id, message_ids=[801]
        )
        # Los registros en DB no deben haberse modificado
        self.assertEqual(RemotePayload.get_by_id(r0.id).message_id, 201)

    def test_relocate_delete_failure_executes_rollback_and_raises_permission_error(
        self,
    ):
        """Si falla el borrado de los viejos, destruye las copias y lanza PermissionDeniedCleanupError."""
        r0 = RemotePayload.register_upload(
            self.p0, self._create_mock_message(301), self.user
        )

        msg_new_0 = self._create_mock_message(701)
        self.mock_client.copy_message.return_value = msg_new_0
        # Simular que delete_messages falla por falta de permisos
        self.mock_client.delete_messages.side_effect = RPCError(
            "MESSAGE_DELETE_FORBIDDEN"
        )

        with self.assertRaises(PermissionDeniedCleanupError) as ctx:
            ChunkRelocator.relocate(
                client=self.mock_client,
                db=self.db,
                chat_id=self.chat.id,
                remotes=[r0],
            )

        self.assertEqual(ctx.exception.chat_id, self.chat.id)
        # Verificamos que intentó deshacer la copia 701
        self.assertEqual(self.mock_client.delete_messages.call_count, 2)
        # El ID original en DB no cambió
        self.assertEqual(RemotePayload.get_by_id(r0.id).message_id, 301)


if __name__ == "__main__":
    unittest.main()
