import unittest

from totelegram.core.setting import AccessLevel, Settings


class TestArchitectureRules(unittest.TestCase):
    def test_adr_001_immutable_profile_credentials(self):
        """
        ADR-001: Perfil Inmutable.

        Las credenciales base (API_ID, API_HASH) y el nombre del perfil
        DEBEN ser inmutables para garantizar la integridad de la sesión de Pyrogram.

        Si este test falla, lee: docs/adr/001-perfil-inmutable.md
        """
        required_internal_fields = {"API_ID", "API_HASH", "PROFILE_NAME"}

        api_id= Settings.get_info("API_ID")
        api_hash= Settings.get_info("API_HASH")
        profile_name= Settings.get_info("PROFILE_NAME")

        error_message = (
            f"\n\n[VIOLACIÓN DE ARQUITECTURA - ADR-001]\n"
            f"Se intentó hacer editables campos críticos: {required_internal_fields}\n"
            f"Estos campos deben permanecer en Settings.INTERNAL_FIELDS.\n"
            f"Consulta 'docs/adr/001-perfil-inmutable.md' para más detalles.\n"
        )

        self.assertTrue(api_id is not None and api_id.level == AccessLevel.DEBUG_READONLY, error_message)
        self.assertTrue(api_hash is not None and api_hash.level == AccessLevel.DEBUG_READONLY, error_message)
        self.assertTrue(profile_name is not None and profile_name.level == AccessLevel.DEBUG_READONLY, error_message)
