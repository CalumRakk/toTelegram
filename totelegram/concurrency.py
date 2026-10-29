# pyright: reportUnusedCoroutine=false
import atexit
import logging
import re
import signal
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta, timezone
from typing import TYPE_CHECKING, Generator, List, Optional, cast

import peewee
from pyrogram.errors import RPCError

from totelegram.common.helpers import get_utc_now, is_expired
from totelegram.exceptions import (
    AccountBusyError,
    ConcurrencyError,
    SystemSuspendedError,
)
from totelegram.models import Claim, Job, Payload, RemotePayload
from totelegram.schemas import ResourceType
from totelegram.telegram.search import find_messages_with_history_fallback

if TYPE_CHECKING:
    from pyrogram.client import Client


logger = logging.getLogger(__name__)


# Constantes del protocolo de bloqueo distribuido
LOCK_ROOT_TAG = "#totelegram_lock"
DEFAULT_LOCK_TTL_SECONDS = 300  # 5 minutos

# DTOs Y CONTROLADOR DE LATIDOS (HEARTBEAT)


@dataclass
class LeaseHeartbeat:
    """
    Controlador de latidos para mantener vivos los leases activos.
    Se conecta naturalmente tanto a callbacks de Pyrogram como a bucles de espera.
    """

    coordinator: "ConcurrencyCoordinator"
    tracked_resources: List[str]
    renew_interval_seconds: int = 60
    last_pulse: float = field(default_factory=time.monotonic)

    def pulse(self, current: int = 0, total: int = 0, *args, **kwargs) -> bool:
        """
        Latido reactivo llamado durante la transferencia de bytes (progreso).
        Aplica throttling interno para no saturar la base de datos con renovaciones innecesarias.
        """
        now = time.monotonic()
        if now - self.last_pulse >= self.renew_interval_seconds:
            success = self.coordinator.renew_resources(self.tracked_resources)
            if success:
                self.last_pulse = now
            return success
        return True

    def sleep(self, total_seconds: int, step_seconds: int = 1, on_tick=None):
        """
        Pausa activa que mantiene vivos los leases en DB durante pausas programadas o FloodWait.
        """
        if total_seconds <= 0:
            return

        remaining = total_seconds
        while remaining > 0:
            chunk_start = time.time()
            sleep_chunk = min(remaining, step_seconds)
            time.sleep(sleep_chunk)
            chunk_elapsed = time.time() - chunk_start

            # Si pedimos dormir 1 segundo pero pasaron más de 15 segundos reales,
            # significa inequívocamente que la máquina se suspendió/hibernó.
            if chunk_elapsed > (sleep_chunk + 10):
                logger.warning(
                    f"Detectada suspensión del sistema: se esperaban {sleep_chunk}s "
                    f"pero transcurrieron {chunk_elapsed:.1f}s."
                )
                raise SystemSuspendedError(
                    "El sistema operativo se suspendió o hibernó durante la ejecución. "
                    "Abortando para proteger la integridad de los datos y reconectar limpiamente."
                )

            remaining -= sleep_chunk

            # Si renovar el lease falla porque la conexión a DB murió, no continuar
            if not self.pulse():
                raise ConcurrencyError(
                    "Se perdió la conexión con la base de datos o el lease expiró."
                )

            if on_tick:
                on_tick(remaining, total_seconds)


@dataclass
class PayloadClaim:
    """Representa el reclamo exclusivo de una pieza para su procesamiento."""

    payload: Payload
    resource_id: str
    heartbeat: LeaseHeartbeat


