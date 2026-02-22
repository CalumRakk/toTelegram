from __future__ import annotations

import json
import locale
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional, cast

from totelegram.core.registry import SettingsManager

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram import enums
    from pyrogram.enums import MessageMediaType
    from pyrogram.errors import (
        ApiIdInvalid,
        ApiIdPublishedFlood,
        ChannelPrivate,
        ChatWriteForbidden,
        PeerIdInvalid,
        UsernameInvalid,
    )
    from pyrogram.types import Chat, Message


logger = logging.getLogger(__name__)


class TelegramSession:
    def __init__(
        self,
        session_name: str,
        api_id: int,
        api_hash: str,
        profiles_dir: Path | str,
    ):
        self.client: Optional[Client] = None
        self.name = session_name
        self.api_id = api_id
        self.api_hash = api_hash
        self.profiles_dir = profiles_dir

    def start(self) -> Client:
        """Inicia la conexión manualmente."""
        if self.client and self.client.is_connected:
            return self.client

        logger.info(f"Iniciando sesión de Telegram: {self.name}")

        lang, encoding = locale.getdefaultlocale()
        iso639 = lang.split("_")[0] if lang else "en"

        from pyrogram import Client  # type: ignore
        from pyrogram.errors import ApiIdInvalid, ApiIdPublishedFlood
        from pyrogram.types import Chat  # type: ignore

        self.client = Client(
            name=self.name,  # type: ignore
            api_id=self.api_id,  # type: ignore
            api_hash=self.api_hash,  # type: ignore
            workdir=str(self.profiles_dir),  # type: ignore
            lang_code=iso639,
            in_memory=False,
            no_updates=True,
        )

        try:
            self.client.start()  # type: ignore
            me = cast(Chat, self.client.get_me())
            logger.info(
                f"Cliente Pyrogram iniciado correctamente: {me.first_name} (@{me.username})"
            )
            return self.client

        except ApiIdInvalid as e:
            logger.debug("Error: API ID o Hash inválidos.")
            raise e
        except ApiIdPublishedFlood as e:
            logger.debug("Error: API ID baneado públicamente.")
            raise e
        except Exception as e:
            logger.debug(f"Error: inesperado: {e}")
            raise e

    def stop(self):
        """Detiene la conexión manualmente."""
        if self.client and self.client.is_connected:
            logger.info("Cerrando sesión de Telegram...")
            try:
                self.client.stop()  # type: ignore
            except Exception as e:
                logger.warning(f"Error al cerrar cliente (ignorable): {e}")
        self.client = None

    def __enter__(self) -> Client:
        """Soporte para Context Manager (with)."""
        return self.start()

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Soporte para Context Manager (with)."""
        self.stop()

    @classmethod
    def from_profile(
        cls, profile_name: str, manager: SettingsManager
    ) -> "TelegramSession":
        """
        Construye una `TelegramSession` a partir de un perfil válido.

        Verifica que el perfil exista y sea trinity, obtiene sus credenciales
        y devuelve una sesión lista para iniciarse.

        Args:
            profile_name (str): Nombre del perfil a usar.
            manager (SettingsManager): Manejador de configuraciones.

        Raises:
            ValueError: Si el perfil no existe o no es trinity

        Examples:
            >>> with TelegramSession.from_profile("default", manager) as client:
            ...     client.get_me()
        """
        profile = manager.get_profile(profile_name)

        if profile is None:
            raise ValueError("Perfil no existe")

        if not profile.is_trinity:
            raise ValueError("Perfil no es trinity")

        settings = manager.get_settings(profile_name)
        return cls(
            session_name=profile_name,
            api_id=settings.api_id,
            api_hash=settings.api_hash,
            profiles_dir=manager.profiles_dir,
        )


def parse_message_json_data(json_data: dict) -> Message:
    """Utilidad para reconstruir objetos Message desde JSON almacenado en BD."""
    from pyrogram.enums import MessageMediaType
    from pyrogram.types import Chat, Message

    if isinstance(json_data, str):
        json_data = json.loads(json_data)

    data = json_data.copy()

    # Traducimos message_id (nombre en JSON) a id (nombre en constructor)
    if "message_id" in data:
        data["id"] = data.pop("message_id")

    if "_" in data:
        data.pop("_")

    chat_json = data["chat"].copy()

    if "_" in chat_json:
        chat_json.pop("_")
    chat = Chat(**chat_json)

    media_raw = data.get("media")
    media_type = None
    if isinstance(media_raw, str) and "." in media_raw:

        media_type_str = media_raw.split(".")[-1].lower()
        media_type = MessageMediaType(media_type_str)
    elif media_raw:
        media_type = media_raw

    data["chat"] = chat
    data["media"] = media_type

    data.pop("link", "")
    return Message(**data)
