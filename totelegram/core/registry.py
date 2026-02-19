import json
import logging
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, cast

from dotenv import dotenv_values
from pydantic import BaseModel, ValidationError

from totelegram.core.setting import (
    APP_SESSION_NAME,
    InfoField,
    Settings,
)
from totelegram.utils import VALUE_NOT_SET, get_user_config_dir

logger = logging.getLogger(__name__)


class Profile(BaseModel):
    name: str
    path_env: Path
    path_session: Path

    @property
    def has_env(self):
        return self.path_env.exists()

    @property
    def has_session(self):
        return self.path_session.exists()

    @property
    def is_trinity(self):
        """Un perfil es trinity si tiene ambos archivos y su nombre."""
        return self.has_env and self.has_session


class settings:
    name: str
    is_debug: bool


class Result(NamedTuple):
    changed: bool
    value: Any


class SettingsManager:
    def __init__(self, worktable: Optional[Path] = None):
        self.app_name = APP_SESSION_NAME
        self.worktable = worktable or Path(get_user_config_dir(self.app_name))
        self.settings_active_path = self.worktable / "settings_active"
        self.profiles_dir = self.worktable / "profiles"
        self.database_path = self.profiles_dir / f"{self.app_name}.sqlite"

    def get_settings_path(self, name: str) -> Path:
        """nombre -> profiles/nombre.json"""
        return self.profiles_dir / f"{name.lower()}.env"

    def _get_all_profile_stems(self) -> List[str]:
        """Busca todos los nombres únicos que tienen un .env o un .session"""
        if not self.profiles_dir.exists():
            return []

        stems = set()
        for f in self.profiles_dir.glob("*"):
            if f.suffix in [".env", ".session"]:
                stems.add(f.stem)
        return sorted(list(stems))

    def get_all_profiles(self) -> List[Profile]:
        profiles = []
        for name in self._get_all_profile_stems():
            path_env = self.profiles_dir / f"{name}.env"
            path_session = self.profiles_dir / f"{name}.session"
            profile = Profile(name=name, path_env=path_env, path_session=path_session)
            profiles.append(profile)
        return profiles

    def get_profile(self, name: str) -> Optional[Profile]:
        return next((p for p in self.get_all_profiles() if p.name == name), None)

    def delete_settings_profile(self, name: str) -> List[Path]:
        """
        Elimina físicamente el conjunto de archivos del perfil.
        Retorna una lista de los archivos que fueron eliminados.
        """
        name_lower = name.lower()
        env_path = self.get_settings_path(name_lower)
        session_path = self.profiles_dir / f"{name_lower}.session"

        deleted_files = []

        if env_path.exists():
            env_path.unlink()
            deleted_files.append(env_path)

        if session_path.exists():
            session_path.unlink()
            deleted_files.append(session_path)

        if self.active_settings_exists():
            if self.get_active_settings_name() == name_lower:
                self.settings_active_path.unlink()

        return deleted_files

    def settings_exists(self, name: str) -> bool:
        return self.get_settings_path(name).exists()

    def set_settings_name_as_active(self, name: str):
        path = self.get_settings_path(name)
        if not path.exists():
            raise IOError(f"Se intentó activar el perfil '{name}', pero no existe.")

        self.settings_active_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_active_path.write_text(name)

    def get_active_settings_name(self) -> str:
        if not self.settings_active_path.exists():
            raise IOError("No hay un perfil activo.")
        return self.settings_active_path.read_text()

    def active_settings_exists(self) -> bool:
        return self.settings_active_path.exists()

    def get_settings(self, settings_name: str) -> Settings:
        settings_path = self.get_settings_path(settings_name)
        if not settings_path.exists():
            raise IOError(
                f"El archivo de configuracion '{settings_path.name}' no existe."
            )

        try:
            settings = Settings(_env_file=settings_path)  # type: ignore
            return settings
        except (ValueError, ValidationError) as e:
            logger.error(
                f"El archivo de configuracion '{settings_path.name}' contiene errores:"
            )
            raise ValueError(f"Error de validacion: {e}")

    @staticmethod
    def get_default_settings() -> Settings:
        return Settings(profile_name=VALUE_NOT_SET)

    def resolve_settings_name(
        self, settings_name: Optional[str] = None, error: bool = True
    ) -> Optional[str]:
        """
        Valida y resuelve el perfil de configuración a utilizar.
        La operacion simplifica estos tres escenarios:

        - Si se proporciona `settings_name`, verifica que exista.
        - Si no se proporciona, utiliza el perfil activo.
        - Si la validación falla y `error` es True, lanza ValueError;
        de lo contrario, devuelve None.

        Args:
            settings_name (Optional[str]): Nombre del perfil a validar.
            error (bool): Indica si se debe lanzar una excepción en caso de error.

        Raises:
            ValueError: Si no se logra resolver el perfil y `error` es True.
        """

        if settings_name:
            if not self.settings_exists(settings_name):
                logger.error(f"El perfil {settings_name} no existe.")
                if error:
                    raise ValueError(f"El perfil {settings_name} no existe.")
            return settings_name

        if not self.active_settings_exists():
            logger.error("No hay un perfil activo.")
            if error:
                raise ValueError("No hay un perfil activo.")

        try:
            return self.get_active_settings_name()
        except IOError:
            logger.error("Error al obtener el perfil activo.")
            if error:
                raise ValueError("No hay un perfil activo.")

    # --------------------------------
    #  Métodos de escritura y sanidad
    # --------------------------------

    def _load_and_sanitize(self, settings_name: str) -> Dict[str, Any]:
        """
         Carga el contenido crudo del perfil y normaliza sus claves.

        ¿Por qué este método y no usar Settings()? por:

         1. Evita la contaminacion del archivo: Settings cargaría todos los valores por defecto
            del sistema. Al guardar, el archivo (.model_dump()) pasaría de tener 3 líneas
            a tener +20, dificultando la lectura humana.
         2. Resiliencia: Si al archivo le faltan campos obligatorios (ej. profile_name),
            Settings() lanzaría una validación fallida impidiendo incluso la lectura.
            Este método permite cargar archivos "rotos" para repararlos.
         3. Integridad: Evita que Pydantic descarte variables o metadatos presentes
            en el archivo que no estén mapeados en el modelo actual.
        """
        path = self.get_settings_path(settings_name)
        if not path.exists():
            return {}

        # dotenv_values devuelve todos los valores como strings.
        raw_values = dotenv_values(path)
        return {
            k.lower(): Settings.validate_single_setting(k, v)
            for k, v in raw_values.items()
            if v is not None
        }

    def _write_all_settings(self, settings_name: str, data: Dict[str, Any]) -> None:
        """Vuelca un diccionario completo al archivo .env

        No verifica claves ni valor.
        Se espera que el valor sea un objeto estandar de python.
        """
        path = self.get_settings_path(settings_name)
        path.parent.mkdir(parents=True, exist_ok=True)

        lines = [
            f"{k.lower()}={json.dumps(v, ensure_ascii=False)}" for k, v in data.items()
        ]

        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def set_setting(
        self, settings_name: str, field_name: str, field_value: Any
    ) -> Result:
        """
        Establece un valor de configuración especifico.

        Returns:
            Tuple[bool, Any]: (True si el valor cambió físicamente, Valor actual/final)
        """
        key_normalized = field_name.lower().strip()

        new_value = Settings.validate_single_setting(key_normalized, field_value)

        current_data = self._load_and_sanitize(settings_name)
        if key_normalized in current_data:
            old_value = current_data[key_normalized]
            try:
                old_value = Settings.validate_single_setting(key_normalized, old_value)
            except Exception:
                # Si el valor en el archivo estaba corrupto, forzamos el valor por defecto
                return self.unset_setting(settings_name, key_normalized)

            if old_value == new_value:
                return Result(False, old_value)

        current_data[key_normalized] = new_value
        self._write_all_settings(settings_name, current_data)
        return Result(True, new_value)

    def unset_setting(self, settings_name: str, field_name: str):
        """
        Restablece un valor de configuración especifico a su valor por defecto.

        Returns:
            Tuple[bool, Any]: (True si el valor cambió físicamente, Valor por defecto)

        """
        key_normalized = field_name.lower().strip()

        info = cast(InfoField, Settings.validate_key_access(False, key_normalized))

        current_data = self._load_and_sanitize(settings_name)
        if key_normalized in current_data:
            del current_data[key_normalized]
            self._write_all_settings(settings_name, current_data)
            # Si la clave existia, fue elimina y ahora se usará el valor por defecto.
            return Result(True, info.default_value)

        # Si la clave no estaba en el archivo, es porque se está usando el valor por defecto.
        return Result(False, info.default_value)

    def add_setting(
        self, settings_name: str, field_name: str, field_values: List[str]
    ) -> Result:
        """
        Agrega elementos a una configuración de tipo lista sin duplicarlos.

        Returns:
            Tuple[bool, List[str]]: (True si se añadieron elementos nuevos, Lista final resultante)
        """
        key_normalized = field_name.lower().strip()
        new_elements = Settings.validate_single_setting(key_normalized, field_values)
        if not isinstance(field_values, list):
            raise ValueError(
                f"El campo '{key_normalized}' no es una lista, no se puede usar 'add'."
            )

        # Obtenemos el valor el valor del archivo (si lo hay) para comparar con el nuevo.
        current_data = self._load_and_sanitize(settings_name)

        current_list = []
        if key_normalized in current_data:
            try:
                current_list = list(
                    Settings.validate_single_setting(
                        key_normalized, current_data[key_normalized]
                    )
                )
            except (ValueError, TypeError):
                # El valor en el archivo estaba corrupto, forzamos el valor por defecto
                info = cast(InfoField, Settings.get_info(key_normalized))
                current_list = info.default_value

        # Solo se agregan los elementos que no estan ya en la lista
        initial_count = len(current_list)
        for item in new_elements:
            if item not in current_list:
                current_list.append(item)

        changed = len(current_list) > initial_count
        if changed:
            current_data[key_normalized] = current_list
            self._write_all_settings(settings_name, current_data)

        return Result(changed, current_list)


class ProfileManager:
    pass
