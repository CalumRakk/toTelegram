import json
import time
import unittest
from datetime import datetime, timedelta, timezone
from typing import cast
from unittest.mock import MagicMock

from totelegram.concurrency import (
    AccountBusyError,
    ConcurrencyCoordinator,
    LeaseHeartbeat,
    PayloadClaim,
)
from totelegram.database import DatabaseSession
from totelegram.models import (
    Claim,
    Job,
    Payload,
    RemotePayload,
    Source,
    TelegramChat,
    TelegramUser,
)
from totelegram.schemas import (
    JobStatus,
    ResourceType,
    SourceType,
    Strategy,
    StrategyConfig,
)


class TestConcurrencyCoordinator(unittest.TestCase):
    def setUp(self):
        # Base de datos SQLite en memoria aislada para tests
        self.db_session = DatabaseSession("sqlite:///:memory:", auto_init_schema=True)
        self.db = self.db_session.start()

        # Nodos simulados (ej. dos terminales ejecutándose a la vez)
        self.node_a = "node_worker_alpha"
        self.node_b = "node_worker_beta"

        self.coord_a = ConcurrencyCoordinator(self.db, node_id=self.node_a)
        self.coord_b = ConcurrencyCoordinator(self.db, node_id=self.node_b)

        # Fixtures base
        self.chat = TelegramChat.create(
            id=-100111222333, title="Canal Concurrencia", type="channel"
        )
        self.user = TelegramUser.create(
            id=123456789, first_name="WorkerUser", is_premium=False
        )
        self.source = Source.create(
            path_str="/tmp/test_file.iso",
            md5sum="hash_iso_unique_concurrency",
            size=3000,
            mtime=1700000000.0,
            mimetype="application/octet-stream",
            type=SourceType.FILE,
        )
        self.job = Job.create(
            source=self.source,
            chat=self.chat,
            strategy=Strategy.CHUNKED,
            status=JobStatus.PENDING,
            config=StrategyConfig(
                tg_max_size=1000, user_is_premium=False, app_version="0.9.15"
            ),
        )

        # Crear 3 piezas para el Job (seq 0, 1, 2)
        self.p0 = Payload.create(
            job=self.job,
            sequence_index=0,
            start_offset=0,
            end_offset=1000,
            size=1000,
            filename="file.iso.01-03",
            filename_short="h.iso.01-03",
        )
        self.p1 = Payload.create(
            job=self.job,
            sequence_index=1,
            start_offset=1000,
            end_offset=2000,
            size=1000,
            filename="file.iso.02-03",
            filename_short="h.iso.02-03",
        )
        self.p2 = Payload.create(
            job=self.job,
            sequence_index=2,
            start_offset=2000,
            end_offset=3000,
            size=1000,
            filename="file.iso.03-03",
            filename_short="h.iso.03-03",
        )

    def tearDown(self):
        self.db_session.close()

    def _create_mock_message(self, message_id: int):
        msg = MagicMock()
        msg.id = message_id
        msg.chat = MagicMock(id=self.chat.id)
        msg.__str__.return_value = json.dumps(  # type: ignore
            {"id": message_id, "chat": {"id": self.chat.id}}
        )
        return msg

    # PRUEBAS DE EXCLUSIÓN MUTUA DE CUENTA (guard_account)

    def test_guard_account_lifecycle(self):
        """Valida que guard_account adquiera el lease y lo libere al salir del bloque."""
        account_id = self.user.id
        resource_id = f"account:{account_id}"

        self.assertIsNone(Claim.get_or_none(Claim.resource_id == resource_id))

        with self.coord_a.guard_account(account_id, ttl_seconds=60) as res:
            self.assertEqual(res, resource_id)

            # Verificar que el registro existe en la DB
            claim = cast(Claim, Claim.get_or_none(Claim.resource_id == resource_id))
            self.assertIsNotNone(claim)
            self.assertEqual(claim.node_id, self.node_a)
            self.assertEqual(claim.resource_type, ResourceType.ACCOUNT)

        # Al salir del bloque `with`, debe haberse eliminado
        self.assertIsNone(Claim.get_or_none(Claim.resource_id == resource_id))

    def test_guard_account_collision_raises_busy_error(self):
        """Si un nodo tiene bloqueada una cuenta, otro nodo debe recibir AccountBusyError."""
        account_id = self.user.id

        with self.coord_a.guard_account(account_id, ttl_seconds=60):
            # El nodo B intenta usar la misma cuenta mientras A la tiene activa
            with self.assertRaises(AccountBusyError) as ctx:
                with self.coord_b.guard_account(account_id):
                    pass

            self.assertEqual(ctx.exception.account_id, account_id)
            self.assertEqual(ctx.exception.holder_node_id, self.node_a)

    def test_guard_account_anonymous_noop(self):
        """Si account_id es None, guard_account debe operar sin crear claims en DB."""
        with self.coord_a.guard_account(account_id=None) as res:
            self.assertEqual(res, "account:anonymous")

        self.assertEqual(Claim.select().count(), 0)

    # PRUEBAS DE EXPIRACIÓN Y RECUPERACIÓN DE LEASES (TTL)

    def test_lease_expiration_and_recovery_by_another_node(self):
        """Un lease expirado de Node A debe poder ser reclamado/sobreescrito por Node B."""
        resource_id = f"account:{self.user.id}"

        # Crear un claim manualmente ya vencido en el pasado
        expired_time = datetime.now(timezone.utc) - timedelta(seconds=120)
        Claim.create(
            resource_id=resource_id,
            resource_type=ResourceType.ACCOUNT,
            node_id=self.node_a,
            expires_at=expired_time,
        )

        # Node B debe ser capaz de tomar posesión del recurso porque el de A expiró
        with self.coord_b.guard_account(self.user.id, ttl_seconds=100) as res:
            self.assertEqual(res, resource_id)
            claim = Claim.get(Claim.resource_id == resource_id)
            self.assertEqual(claim.node_id, self.node_b)
            self.assertGreater(claim.expires_at, datetime.now(timezone.utc))

    def test_same_node_can_reacquire_or_extend_its_own_lease(self):
        """Un nodo puede volver a llamar _acquire_resource sobre su propio lease para extenderlo."""
        res_id = "test_custom_resource"
        self.assertTrue(
            self.coord_a._acquire_resource(res_id, ResourceType.PAYLOAD, ttl_seconds=30)
        )

        claim_1 = Claim.get(Claim.resource_id == res_id)
        original_expires = claim_1.expires_at

        # El mismo nodo vuelve a adquirir con mayor TTL
        time.sleep(0.01)
        self.assertTrue(
            self.coord_a._acquire_resource(res_id, ResourceType.PAYLOAD, ttl_seconds=90)
        )

        claim_2 = Claim.get(Claim.resource_id == res_id)
        self.assertEqual(claim_2.node_id, self.node_a)
        self.assertGreater(claim_2.expires_at, original_expires)

    def test_renew_resources_batch(self):
        """Valida la renovación de vigencia en lote de múltiples recursos."""
        res_ids = ["payload:101", "payload:102", f"account:{self.user.id}"]

        for rid in res_ids:
            self.coord_a._acquire_resource(rid, ResourceType.PAYLOAD, ttl_seconds=10)

        old_claims = {c.resource_id: c.expires_at for c in Claim.select()}

        time.sleep(0.01)
        success = self.coord_a.renew_resources(res_ids, ttl_seconds=300)
        self.assertTrue(success)

        for claim in Claim.select():
            self.assertGreater(claim.expires_at, old_claims[claim.resource_id])

        # Node B intenta renovar recursos pertenecientes a Node A -> debe fallar (retorna False)
        self.assertFalse(self.coord_b.renew_resources(res_ids))

    # PRUEBAS DE RECLAMO COOPERATIVO DE PIEZAS (claim_next_payload)

    def test_claim_next_payload_sequential_distribution(self):
        """Distribuye secuencialmente piezas entre diferentes workers concurrentes."""
        # Worker A reclama -> debe obtener la pieza seq 0
        with self.coord_a.claim_next_payload(self.job, self.user.id) as claim_a:
            claim_a = cast(PayloadClaim, claim_a)
            self.assertIsNotNone(claim_a)
            self.assertEqual(claim_a.payload.id, self.p0.id)
            self.assertEqual(claim_a.payload.sequence_index, 0)

            # Mientras A procesa la pieza 0, Worker B reclama -> debe obtener la pieza seq 1
            with self.coord_b.claim_next_payload(self.job, self.user.id) as claim_b:
                claim_b = cast(PayloadClaim, claim_b)
                self.assertIsNotNone(claim_b)
                self.assertEqual(claim_b.payload.id, self.p1.id)
                self.assertEqual(claim_b.payload.sequence_index, 1)

                # Worker C reclama -> debe obtener la pieza seq 2
                coord_c = ConcurrencyCoordinator(self.db, node_id="worker_c")
                with coord_c.claim_next_payload(self.job, self.user.id) as claim_c:
                    claim_c = cast(PayloadClaim, claim_c)
                    self.assertIsNotNone(claim_c)
                    self.assertEqual(claim_c.payload.id, self.p2.id)

                    # Worker D reclama -> Todas están bloqueadas concurrentemente, debe retornar None
                    coord_d = ConcurrencyCoordinator(self.db, node_id="worker_d")
                    with coord_d.claim_next_payload(self.job, self.user.id) as claim_d:
                        self.assertIsNone(claim_d)

    def test_claim_next_payload_skips_fulfilled_and_reclaims_orphaned(self):
        """
        No debe reclamar piezas con RemotePayload activo (no huérfano),
        pero SÍ debe reclamar piezas que quedaron huérfanas.
        """
        # Marcamos la pieza 0 como subida exitosamente (RemotePayload válido)
        msg0 = self._create_mock_message(1001)
        RemotePayload.register_upload(self.p0, msg0, self.user)

        # Marcamos la pieza 1 como huérfana (falló o se borró el mensaje en Telegram)
        msg1 = self._create_mock_message(1002)
        remote_orphaned = RemotePayload.register_upload(self.p1, msg1, self.user)
        remote_orphaned.mark_orphaned()

        # Worker A reclama:
        # - Debe saltar la pieza 0 (ya está en Telegram)
        # - Debe reclamar la pieza 1 (está huérfana, requiere re-subida)
        with self.coord_a.claim_next_payload(self.job, self.user.id) as claim:
            claim = cast(PayloadClaim, claim)
            self.assertIsNotNone(claim)
            self.assertEqual(claim.payload.id, self.p1.id)

    def test_claim_next_payload_returns_none_when_all_uploaded(self):
        """Si todas las piezas tienen RemotePayload válido, devuelve None."""
        msg0 = self._create_mock_message(2001)
        msg1 = self._create_mock_message(2002)
        msg2 = self._create_mock_message(2003)

        RemotePayload.register_upload(self.p0, msg0, self.user)
        RemotePayload.register_upload(self.p1, msg1, self.user)
        RemotePayload.register_upload(self.p2, msg2, self.user)

        with self.coord_a.claim_next_payload(self.job, self.user.id) as claim:
            self.assertIsNone(claim)

    # PRUEBAS DEL CONTROLADOR DE LATIDOS (LeaseHeartbeat)

    def test_lease_heartbeat_throttling(self):
        """Verifica que pulse() no sature la DB y solo renueve tras expirar el intervalo."""
        res_id = "test_pulse_res"
        self.coord_a._acquire_resource(res_id, ResourceType.PAYLOAD, ttl_seconds=60)

        heartbeat = LeaseHeartbeat(
            coordinator=self.coord_a,
            tracked_resources=[res_id],
            renew_interval_seconds=10,
        )

        initial_pulse_time = heartbeat.last_pulse

        # Llamada inmediata: tiempo transcurrido < 10s -> No debe renovar
        result_quick = heartbeat.pulse()
        self.assertTrue(result_quick)
        self.assertEqual(heartbeat.last_pulse, initial_pulse_time)

        # Forzamos avance del reloj interno
        heartbeat.last_pulse = time.monotonic() - 15

        # Llamada con tiempo vencido -> Debe disparar renovación
        result_renewed = heartbeat.pulse()
        self.assertTrue(result_renewed)
        self.assertGreater(heartbeat.last_pulse, initial_pulse_time)

    def test_lease_heartbeat_sleep_and_tick_callback(self):
        """Valida que sleep() mantenga vivos los leases y reporte el progreso segundo a segundo."""
        res_id = "test_sleep_res"
        self.coord_a._acquire_resource(res_id, ResourceType.ACCOUNT, ttl_seconds=60)

        heartbeat = LeaseHeartbeat(
            coordinator=self.coord_a,
            tracked_resources=[res_id],
            renew_interval_seconds=1,
        )

        ticks_recorded = []

        def on_tick_callback(remaining, total):
            ticks_recorded.append((remaining, total))

        # Pausa activa de 2 segundos (usando step de 1 segundo)
        heartbeat.sleep(total_seconds=2, step_seconds=1, on_tick=on_tick_callback)

        self.assertEqual(len(ticks_recorded), 2)
        self.assertEqual(ticks_recorded[0], (1, 2))
        self.assertEqual(ticks_recorded[1], (0, 2))


if __name__ == "__main__":
    unittest.main()
