import logging
from pathlib import Path
from typing import Tuple, cast

from totelegram.identity import DEFAULT_TG_API_HASH, DEFAULT_TG_API_ID
from totelegram.telegram.client import TelegramSession

logger = logging.getLogger(__name__)


class AuthLogic:
    def __init__(
        self,
        session_name: str,
        api_id: int | None = None,
        api_hash: str | None = None,
        workdir: Path | str | None = None,
    ) -> None:
        self.session_name = session_name
        self.api_id = api_id or DEFAULT_TG_API_ID
        self.api_hash = api_hash or DEFAULT_TG_API_HASH
        self.workdir = Path(workdir) if workdir else Path(".")

    def run_auth_flow(self) -> Tuple[Path, int, str]:
        """
        Inicia el flujo interactivo de autenticación de Pyrogram.
        Retorna:
          1. Ruta de la sesión temporal generada.
          2. ID numérico de la cuenta de Telegram.
          3. Primer nombre del usuario conectado (first_name).
        """
        from pyrogram.types import User

        with TelegramSession(
            session_name=self.session_name,
            api_id=self.api_id,
            api_hash=self.api_hash,
            profiles_dir=self.workdir,
        ) as client:
            me = cast(User, client.get_me())
            account_id = me.id
            first_name = me.first_name or "user"

        temp_session_path = self.workdir / f"{self.session_name}.session"
        return temp_session_path, account_id, first_name