class ConcurrencyCoordinator:
    """
    Fuente de verdad centralizada para la exclusión mutua y leases en toTelegram.
    Maneja la exclusión global de cuenta en Telegram y leases locales en SQLite.
    """

    def __init__(
        self,
        db: peewee.Database,
        node_id: str,
        client: Optional["Client"] = None,
    ):
        self.db = db
        self.node_id = node_id
        self.client = client
        self._active_account_lock: TelegramAccountLock | None = None

    def _acquire_resource(
        self,
        resource_id: str,
        resource_type: ResourceType,
        ttl_seconds: int,
    ) -> bool:
        """Intenta adquirir o recuperar atómicamente un lease para un recurso."""
        expires_at = get_utc_now() + timedelta(seconds=ttl_seconds)

        try:
            with self.db.atomic():
                Claim.create(
                    resource_id=resource_id,
                    resource_type=resource_type,
                    node_id=self.node_id,
                    expires_at=expires_at,
                )
                logger.debug(f"Lease creado: {resource_id} [Nodo: {self.node_id}]")
                return True
        except peewee.IntegrityError:
            # Colisión: verificar si el claim existente pertenece a este nodo o ya expiró
            claim = cast(Claim, Claim.get_or_none(Claim.resource_id == resource_id))
            if claim and is_expired(claim.expires_at):
                claim.node_id = self.node_id
                claim.expires_at = expires_at
                claim.save(only=[Claim.node_id, Claim.expires_at, Claim.updated_at])
                logger.debug(f"Lease expirado recuperado: {resource_id}")
                return True

            return False

    def renew_resources(self, resource_ids: List[str], ttl_seconds: int = 300) -> bool:
        """
        Renueva tanto el lock global de la cuenta en Telegram
        como los leases de piezas en la base de datos local.
        """
        # Renovar el lock de la cuenta en Telegram
        if self._active_account_lock is not None:
            renewed_tg = self._active_account_lock.renew(extra_ttl_seconds=ttl_seconds)
            if not renewed_tg:
                logger.error(
                    "Se perdió el lock de la cuenta en Telegram durante la subida."
                )
                return False

        # Renovar los leases de piezas locales (payload:xxx) en SQLite
        payload_resources = [r for r in resource_ids if not r.startswith("account:")]
        if not payload_resources:
            return True

        new_expires_at = get_utc_now() + timedelta(seconds=ttl_seconds)
        try:
            with self.db.atomic():
                updated = (
                    Claim.update(expires_at=new_expires_at)
                    .where(
                        (Claim.resource_id.in_(payload_resources))
                        & (Claim.node_id == self.node_id)
                    )
                    .execute()
                )
                return updated > 0
        except Exception as e:
            logger.error(f"Fallo al renovar leases locales {payload_resources}: {e}")
            return False

    def release_resource(self, resource_id: str) -> None:
        """Libera de forma explícita un recurso adquirido por este nodo."""
        try:
            Claim.delete().where(
                (Claim.resource_id == resource_id) & (Claim.node_id == self.node_id)
            ).execute()
            logger.debug(f"Lease liberado: {resource_id}")
        except Exception as e:
            logger.warning(f"Error liberando lease {resource_id}: {e}")

    # CONTEXT MANAGERS DECLARATIVOS

    @contextmanager
    def guard_account(
        self, account_id: int | None, ttl_seconds: int = 300
    ) -> Generator[str, None, None]:
        if not account_id:
            yield "account:anonymous"
            return

        resource_id = f"account:{account_id}"
        if not self.client:
            raise RuntimeError(
                "Se requiere un cliente de Telegram activo para bloquear la cuenta."
            )

        # Si ya fue adquirido por este mismo coordinador, reutilizamos de forma reentrante
        if (
            self._active_account_lock is not None
            and self._active_account_lock.is_acquired
        ):
            yield resource_id
            return

        tg_lock = TelegramAccountLock(
            client=self.client,
            account_id=account_id,
            node_id=self.node_id,
            ttl_seconds=ttl_seconds,
        )
        tg_lock.acquire()
        self._active_account_lock = tg_lock
        try:
            yield resource_id
        finally:
            self._active_account_lock = None
            tg_lock.release()
        return

    @contextmanager
    def claim_next_payload(
        self,
        job: Job,
        account_id: int | None = None,
        ttl_seconds: int = 300,
    ) -> Generator[PayloadClaim | None, None, None]:
        """
        Busca y bloquea atómicamente la siguiente pieza libre de un Job.
        Al salir del bloque `with`, el lease de la pieza se libera automáticamente.
        Devuelve `None` si el Job ya no tiene piezas pendientes o si todas las restantes
        están siendo procesadas concurrentemente por otras terminales.
        """
        # Subconsulta de piezas que ya tienen un RemotePayload válido (no huérfano)
        fulfilled_ids = [
            r.payload_id
            for r in RemotePayload.select(RemotePayload.payload_id).where(
                RemotePayload.is_orphaned == False
            )
        ]

        # Obtener candidatos pendientes ordenados por secuencia
        query = (
            Payload.select()
            .where((Payload.job == job) & (~Payload.id.in_(fulfilled_ids)))  # type: ignore
            .order_by(Payload.sequence_index)
        )

        candidates = list(query)
        selected_payload: Payload | None = None
        selected_resource_id: str | None = None

        # Reclamo atómico: intentar adquirir la primera pieza disponible
        for candidate in candidates:
            res_id = f"payload:{candidate.id}"
            if self._acquire_resource(
                resource_id=res_id,
                resource_type=ResourceType.PAYLOAD,
                ttl_seconds=ttl_seconds,
            ):
                selected_payload = candidate
                selected_resource_id = res_id
                break

        if selected_payload is None or selected_resource_id is None:
            yield None
            return

        # Crear el heartbeat agrupado (cuenta + payload)
        tracked = [selected_resource_id]
        if account_id:
            tracked.append(f"account:{account_id}")

        heartbeat = LeaseHeartbeat(
            coordinator=self,
            tracked_resources=[selected_resource_id],
            renew_interval_seconds=60,
        )
        claim_obj = PayloadClaim(
            payload=selected_payload,
            resource_id=selected_resource_id,
            heartbeat=heartbeat,
        )

        try:
            yield claim_obj
        finally:
            self.release_resource(selected_resource_id)


