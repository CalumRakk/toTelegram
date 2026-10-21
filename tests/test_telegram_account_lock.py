import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from pyrogram.errors import RPCError
from pyrogram.types import Message

from totelegram.concurrency import (
    AccountBusyError,
    AccountLockPayload,
    TelegramAccountLock,
)


class TestTelegramAccountLock(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock()
        # Valores por defecto para evitar que devuelvan generadores mockeados
        self.mock_client.search_messages.return_value = []
        self.mock_client.get_chat_history.return_value = []

        self.account_id = 123456789
        self.node_id = "vps-test:101"
        self.lock = TelegramAccountLock(
            client=self.mock_client,
            account_id=self.account_id,
            node_id=self.node_id,
            ttl_seconds=300,
        )

    def _create_fake_message(
        self, message_id: int, text: str, timestamp: int
    ) -> Message:
        # spec=Message hace que isinstance(msg, Message) sea True
        msg = MagicMock(spec=Message)
        msg.id = message_id
        msg.text = text
        msg.date = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        msg.edit_date = None
        return msg

    @patch("time.time", return_value=1000)
    @patch("time.sleep", return_value=None)
    def test_acquire_clean_success(self, mock_sleep, mock_time):
        """Adquisición limpia sin colisiones ni locks previos."""
        self.mock_client.search_messages.return_value = []
        self.mock_client.get_chat_history.return_value = []

        sent_msg = self._create_fake_message(
            message_id=50,
            text="provisional",
            timestamp=1000,
        )
        self.mock_client.send_message.return_value = sent_msg

        own_payload = AccountLockPayload(
            account_id=self.account_id,
            node_id=self.node_id,
            created_at=1000,
            expires_at=1300,
            message_id=50,
        )
        verified_msg = self._create_fake_message(
            message_id=50,
            text=own_payload.to_message_text(),
            timestamp=1000,
        )
        self.mock_client.search_messages.side_effect = [[], [verified_msg]]

        acquired = self.lock.acquire()

        self.assertTrue(self.lock.is_acquired)
        self.assertEqual(acquired.message_id, 50)
        self.assertEqual(acquired.node_id, self.node_id)
        self.mock_client.send_message.assert_called_once()

    @patch("time.time", return_value=1000)
    def test_acquire_fails_when_active_lock_held_by_other_node(self, mock_time):
        """Debe lanzar AccountBusyError si otro nodo tiene un lock vigente."""
        active_payload = AccountLockPayload(
            account_id=self.account_id,
            node_id="other-machine:999",
            created_at=900,
            expires_at=1200,
            message_id=20,
        )
        existing_msg = self._create_fake_message(
            message_id=20,
            text=active_payload.to_message_text(),
            timestamp=900,
        )

        self.mock_client.search_messages.return_value = [existing_msg]

        with self.assertRaises(AccountBusyError) as ctx:
            self.lock.acquire()

        self.assertEqual(ctx.exception.account_id, self.account_id)
        self.assertEqual(ctx.exception.holder_node_id, "other-machine:999")
        self.mock_client.send_message.assert_not_called()

    @patch("time.time", return_value=1500)
    @patch("time.sleep", return_value=None)
    def test_acquire_reclaims_stale_expired_lock(self, mock_sleep, mock_time):
        """Si el lock previo ya expiró, debe limpiarse y adquirirse uno nuevo."""
        expired_payload = AccountLockPayload(
            account_id=self.account_id,
            node_id="dead-node:999",
            created_at=900,
            expires_at=1200,
            message_id=10,
        )
        stale_msg = self._create_fake_message(
            message_id=10,
            text=expired_payload.to_message_text(),
            timestamp=900,
        )

        sent_msg = self._create_fake_message(
            message_id=60,
            text="new_lock",
            timestamp=1500,
        )
        self.mock_client.send_message.return_value = sent_msg

        new_payload = AccountLockPayload(
            account_id=self.account_id,
            node_id=self.node_id,
            created_at=1500,
            expires_at=1800,
            message_id=60,
        )
        new_msg = self._create_fake_message(
            message_id=60,
            text=new_payload.to_message_text(),
            timestamp=1500,
        )

        self.mock_client.search_messages.side_effect = [[stale_msg], [new_msg]]

        acquired = self.lock.acquire()

        self.mock_client.delete_messages.assert_any_call("me", 10)
        self.assertTrue(self.lock.is_acquired)
        self.assertEqual(acquired.message_id, 60)

    @patch("time.time", return_value=1000)
    @patch("time.sleep", return_value=None)
    def test_acquire_loses_race_condition(self, mock_sleep, mock_time):
        """Si dos nodos envían al mismo tiempo, gana el message_id más bajo."""
        sent_msg = self._create_fake_message(
            message_id=41,
            text="provisional",
            timestamp=1000,
        )
        self.mock_client.send_message.return_value = sent_msg

        rival_msg = self._create_fake_message(
            message_id=40,
            text=AccountLockPayload(
                account_id=self.account_id,
                node_id="rival-node:200",
                created_at=1000,
                expires_at=1300,
                message_id=40,
            ).to_message_text(),
            timestamp=1000,
        )
        own_msg = self._create_fake_message(
            message_id=41,
            text=AccountLockPayload(
                account_id=self.account_id,
                node_id=self.node_id,
                created_at=1000,
                expires_at=1300,
                message_id=41,
            ).to_message_text(),
            timestamp=1000,
        )

        self.mock_client.search_messages.side_effect = [
            [],
            [rival_msg, own_msg],
        ]

        with self.assertRaises(AccountBusyError) as ctx:
            self.lock.acquire()

        self.assertEqual(ctx.exception.holder_node_id, "rival-node:200")
        self.mock_client.delete_messages.assert_called_with("me", 41)
        self.assertFalse(self.lock.is_acquired)

    def test_renew_success(self):
        """Renovación periódica del lock vía edit_message_text."""
        self.lock._is_acquired = True
        self.lock._active_payload = AccountLockPayload(
            account_id=self.account_id,
            node_id=self.node_id,
            created_at=1000,
            expires_at=1300,
            message_id=50,
        )

        edited_msg = self._create_fake_message(
            message_id=50,
            text="renewed",
            timestamp=1100,
        )
        self.mock_client.edit_message_text.return_value = edited_msg

        success = self.lock.renew(extra_ttl_seconds=300)

        self.assertTrue(success)
        self.mock_client.edit_message_text.assert_called_once()
        self.assertEqual(self.lock.active_payload.expires_at, 1100 + 300)

    def test_renew_fails_gracefully_on_rpc_error(self):
        """Si el mensaje de lock fue borrado durante la subida, renew retorna False."""
        self.lock._is_acquired = True
        self.lock._active_payload = AccountLockPayload(
            account_id=self.account_id,
            node_id=self.node_id,
            created_at=1000,
            expires_at=1300,
            message_id=50,
        )

        self.mock_client.edit_message_text.side_effect = RPCError("MESSAGE_ID_INVALID")

        success = self.lock.renew(extra_ttl_seconds=300)

        self.assertFalse(success)
        self.assertFalse(self.lock.is_acquired)

    def test_release_deletes_message_and_cleans_state(self):
        """Liberación explícita elimina el mensaje en Telegram."""
        self.lock._is_acquired = True
        self.lock._active_payload = AccountLockPayload(
            account_id=self.account_id,
            node_id=self.node_id,
            created_at=1000,
            expires_at=1300,
            message_id=75,
        )

        self.lock.release()

        self.assertFalse(self.lock.is_acquired)
        self.assertIsNone(self.lock.active_payload)
        self.mock_client.delete_messages.assert_called_once_with("me", 75)


if __name__ == "__main__":
    unittest.main()
