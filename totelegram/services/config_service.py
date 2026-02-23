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

        def remove_quotation(value: Any):
            if isinstance(value, str):
                return value.strip("'").strip('"')
            if isinstance(value, list):
                return [remove_quotation(v) for v in value]
            return value

        raw_data = {
            args[i].lower(): remove_quotation(args[i + 1])
            for i in range(0, len(args), 2)
        }
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
        action: Literal["set", "add", "remove"],
    ) -> Tuple[bool, Any]:
        """
        Aplica un cambio individual, resolviendo la lógica de listas si es necesario.
        Devuelve (si_cambio, valor_final).
        """
        key = key.lower().strip()

        if action == "set":
            changed = self.manager.set_setting(settings_name, key, value)
            return changed, value

        # Modificar listas (add/remove)
        if not isinstance(value, list):
            value = [value]

        try:
            current_settings = self.manager.get_settings(settings_name)
            current_list = getattr(current_settings, key)
        except Exception:
            # Si el archivo está roto, tomamos el default
            info = Settings.get_info(key)
            current_list = (
                info.default_value.copy()
                if info and isinstance(info.default_value, list)
                else []
            )

        if not isinstance(current_list, list):
            raise ValueError(
                f"El campo '{key}' no es una lista, no se puede usar '{action}'."
            )

        original_count = len(current_list)

        if action == "add":
            for item in value:
                if item not in current_list:
                    current_list.append(item)
        elif action == "remove":
            current_list = [item for item in current_list if item not in value]

        # Guardar solo si hubo cambios reales
        changed = len(current_list) != original_count
        if changed:
            self.manager.set_setting(settings_name, key, current_list)

        return changed, current_list

    def restore_default(self, settings_name: str, key: str) -> Any:
        """Restaura una configuración a su valor por defecto."""
        info = Settings.validate_key_access(self.is_debug, key)

        self.manager.unset_setting(settings_name, key)

        self.manager.set_setting(settings_name, key, info.default_value)
        return info.default_value