@dataclass(frozen=True)
class AccountLockPayload:
    """
    Representa los metadatos serializados del bloqueo de cuenta en Telegram.
    Diseñado para ser inmutable y desacoplado de las llamadas de red.
    """

    account_id: int
    node_id: str
    created_at: int  # Unix timestamp UTC
    expires_at: int  # Unix timestamp UTC
    message_id: int | None = None

    # Patrón estricto para extraer metadatos del texto del mensaje
    _REGEX_PATTERN = re.compile(
        r"🔒\s*\[toTelegram Account Lock\]\s*"
        r"•\s*Cuenta:\s*(?P<account_id>\d+)\s*"
        r"•\s*Nodo:\s*(?P<node_id>[^\n\r]+)\s*"
        r"•\s*Creado:\s*(?P<created_at>\d+)\s*"
        r"•\s*Expira:\s*(?P<expires_at>\d+)\s*"
        r".*?#totelegram_lock.*?#acc_(?P<tag_account_id>\d+)",
        re.DOTALL,
    )

    def to_message_text(self) -> str:
        """Serializa el bloqueo al formato visual y técnico de Telegram."""
        return (
            "🔒 [toTelegram Account Lock]\n"
            f"• Cuenta: {self.account_id}\n"
            f"• Nodo: {self.node_id}\n"
            f"• Creado: {self.created_at}\n"
            f"• Expira: {self.expires_at}\n\n"
            f"🏷 {LOCK_ROOT_TAG} #acc_{self.account_id}"
        )

    @classmethod
    def from_message_text(
        cls, text: str, message_id: int | None = None
    ) -> Optional["AccountLockPayload"]:
        """
        Deserializa el texto de un mensaje de Telegram.
        Retorna None si el formato es inválido o no corresponde a un lock legítimo.
        """
        if not text or LOCK_ROOT_TAG not in text:
            return None

        match = cls._REGEX_PATTERN.search(text)
        if not match:
            return None

        data = match.groupdict()
        acc_id = int(data["account_id"])
        tag_acc_id = int(data["tag_account_id"])

        # Validación cruzada: el ID del cuerpo debe coincidir con el tag
        if acc_id != tag_acc_id:
            return None

        return cls(
            account_id=acc_id,
            node_id=data["node_id"].strip(),
            created_at=int(data["created_at"]),
            expires_at=int(data["expires_at"]),
            message_id=message_id,
        )

    def is_expired(self, current_time: int) -> bool:
        """Determina si el bloqueo ha expirado contra un timestamp de referencia."""
        return current_time >= self.expires_at

    def is_held_by(self, node_id: str) -> bool:
        """Comprueba si el bloqueo pertenece a un nodo específico."""
        return self.node_id == node_id

    def remaining_seconds(self, current_time: int) -> int:
        """Retorna los segundos restantes de vigencia (0 si ya expiró)."""
        return max(0, self.expires_at - current_time)


