
import logging
import threading
from datetime import datetime, timedelta

import peewee

from totelegram.models import Claim, ResourceType

logger = logging.getLogger(__name__)

class LeaseManager:
    def __init__(self, db: peewee.Database, node_id: str):
        self.db = db
        self.node_id = node_id

    def renew(self, resource_id: str, ttl_minutes: int = 5) -> bool:
        """Renueva el tiempo de expiración de un lock si aún nos pertenece."""
        expires = datetime.now() + timedelta(minutes=ttl_minutes)
        try:
            # connection_context() es VITAL aquí porque Peewee usa conexiones thread-local.
            with self.db.connection_context():
                with self.db.atomic():
                    claim = Claim.get_or_none(Claim.resource_id == resource_id)
                    if claim and claim.node_id == self.node_id:
                        claim.expires_at = expires
                        claim.save()
                        return True
            return False
        except Exception as e:
            logger.error(f"Error renovando lock de {resource_id}: {e}")
            return False

    def try_acquire(self, resource_id: str, r_type: ResourceType, ttl_minutes: int = 5) -> bool:
        """
        Intenta adquirir un lock.
        Retorna True si se adquirió (o ya era nuestro), False si está tomado por otro nodo.
        """
        now = datetime.now()
        expires = now + timedelta(minutes=ttl_minutes)

        try:
            with self.db.atomic():
                # Intentar crear el registro
                Claim.create(
                    resource_id=resource_id,
                    resource_type=r_type,
                    node_id=self.node_id,
                    expires_at=expires
                )
                return True
        except peewee.IntegrityError:
            # Ya existe, comprobamos si es nuestro o si está expirado
            claim = Claim.get_or_none(Claim.resource_id == resource_id)
            if claim:
                if claim.node_id == self.node_id:
                    # Es nuestro, renovamos
                    claim.expires_at = expires
                    claim.save()
                    return True

                if datetime.now() > claim.expires_at:
                    # Expiró, lo robamos
                    claim.node_id = self.node_id
                    claim.expires_at = expires
                    claim.save()
                    logger.info(f"Lock robado para {resource_id}")
                    return True

            logger.warning(f"Recurso {resource_id} bloqueado por otro nodo: {claim.node_id if claim else 'unknown'}")
            return False

    def release(self, resource_id: str):
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
        self._thread = threading.Thread(target=self._heartbeat, daemon=True, name=f"Heartbeat-{self.resource_id}")
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
