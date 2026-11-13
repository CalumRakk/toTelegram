import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from pyrogram.errors import RPCError
from pyrogram.types import Message

from totelegram.concurrency import (
    LOCK_ROOT_TAG,
    AccountBusyError,
    AccountLockPayload,
    TelegramAccountLock,
    parse_node_id,
)


class TestAccountLockProtocol(unittest.TestCase):
    """Pruebas unitarias de serialización, deserialización y expiración del payload del lock."""

    def test_serialization_and_deserialization_roundtrip(self):
        payload = AccountLockPayload(
            account_id=123456789,
            node_id="vps-frankfurt:9901",
            created_at=1700000000,
            expires_at=1700000300,
            message_id=42,
        )

        text = payload.to_message_text()

        self.assertIn(LOCK_ROOT_TAG, text)
        self.assertIn("#acc_123456789", text)
        self.assertIn("vps-frankfurt:9901", text)

        parsed = AccountLockPayload.from_message_text(text, message_id=42)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.account_id, 123456789)
        self.assertEqual(parsed.node_id, "vps-frankfurt:9901")
        self.assertEqual(parsed.created_at, 1700000000)
        self.assertEqual(parsed.expires_at, 1700000300)
        self.assertEqual(parsed.message_id, 42)

    def test_node_id_parsing_and_properties(self):
        """Verifica la extracción de machine_id y pid."""
        m_id, pid = parse_node_id("my-machine-abc:1234")
        self.assertEqual(m_id, "my-machine-abc")
        self.assertEqual(pid, 1234)

        # Caso sin PID o malformado
        m_id2, pid2 = parse_node_id("legacy-node")
        self.assertEqual(m_id2, "legacy-node")
        self.assertIsNone(pid2)

        payload = AccountLockPayload(
            account_id=111,
            node_id="host-alpha:5555",
            created_at=1000,
            expires_at=1200,
        )
        self.assertEqual(payload.machine_id, "host-alpha")
        self.assertEqual(payload.pid, 5555)

    def test_is_locally_held_and_dead_detection(self):
        """Verifica la detección de procesos huérfanos en la misma máquina."""
        payload = AccountLockPayload(
            account_id=111,
            node_id="local-box:99999",
            created_at=1000,
            expires_at=1200,
        )

        # Si el machine_id coincide y el PID no corre -> True
        with patch("totelegram.concurrency.is_pid_running", return_value=False):
            self.assertTrue(payload.is_locally_held_and_dead("local-box"))

        # Si el machine_id coincide pero el PID sigue vivo -> False
        with patch("totelegram.concurrency.is_pid_running", return_value=True):
            self.assertFalse(payload.is_locally_held_and_dead("local-box"))

        # Si es otra máquina -> False (no podemos saber el estado del PID remoto)
        with patch("totelegram.concurrency.is_pid_running", return_value=False):
            self.assertFalse(payload.is_locally_held_and_dead("other-box"))

    def test_expiration_logic(self):
        payload = AccountLockPayload(
            account_id=111,
            node_id="node-a",
            created_at=1000,
            expires_at=1300,
        )

        self.assertFalse(payload.is_expired(current_time=1200))
        self.assertEqual(payload.remaining_seconds(current_time=1200), 100)
        self.assertTrue(payload.is_expired(current_time=1300))
        self.assertEqual(payload.remaining_seconds(current_time=1300), 0)
        self.assertTrue(payload.is_expired(current_time=1350))
        self.assertEqual(payload.remaining_seconds(current_time=1350), 0)

    def test_ownership_verification(self):
        payload = AccountLockPayload(
            account_id=111,
            node_id="my-pc:123",
            created_at=1000,
            expires_at=1300,
        )

        self.assertTrue(payload.is_held_by("my-pc:123"))
        self.assertFalse(payload.is_held_by("vps-node:456"))

    def test_rejects_corrupted_or_mismatched_text(self):
        self.assertIsNone(AccountLockPayload.from_message_text("Hola mundo"))
        self.assertIsNone(
            AccountLockPayload.from_message_text(f"{LOCK_ROOT_TAG} texto suelto")
        )

        tampered_text = (
            "🔒 [toTelegram Account Lock]\n"
            "• Cuenta: 123\n"
            "• Nodo: nodo-x\n"
            "• Creado: 1000\n"
            "• Expira: 1300\n\n"
            "🏷 #totelegram_lock #acc_999"
        )
        self.assertIsNone(AccountLockPayload.from_message_text(tampered_text))


