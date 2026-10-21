import unittest

from totelegram.concurrency import (
    LOCK_ROOT_TAG,
    AccountLockPayload,
)


class TestAccountLockProtocol(unittest.TestCase):
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

    def test_expiration_logic(self):
        payload = AccountLockPayload(
            account_id=111,
            node_id="node-a",
            created_at=1000,
            expires_at=1300,
        )

        # Aún vigente
        self.assertFalse(payload.is_expired(current_time=1200))
        self.assertEqual(payload.remaining_seconds(current_time=1200), 100)

        # Justo en el límite
        self.assertTrue(payload.is_expired(current_time=1300))
        self.assertEqual(payload.remaining_seconds(current_time=1300), 0)

        # Ya vencido
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
        # Texto plano irrelevante
        self.assertIsNone(AccountLockPayload.from_message_text("Hola mundo"))

        # Tag presente pero sin estructura esperada
        self.assertIsNone(
            AccountLockPayload.from_message_text(f"{LOCK_ROOT_TAG} texto suelto")
        )

        # Discrepancia entre el account_id del cuerpo y el tag (#acc_999 != 123)
        tampered_text = (
            "🔒 [toTelegram Account Lock]\n"
            "• Cuenta: 123\n"
            "• Nodo: nodo-x\n"
            "• Creado: 1000\n"
            "• Expira: 1300\n\n"
            "🏷 #totelegram_lock #acc_999"
        )
        self.assertIsNone(AccountLockPayload.from_message_text(tampered_text))


if __name__ == "__main__":
    unittest.main()
