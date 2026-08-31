import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Generator, List, Optional, cast

import peewee

from totelegram.common.helpers import get_utc_now, is_expired
from totelegram.models import Claim, Job, Payload, RemotePayload
from totelegram.schemas import ResourceType

logger = logging.getLogger(__name__)


# EXCEPCIONES DE DOMINIO


class ConcurrencyError(Exception):
    """Excepción base para conflictos de concurrencia."""

    pass


class AccountBusyError(ConcurrencyError):
    """Lanzada cuando una cuenta de Telegram está en uso por otro nodo/terminal."""

    def __init__(self, account_id: int, holder_node_id: Optional[str] = None):
        self.account_id = account_id
        self.holder_node_id = holder_node_id
        super().__init__(
            f"La cuenta de Telegram '{account_id}' está en uso activo por el nodo: {holder_node_id or 'desconocido'}."
        )


class PayloadClaimError(ConcurrencyError):
    """Lanzada cuando ocurre un error al reclamar una pieza."""

    pass


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
            sleep_chunk = min(remaining, step_seconds)
            time.sleep(sleep_chunk)
            remaining -= sleep_chunk

            self.pulse()
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
    Compatible tanto con SQLite local como con PostgreSQL concurrente.
    """

    def __init__(self, db: peewee.Database, node_id: str):
        self.db = db
        self.node_id = node_id

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
            if claim:
                if claim.node_id == self.node_id or is_expired(claim.expires_at):
                    claim.node_id = self.node_id
                    claim.expires_at = expires_at
                    claim.save(only=[Claim.node_id, Claim.expires_at, Claim.updated_at])
                    logger.debug(
                        f"Lease recuperado/renovado: {resource_id} [Nodo: {self.node_id}]"
                    )
                    return True

            return False

    def renew_resources(self, resource_ids: List[str], ttl_seconds: int = 300) -> bool:
        """Renueva en lote la vigencia de todos los recursos listados."""
        if not resource_ids:
            return True

        new_expires_at = get_utc_now() + timedelta(seconds=ttl_seconds)
        try:
            with self.db.atomic():
                updated = (
                    Claim.update(expires_at=new_expires_at)
                    .where(
                        (Claim.resource_id.in_(resource_ids))
                        & (Claim.node_id == self.node_id)
                    )
                    .execute()
                )
                logger.debug(
                    f"Heartbeat: {updated}/{len(resource_ids)} leases renovados."
                )
                return updated > 0
        except Exception as e:
            logger.error(f"Fallo al renovar leases {resource_ids}: {e}")
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
        self, account_id: Optional[int], ttl_seconds: int = 300
    ) -> Generator[str, None, None]:
        """
        Garantiza exclusividad global sobre una cuenta de Telegram durante el bloque `with`.
        Lanza `AccountBusyError` si otro nodo la está utilizando.
        """
        if not account_id:
            yield "account:anonymous"
            return

        resource_id = f"account:{account_id}"
        acquired = self._acquire_resource(
            resource_id=resource_id,
            resource_type=ResourceType.ACCOUNT,
            ttl_seconds=ttl_seconds,
        )

        if not acquired:
            existing = Claim.get_or_none(Claim.resource_id == resource_id)
            holder = existing.node_id if existing else "desconocido"
            raise AccountBusyError(account_id, holder)

        try:
            yield resource_id
        finally:
            self.release_resource(resource_id)

    @contextmanager
    def claim_next_payload(
        self,
        job: Job,
        account_id: Optional[int] = None,
        ttl_seconds: int = 300,
    ) -> Generator[Optional[PayloadClaim], None, None]:
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
                (
                    RemotePayload.is_orphaned == False  # noqa: E712
                )
            )
        ]

        # Obtener candidatos pendientes ordenados por secuencia
        query = (
            Payload.select()
            .where((Payload.job == job) & (~Payload.id.in_(fulfilled_ids)))  # type: ignore
            .order_by(Payload.sequence_index)
        )

        candidates = list(query)
        selected_payload: Optional[Payload] = None
        selected_resource_id: Optional[str] = None

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
            tracked_resources=tracked,
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
