import logging
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import cast

import peewee

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
        try:
            with self.db.atomic():
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

                exp = claim.expires_at
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)

                if datetime.now(timezone.utc) > exp:
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
                with self.db.atomic():
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