class TelegramAccountLock:
    """
    Gestor de exclusión mutua distribuida respaldado en 'Mensajes Guardados' de Telegram.
    Garantiza que una cuenta solo procese una subida a la vez entre múltiples máquinas (PC, VPS).
    """

    DEFAULT_TTL_SECONDS = 300  # 5 minutos de validez por defecto
    RENEW_INTERVAL_SECONDS = 60  # Latido cada 60 segundos

    def __init__(
        self,
        client: "Client",
        account_id: int,
        node_id: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ):
        self.client = client
        self.account_id = account_id
        self.node_id = node_id
        self.ttl_seconds = ttl_seconds

        self._active_payload: AccountLockPayload | None = None
        self._is_acquired = False

    @property
    def is_acquired(self) -> bool:
        return self._is_acquired

    @property
    def active_payload(self) -> AccountLockPayload | None:
        return self._active_payload

    def _get_server_timestamp(self, message) -> int:
        """Extrae el timestamp UTC oficial del servidor de Telegram."""
        dt = message.edit_date or message.date
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())

    def _find_locks_in_saved_messages(self) -> list[AccountLockPayload]:
        query = f"{LOCK_ROOT_TAG} #acc_{self.account_id}"

        messages = find_messages_with_history_fallback(
            client=self.client,
            chat_id="me",
            query=query,
            search_limit=10,
            history_limit=5,
        )

        found: list[AccountLockPayload] = []
        for msg in messages:
            payload = AccountLockPayload.from_message_text(msg.text, msg.id)
            if payload and payload.account_id == self.account_id:
                found.append(payload)

        return sorted(found, key=lambda x: x.message_id or 0)

    def acquire(self) -> AccountLockPayload:
        """
        Intenta adquirir el lock con resolución de carreras Read-After-Write.
        Lanza AccountBusyError si otro nodo mantiene un bloqueo activo.
        """
        from pyrogram.types import Message

        current_time = int(time.time())
        existing_locks = self._find_locks_in_saved_messages()

        # Comprobar si hay un lock vigente ocupado por otro nodo
        for lock in existing_locks:
            if not lock.is_expired(current_time):
                if not lock.is_held_by(self.node_id):
                    logger.info(
                        f"Cuenta {self.account_id} ocupada por nodo '{lock.node_id}'. "
                        f"Expira en {lock.remaining_seconds(current_time)}s."
                    )
                    raise AccountBusyError(self.account_id, lock.node_id)
                else:
                    # El lock pertenece a este mismo nodo (re-adquisición o reanudación limpia)
                    self._active_payload = lock
                    self._is_acquired = True
                    return lock

            # Si está expirado, intentar limpiarlo silenciosamente
            if lock.message_id:
                try:
                    self.client.delete_messages("me", lock.message_id)
                except RPCError:
                    pass

        # Enviar el mensaje provisional de reclamo
        provisional_payload = AccountLockPayload(
            account_id=self.account_id,
            node_id=self.node_id,
            created_at=current_time,
            expires_at=current_time + self.ttl_seconds,
        )

        sent_msg = self.client.send_message(
            chat_id="me",
            text=provisional_payload.to_message_text(),
        )

        assert isinstance(sent_msg, Message)

        # Sincronizamos los timestamps con la hora real del servidor de Telegram
        server_created_at = self._get_server_timestamp(sent_msg)
        official_payload = AccountLockPayload(
            account_id=self.account_id,
            node_id=self.node_id,
            created_at=server_created_at,
            expires_at=server_created_at + self.ttl_seconds,
            message_id=sent_msg.id,
        )

        # Actualizamos el texto del mensaje con los timestamps exactos del servidor
        try:
            self.client.edit_message_text(
                chat_id="me",
                message_id=sent_msg.id,
                text=official_payload.to_message_text(),
            )
        except RPCError:
            pass

        # Consenso Anti-Carrera: Read-After-Write
        # Pequeña pausa para permitir que mensajes concurrentes se asienten en el servidor
        time.sleep(0.4)

        active_locks = [
            lock
            for lock in self._find_locks_in_saved_messages()
            if not lock.is_expired(current_time)
        ]
        if active_locks:
            winner = active_locks[0]
            if winner.message_id != sent_msg.id:
                # Perdimos la carrera: otro nodo llegó antes
                logger.warning(
                    f"Conflicto de concurrencia: el nodo '{winner.node_id}' ganó el lock "
                    f"(Msg {winner.message_id} < {sent_msg.id}). Liberando mensaje perdedor."
                )
                try:
                    self.client.delete_messages("me", sent_msg.id)
                except RPCError:
                    pass

                raise AccountBusyError(self.account_id, winner.node_id)

        self._active_payload = official_payload
        self._is_acquired = True
        _GLOBAL_ACTIVE_LOCKS.add(self)
        logger.debug(
            f"Lock de cuenta adquirido con éxito: {official_payload.node_id} [Msg {official_payload.message_id}]"
        )
        return official_payload

    def renew(self, extra_ttl_seconds: int | None = None) -> bool:
        """
        Renueva la vigencia del lock extendiendo su tiempo de expiración.
        Retorna True si la renovación fue exitosa, o False si el lock fue alterado o eliminado.
        """
        if (
            not self._is_acquired
            or not self._active_payload
            or not self._active_payload.message_id
        ):
            return False

        extension = extra_ttl_seconds or self.ttl_seconds
        new_expires_at = int(time.time()) + extension

        updated_payload = AccountLockPayload(
            account_id=self._active_payload.account_id,
            node_id=self._active_payload.node_id,
            created_at=self._active_payload.created_at,
            expires_at=new_expires_at,
            message_id=self._active_payload.message_id,
        )

        try:
            edited_msg = self.client.edit_message_text(
                chat_id="me",
                message_id=self._active_payload.message_id,
                text=updated_payload.to_message_text(),
            )
            # Re-sincronizamos fecha del servidor
            server_ts = self._get_server_timestamp(edited_msg)
            self._active_payload = AccountLockPayload(
                account_id=updated_payload.account_id,
                node_id=updated_payload.node_id,
                created_at=updated_payload.created_at,
                expires_at=server_ts + extension,
                message_id=updated_payload.message_id,
            )
            logger.debug(
                f"Lock renovado en Telegram: Expira a las {self._active_payload.expires_at}"
            )
            return True
        except RPCError as e:
            logger.error(
                f"Fallo crítico renovando lock en 'me' (Msg {self._active_payload.message_id}): {e}"
            )
            self._is_acquired = False
            return False

    def release(self) -> None:
        """
        Libera el lock eliminando físicamente el mensaje de 'Mensajes Guardados'.
        Es idempotente y tolerante a fallos si el mensaje ya fue eliminado.
        """
        if not self._is_acquired or not self._active_payload:
            return

        msg_id = self._active_payload.message_id
        self._is_acquired = False
        self._active_payload = None
        _GLOBAL_ACTIVE_LOCKS.discard(self)

        if msg_id:
            try:
                self.client.delete_messages("me", msg_id)
                logger.debug(f"Lock liberado exitosamente en 'me' (Msg {msg_id}).")
            except RPCError as e:
                logger.warning(f"Aviso liberando lock en Telegram (Msg {msg_id}): {e}")

    def __enter__(self) -> AccountLockPayload:
        return self.acquire()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


# Registro de locks activos para liberación en caso de interrupción forzada
_GLOBAL_ACTIVE_LOCKS: set[TelegramAccountLock] = set()


def _cleanup_locks_on_shutdown(*args, **kwargs):
    """Callback de emergencia para liberar locks ante SIGINT, SIGTERM o exit."""
    while _GLOBAL_ACTIVE_LOCKS:
        lock = _GLOBAL_ACTIVE_LOCKS.pop()
        try:
            lock.release()
        except Exception:  # noqa: S110
            pass


atexit.register(_cleanup_locks_on_shutdown)

# Registrar interceptores de señales si estamos en el hilo principal
try:
    signal.signal(
        signal.SIGINT,
        lambda s, f: (_cleanup_locks_on_shutdown(), signal.default_int_handler(s, f)),
    )
    signal.signal(
        signal.SIGTERM, lambda s, f: (_cleanup_locks_on_shutdown(), sys.exit(1))
    )
except (ValueError, AttributeError):
    # En entornos multihilo o Windows no siempre se pueden asignar señales fuera del main thread
    pass
