import logging
from typing import Any, Dict, List, Literal, Tuple

from totelegram.core.registry import SettingsManager
from totelegram.core.setting import Settings

logger = logging.getLogger(__name__)


class ConfigService:
    def __init__(self, manager: SettingsManager, is_debug: bool = False):
        self.manager = manager
        self.is_debug = is_debug

    def prepare_updates(self, args: List[str]) -> Dict[str, Any]:
        """
        Transforma una lista plana [k, v, k, v] en un dict validado.
        Lanza ValueError si los pares están incompletos o fallan validación.
        """

        # TODO: Hacer más explicito que el valor del par puede ser Any, aunque normalmente se espera que sea un string.
        if not args or len(args) % 2 != 0:
            raise ValueError(
                "Debes proporcionar pares de CLAVE y VALOR. Ej: 'set chat_id 12345'"
            )

        raw_data = {args[i].lower(): args[i + 1] for i in range(0, len(args), 2)}
        updates = {}

        for key, raw_value in raw_data.items():
            # Validar permisos de acceso (Editable, Debug, etc)
            Settings.validate_key_access(self.is_debug, key)
            # Validar y convertir tipos (str -> int, etc)
            clean_value = Settings.validate_single_setting(key, raw_value)
            updates[key] = clean_value

        return updates

    def apply_update(
        self,
        settings_name: str,
        key: str,
        value: Any,
        action: Literal["set", "add"] = "set",
    ) -> Tuple[bool, Any]:
        """
        Aplica un cambio individual.
        Devuelve (si_cambio, valor_final).
        """
        if action == "set":
            return self.manager.set_setting(settings_name, key, value)
        else:
            if not isinstance(value, list):
                value = [value]
            return self.manager.add_setting(settings_name, key, value)

    def restore_default(self, settings_name: str, key: str) -> Any:
        """Restaura una configuración a su valor por defecto."""
        info = Settings.validate_key_access(self.is_debug, key)

        self.manager.unset_setting(settings_name, key)

        self.manager.set_setting(settings_name, key, info.default_value)
        return info.default_value