class TestTelegramAccountLock(unittest.TestCase):
    """Pruebas de adquisición de exclusión mutua en red contra Telegram (Mensajes Guardados)."""

    def setUp(self):
        self.mock_client = MagicMock()
        self.mock_client.search_messages.return_value = []
        self.mock_client.get_chat_history.return_value = []

        self.account_id = 123456789
        self.node_id = "vps-test:101"
        self.lock = TelegramAccountLock(
            client=self.mock_client,
            account_id=self.account_id,
            node_id=self.node_id,
            ttl_seconds=120,
        )

    def _create_fake_message(
        self, message_id: int, text: str, timestamp: int
    ) -> Message:
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
            expires_at=1120,
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
    @patch("time.sleep", return_value=None)
    def test_acquire_reclaims_orphaned_lock_from_dead_pid_same_machine(
        self, mock_sleep, mock_time
    ):
        """
        Si el lock previo fue creado en esta misma máquina pero el proceso
        ya murió (PID inactivo), se debe auto-recuperar inmediatamente sin esperar al TTL.
        """
        dead_pid = 98765
        stale_payload = AccountLockPayload(
            account_id=self.account_id,
            node_id=f"vps-test:{dead_pid}",  # Mismo machine_id ('vps-test')
            created_at=950,
            expires_at=1070,  # Aún le quedaban 70 segundos de TTL
            message_id=25,
        )
        stale_msg = self._create_fake_message(
            message_id=25,
            text=stale_payload.to_message_text(),
            timestamp=950,
        )

        sent_msg = self._create_fake_message(
            message_id=70,
            text="provisional",
            timestamp=1000,
        )
        self.mock_client.send_message.return_value = sent_msg

        new_payload = AccountLockPayload(
            account_id=self.account_id,
            node_id=self.node_id,
            created_at=1000,
            expires_at=1120,
            message_id=70,
        )
        new_msg = self._create_fake_message(
            message_id=70,
            text=new_payload.to_message_text(),
            timestamp=1000,
        )

        # En la primera búsqueda encuentra el lock del PID muerto; luego el nuevo
        self.mock_client.search_messages.side_effect = [[stale_msg], [new_msg]]

        # Simulamos que el PID 98765 ya no existe en el sistema
        with patch(
            "totelegram.concurrency.is_pid_running",
            side_effect=lambda pid: pid != dead_pid,
        ):
            acquired = self.lock.acquire()

        # Debe haber eliminado el lock huérfano 25 y adquirido el nuevo 70
        self.mock_client.delete_messages.assert_any_call("me", 25)
        self.assertTrue(self.lock.is_acquired)
        self.assertEqual(acquired.message_id, 70)

    @patch("time.time", return_value=1000)
    def test_acquire_rejects_active_lock_from_living_pid_same_machine(self, mock_time):
        """
        Si hay un lock en la misma máquina pero el PID sigue corriendo activamente,
        debe lanzar AccountBusyError.
        """
        living_pid = 4321
        active_payload = AccountLockPayload(
            account_id=self.account_id,
            node_id=f"vps-test:{living_pid}",
            created_at=950,
            expires_at=1070,
            message_id=30,
        )
        active_msg = self._create_fake_message(
            message_id=30,
            text=active_payload.to_message_text(),
            timestamp=950,
        )
        self.mock_client.search_messages.return_value = [active_msg]

        with (
            patch("totelegram.concurrency.is_pid_running", return_value=True),
            self.assertRaises(AccountBusyError) as ctx,
        ):
            self.lock.acquire()

        self.assertEqual(ctx.exception.account_id, self.account_id)
        self.assertEqual(ctx.exception.holder_node_id, f"vps-test:{living_pid}")
        self.mock_client.send_message.assert_not_called()

    @patch("time.time", return_value=1000)
    @patch("time.sleep", return_value=None)
    def test_acquire_with_force_breaks_lock_from_other_node(
        self, mock_sleep, mock_time
    ):
        """
        Con force=True, un lock no expirado de otra máquina debe ser invalidado
        y reemplazado exitosamente.
        """
        other_node_payload = AccountLockPayload(
            account_id=self.account_id,
            node_id="other-machine:9999",
            created_at=950,
            expires_at=1070,
            message_id=15,
        )
        existing_msg = self._create_fake_message(
            message_id=15,
            text=other_node_payload.to_message_text(),
            timestamp=950,
        )

        sent_msg = self._create_fake_message(
            message_id=80,
            text="provisional",
            timestamp=1000,
        )
        self.mock_client.send_message.return_value = sent_msg

        new_payload = AccountLockPayload(
            account_id=self.account_id,
            node_id=self.node_id,
            created_at=1000,
            expires_at=1120,
            message_id=80,
        )
        new_msg = self._create_fake_message(
            message_id=80,
            text=new_payload.to_message_text(),
            timestamp=1000,
        )

        self.mock_client.search_messages.side_effect = [[existing_msg], [new_msg]]

        acquired = self.lock.acquire(force=True)

        self.mock_client.delete_messages.assert_any_call("me", 15)
        self.assertTrue(self.lock.is_acquired)
        self.assertEqual(acquired.message_id, 80)

    @patch("time.time", return_value=1000)
    def test_acquire_fails_when_active_lock_held_by_other_node(self, mock_time):
        """Sin force=True, un lock activo de otra máquina lanza AccountBusyError."""
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
            self.lock.acquire(force=False)

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
            expires_at=1620,
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
                expires_at=1120,
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
                expires_at=1120,
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
            expires_at=1120,
            message_id=50,
        )

        edited_msg = self._create_fake_message(
            message_id=50,
            text="renewed",
            timestamp=1100,
        )
        self.mock_client.edit_message_text.return_value = edited_msg

        success = self.lock.renew(extra_ttl_seconds=120)

        self.assertTrue(success)
        self.mock_client.edit_message_text.assert_called_once()
        self.assertEqual(self.lock.active_payload.expires_at, 1100 + 120)

    def test_renew_fails_gracefully_on_rpc_error(self):
        """Si el mensaje de lock fue borrado durante la subida, renew retorna False."""
        self.lock._is_acquired = True
        self.lock._active_payload = AccountLockPayload(
            account_id=self.account_id,
            node_id=self.node_id,
            created_at=1000,
            expires_at=1120,
            message_id=50,
        )

        self.mock_client.edit_message_text.side_effect = RPCError("MESSAGE_ID_INVALID")

        success = self.lock.renew(extra_ttl_seconds=120)

        self.assertFalse(success)
        self.assertFalse(self.lock.is_acquired)

    def test_release_deletes_message_and_cleans_state(self):
        """Liberación explícita elimina el mensaje en Telegram."""
        self.lock._is_acquired = True
        self.lock._active_payload = AccountLockPayload(
            account_id=self.account_id,
            node_id=self.node_id,
            created_at=1000,
            expires_at=1120,
            message_id=75,
        )

        self.lock.release()

        self.assertFalse(self.lock.is_acquired)
        self.assertIsNone(self.lock.active_payload)
        self.mock_client.delete_messages.assert_called_once_with("me", 75)


if __name__ == "__main__":
    unittest.main()
