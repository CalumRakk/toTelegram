# tests/test_architecture.py
import unittest

from totelegram.core.setting import Settings


class TestArchitectureRules(unittest.TestCase):
    def test_adr_001_immutable_profile_credentials(self):
        """
        ADR-001: Perfil Inmutable.

        Las credenciales base (API_ID, API_HASH) y el nombre del perfil
        DEBEN ser inmutables para garantizar la integridad de la sesión de Pyrogram.

        Si este test falla, lee: docs/adr/001-perfil-inmutable.md
        """
        required_internal_fields = {"API_ID", "API_HASH", "PROFILE_NAME"}

        current_internals = Settings.INTERNAL_FIELDS
        missing_fields = required_internal_fields - current_internals

        error_message = (
            f"\n\n[VIOLACIÓN DE ARQUITECTURA - ADR-001]\n"
            f"Se intentó hacer editables campos críticos: {missing_fields}\n"
            f"Estos campos deben permanecer en Settings.INTERNAL_FIELDS.\n"
            f"Consulta 'docs/adr/001-perfil-inmutable.md' para más detalles.\n"
        )

        self.assertTrue(missing_fields == set(), error_message)
