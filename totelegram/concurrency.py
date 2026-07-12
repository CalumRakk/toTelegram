import logging
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import cast

import peewee

from totelegram.database import db_transaction
from totelegram.models import Claim, ResourceType

logger = logging.getLogger(__name__)


class LeaseStore(ABC):
    """
    Interfaz abstracta (contrato) para la persistencia de los bloqueos.
    Permite desacoplar el LeaseManager del ORM o motor de base de datos específico.
    """

    @abstractmethod
    def acquire(
        self,
        resource_id: str,
        resource_type: ResourceType,
        node_id: str,
        expires_at: datetime,
    ) -> bool:
        """Intenta adquirir o heredar un lease de forma atómica."""
        pass

    @abstractmethod
    def renew(self, resource_id: str, node_id: str, expires_at: datetime) -> bool:
        """Renueva un lease si aún pertenece al nodo indicado."""
        pass

    @abstractmethod
    def release(self, resource_id: str) -> None:
        """Elimina o libera el lease."""
        pass


class LeaseManager:
    """
    Gestor de nivel de dominio para controlar los arrendamientos de recursos.
    No contiene lógica de acceso directo a base de datos.
    """

    def __init__(self, store: LeaseStore, node_id: str):
        self.store = store
        self.node_id = node_id

    def try_acquire_account_lease(self, account_id: int, ttl_minutes: int = 5) -> bool:
        expires = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
        return self.store.acquire(
            f"account:{account_id}", ResourceType.ACCOUNT, self.node_id, expires
        )

    def try_acquire_job_lease(self, job_id: int, ttl_minutes: int = 5) -> bool:
        expires = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
        return self.store.acquire(
            f"job:{job_id}", ResourceType.JOB, self.node_id, expires
        )

    def release_account_lease(self, account_id: int):
        """Libera el arrendamiento de una cuenta de Telegram."""
        self.store.release(f"account:{account_id}")

    def release_job_lease(self, job_id: int):
        """Libera el arrendamiento de un Job."""
        self.store.release(f"job:{job_id}")

    def renew(self, resource_id: str, ttl_minutes: int = 5) -> bool:
        expires = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
        return self.store.renew(resource_id, self.node_id, expires)


class PeeweeLeaseStore(LeaseStore):
    """
    Implementación de LeaseStore utilizando Peewee ORM.
    Mantiene la compatibilidad actual con SQLite y PostgreSQL a través de transacciones.
    """

    def __init__(self, db: peewee.Database):
        self.db = db

    def acquire(
        self,
        resource_id: str,
        resource_type: ResourceType,
        node_id: str,
        expires_at: datetime,
    ) -> bool:
        try:
            with db_transaction(self.db):
                Claim.create(
                    resource_id=resource_id,
                    resource_type=resource_type,
                    node_id=node_id,
                    expires_at=expires_at,
                )
                return True
        except peewee.IntegrityError:
            claim = cast(Claim, Claim.get_or_none(Claim.resource_id == resource_id))
            if claim:
                if claim.node_id == node_id:
                    claim.expires_at = expires_at
                    claim.save(only=[Claim.expires_at, Claim.updated_at])
                    return True

                if datetime.now(timezone.utc) > claim.expires_at:
                    claim.node_id = node_id
                    claim.expires_at = expires_at
                    claim.save(only=[Claim.node_id, Claim.expires_at, Claim.updated_at])
                    logger.info(f"Lease recuperado (expirado) para {resource_id}")
                    return True

            logger.warning(
                f"Recurso {resource_id} bloqueado por otro nodo: {claim.node_id if claim else 'unknown'}"
            )
            return False

    def renew(self, resource_id: str, node_id: str, expires_at: datetime) -> bool:
        try:
            with self.db.connection_context():
                with db_transaction(self.db):
                    claim = cast(
                        Claim, Claim.get_or_none(Claim.resource_id == resource_id)
                    )
                    if claim and claim.node_id == node_id:
                        claim.expires_at = expires_at
                        claim.save(only=[Claim.expires_at, Claim.updated_at])
                        return True
            return False
        except Exception as e:
            logger.error(f"Error renovando lease de {resource_id}: {e}")
            return False

    def release(self, resource_id: str) -> None:
        Claim.delete().where(Claim.resource_id == resource_id).execute()


class LeaseKeeper:
    """
    Context Manager que lanza un hilo en segundo plano para mantener vivo
    un Lease (lock) renovándolo periódicamente.
    """

    def __init__(self, manager: LeaseManager, resource_id: str, ttl_minutes: int = 5):
        self.manager = manager
        self.resource_id = resource_id
        self.ttl_minutes = ttl_minutes

        # Renovamos el lock cuando haya transcurrido la mitad del tiempo de vida
        self.interval_seconds = (ttl_minutes * 60) / 2.0

        self._stop_event = threading.Event()
        self._thread = None

    def _heartbeat(self):
        while not self._stop_event.wait(self.interval_seconds):
            logger.debug(f"Heartbeat: Renovando lease para {self.resource_id}...")
            success = self.manager.renew(self.resource_id, self.ttl_minutes)
            if not success:
                logger.warning(
                    f"Peligro: No se pudo renovar el lease para {self.resource_id}. "
                    "¿Fue robado por otro nodo o eliminado?"
                )

    def __enter__(self):
        self._thread = threading.Thread(
            target=self._heartbeat, daemon=True, name=f"Heartbeat-{self.resource_id}"
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
