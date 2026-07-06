import logging
import threading
from datetime import datetime, timedelta
from typing import cast

import peewee

from totelegram.database import db_transaction
from totelegram.models import Claim, ResourceType

logger = logging.getLogger(__name__)


class LeaseManager:
    def __init__(self, db: peewee.Database, node_id: str):
        self.db = db
        self.node_id = node_id

    def try_acquire_account_lease(self, account_id: int, ttl_minutes: int = 5) -> bool:
        """Intenta adquirir el arrendamiento exclusivo para una cuenta de Telegram."""
        return self.try_acquire(
            f"account:{account_id}", ResourceType.ACCOUNT, ttl_minutes
        )

    def try_acquire_job_lease(self, job_id: int, ttl_minutes: int = 5) -> bool:
        """Intenta adquirir el arrendamiento exclusivo para un Job específico."""
        return self.try_acquire(f"job:{job_id}", ResourceType.JOB, ttl_minutes)

    def release_account_lease(self, account_id: int):
        """Libera el arrendamiento de una cuenta de Telegram."""
        self.release(f"account:{account_id}")

    def release_job_lease(self, job_id: int):
        """Libera el arrendamiento de un Job."""
        self.release(f"job:{job_id}")

    def renew(self, resource_id: str, ttl_minutes: int = 5) -> bool:
        """Renueva el tiempo de expiración de un lease si aún nos pertenece."""
        expires = datetime.now() + timedelta(minutes=ttl_minutes)
        try:
            with self.db.connection_context():
                with db_transaction(self.db):
                    claim = cast(
                        Claim, Claim.get_or_none(Claim.resource_id == resource_id)
                    )
                    if claim and claim.node_id == self.node_id:
                        claim.expires_at = expires
                        claim.save(only=[Claim.expires_at, Claim.updated_at])
                        return True
            return False
        except Exception as e:
            logger.error(f"Error renovando lease de {resource_id}: {e}")
            return False

    def try_acquire(
        self, resource_id: str, r_type: ResourceType, ttl_minutes: int = 5
    ) -> bool:
        """
        Intenta adquirir un lease genérico.
        Retorna True si se adquirió (o ya era nuestro), False si está tomado por otro nodo.
        """
        now = datetime.now()
        expires = now + timedelta(minutes=ttl_minutes)

        try:
            with db_transaction(self.db):
                Claim.create(
                    resource_id=resource_id,
                    resource_type=r_type,
                    node_id=self.node_id,
                    expires_at=expires,
                )
                return True
        except peewee.IntegrityError:
            claim = cast(Claim, Claim.get_or_none(Claim.resource_id == resource_id))
            if claim:
                if claim.node_id == self.node_id:
                    claim.expires_at = expires
                    claim.save(only=[Claim.expires_at, Claim.updated_at])
                    return True

                if datetime.now() > claim.expires_at:
                    claim.node_id = self.node_id
                    claim.expires_at = expires
                    claim.save(only=[Claim.node_id, Claim.expires_at, Claim.updated_at])
                    logger.info(f"Lease recuperado (expirado) para {resource_id}")
                    return True

            logger.warning(
                f"Recurso {resource_id} bloqueado por otro nodo: {claim.node_id if claim else 'unknown'}"
            )
            return False

    def release(self, resource_id: str):
        """Elimina el registro de arrendamiento de la base de datos."""
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
        # wait() devuelve True si el evento se setea (cuando hacemos stop),
        # o False si ocurre el timeout (lo cual usamos como nuestro timer).
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
