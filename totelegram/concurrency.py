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
    @abstractmethod
    def acquire(
        self,
        resource_id: str,
        resource_type: ResourceType,
        node_id: str,
        expires_at: datetime,
    ) -> bool:
        pass

    @abstractmethod
    def renew(self, resource_id: str, node_id: str, expires_at: datetime) -> bool:
        pass

    @abstractmethod
    def release(self, resource_id: str) -> None:
        pass


class LeaseManager:
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
        self.store.release(f"account:{account_id}")

    def release_job_lease(self, job_id: int):
        self.store.release(f"job:{job_id}")

    def renew(self, resource_id: str, ttl_minutes: int = 5) -> bool:
        expires = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
        return self.store.renew(resource_id, self.node_id, expires)


class PeeweeLeaseStore(LeaseStore):
    """
    Implementación atómica de LeaseStore para SQLite y PostgreSQL.
    No arroja excepciones de integridad que invaliden transacciones en Postgres.
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
        now = datetime.now(timezone.utc)
        try:
            with self.db.connection_context():
                with db_transaction(self.db):
                    # 1. Intentar reclamar de forma atómica si ya expiró o si pertenece al mismo nodo
                    updated = (
                        Claim.update(
                            node_id=node_id,
                            expires_at=expires_at,
                            updated_at=now,
                        )
                        .where(
                            (Claim.resource_id == resource_id)
                            & ((Claim.expires_at < now) | (Claim.node_id == node_id))
                        )
                        .execute()
                    )
                    if updated > 0:
                        return True

                    # 2. Si no existía registro previo, insertar atómicamente ignorando duplicados
                    inserted = (
                        Claim.insert(
                            resource_id=resource_id,
                            resource_type=resource_type,
                            node_id=node_id,
                            expires_at=expires_at,
                            created_at=now,
                            updated_at=now,
                        )
                        .on_conflict_ignore()
                        .execute()
                    )
                    if inserted > 0:
                        return True

            # Si no se actualizó ni insertó, el recurso está tomado por otro nodo activo
            claim = cast(Claim, Claim.get_or_none(Claim.resource_id == resource_id))
            logger.warning(
                f"Recurso {resource_id} bloqueado por otro nodo: {claim.node_id if claim else 'desconocido'}"
            )
            return False
        except Exception as e:
            logger.error(f"Error adquiriendo lease para {resource_id}: {e}")
            return False

    def renew(self, resource_id: str, node_id: str, expires_at: datetime) -> bool:
        now = datetime.now(timezone.utc)
        try:
            with self.db.connection_context():
                with db_transaction(self.db):
                    rows = (
                        Claim.update(expires_at=expires_at, updated_at=now)
                        .where(
                            (Claim.resource_id == resource_id)
                            & (Claim.node_id == node_id)
                        )
                        .execute()
                    )
                    return rows > 0
        except Exception as e:
            logger.error(f"Error renovando lease de {resource_id}: {e}")
            return False

    def release(self, resource_id: str) -> None:
        try:
            with self.db.connection_context():
                with db_transaction(self.db):
                    Claim.delete().where(Claim.resource_id == resource_id).execute()
        except Exception as e:
            logger.error(f"Error liberando lease de {resource_id}: {e}")


class LeaseKeeper:
    def __init__(self, manager: LeaseManager, resource_id: str, ttl_minutes: int = 5):
        self.manager = manager
        self.resource_id = resource_id
        self.ttl_minutes = ttl_minutes
        self.interval_seconds = (ttl_minutes * 60) / 2.0
        self._stop_event = threading.Event()
        self._thread = None

    def _heartbeat(self):
        while not self._stop_event.wait(self.interval_seconds):
            logger.debug(f"Heartbeat: Renovando lease para {self.resource_id}...")
            success = self.manager.renew(self.resource_id, self.ttl_minutes)
            if not success:
                logger.warning(
                    f"Peligro: No se pudo renovar el lease para {self.resource_id}."
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
