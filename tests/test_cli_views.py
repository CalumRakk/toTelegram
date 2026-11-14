import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from totelegram.cli.ui import console, strip_markup
from totelegram.cli.views.ledger import DisplayLedger
from totelegram.cli.views.settings import DisplayConfig, DisplayProfile, DisplayTarget
from totelegram.cli.views.snapshots import DisplaySnapshotPlan
from totelegram.cli.views.upload import DisplayUpload
from totelegram.i18n import LOCALES_DIR, override_locale
from totelegram.identity import Profile, Settings, SettingsManager
from totelegram.packaging.auditor import AuditItem, AuditStatus
from totelegram.packaging.importer import (
    DbMutations,
    ImportPlan,
    InspectionStatus,
    PlanAction,
    PlanItem,
    SnapshotInspection,
)
from totelegram.packaging.schemas import SourceMetadata, UploadManifest
from totelegram.schemas import ScanReport, SourceType
from totelegram.telegram.types import (
    ChatMatch,
    TargetResolution,
    TargetResolutionStatus,
)


class TestCliViews(unittest.TestCase):
    """Batería de pruebas para las vistas desacopladas y formateadores de CLI (Capa 3)."""

    def test_strip_markup(self):
        """Verifica que strip_markup elimine tags de Rich correctamente."""
        text = "[bold cyan]Destino:[/] [green]Canal de Pruebas[/green]"
        clean = strip_markup(text)
        self.assertEqual(clean, "Destino: Canal de Pruebas")

    def test_display_upload_skip_report_rendering_bilingual(self):
        """Verifica que DisplayUpload reporte omisiones correctamente en español e inglés."""
        for lang in ("es", "en"):
            with override_locale(lang):
                report = ScanReport(exclusion_patterns=["*.tmp"])
                report.log_skip(Path("archivo_grande.iso"), "size")
                report.log_skip(Path("cache.tmp"), "exclusion")
                report.log_skip(Path("corrupto.bin"), "error")

                # No deben lanzar excepciones al renderizar en ningún idioma
                with console.capture() as capture:
                    DisplayUpload.show_skip_report(
                        report, item_type="archivo", force_verbose=True
                    )
                    DisplayUpload.show_skip_report(
                        report, item_type="archivo", force_verbose=False
                    )
                    DisplayUpload.show_skip_report(
                        report, item_type="carpeta", force_verbose=False
                    )

                output = capture.get()
                self.assertTrue(len(output) > 0)

    def test_display_ledger_rendering_bilingual(self):
        """Verifica el renderizado de la tabla de auditoría y prune en ambos idiomas."""
        audit_result = AuditItem(
            filename="foto.jpg",
            path=Path("/tmp/foto.jpg"),
            size=5000,
            is_dir=False,
            status=AuditStatus.BACKED_UP,
            destinations=["Mi Canal (-100123456)"],
        )

        for lang in ("es", "en"):
            with override_locale(lang):
                with console.capture() as capture:
                    # 1. Tabla de estado
                    DisplayLedger.show_directory_status(
                        dir_path=Path("/tmp"),
                        audit_results=[audit_result],
                    )

                    # 2. Tabla de candidatos a prune
                    DisplayLedger.show_prune_candidates(
                        candidates=[audit_result],
                        total_bytes=5000,
                    )

                    # 3. Resumen final de prune
                    DisplayLedger.show_prune_summary(deleted_count=1, freed_bytes=5000)

                output = capture.get()
                if lang == "es":
                    self.assertIn("Estado de Respaldo Local", output)
                    self.assertIn("✓ Respaldado", output)
                else:
                    self.assertIn("Local Backup Status", output)
                    self.assertIn("✓ Backed up", output)

    def test_display_settings_and_profiles_bilingual(self):
        """Verifica que las tablas de configuración y perfiles se generen en ambos idiomas."""
        with TemporaryDirectory() as tmp_dir:
            mgr = SettingsManager(Path(tmp_dir))
            settings = Settings(profile_name="test_user")

            for lang in ("es", "en"):
                with override_locale(lang):
                    with console.capture() as capture:
                        DisplayConfig.show_config_table(
                            profile_name="test_user",
                            manager=mgr,
                            is_debug=False,
                            settings=settings,
                        )

                        profile = Profile(
                            name="test_user",
                            path_env=mgr.get_settings_path("test_user"),
                            path_session=mgr.get_session_path("test_user"),
                        )
                        DisplayProfile.render_profiles_table(
                            manager=mgr,
                            active="test_user",
                            profiles=[profile],
                            quiet=False,
                        )

                    output = capture.get()
                    if lang == "es":
                        self.assertIn("Perfiles disponibles", output)
                    else:
                        self.assertIn("Available profiles", output)

    def test_display_target_status_bilingual(self):
        """Verifica el renderizado de diagnósticos y destinos en ambos idiomas."""
        match = ChatMatch(
            id=-100111222,
            title="Canal Target",
            type="channel",
            username="canal_test",
        )
        resolution_ok = TargetResolution(
            query="canal_test",
            status=TargetResolutionStatus.RESOLVED,
            chat_id=-100111222,
            title="Canal Target",
            match=match,
        )

        for lang in ("es", "en"):
            with override_locale(lang):
                with console.capture() as capture:
                    DisplayTarget.show_diagnostic(
                        resolution=resolution_ok,
                        can_delete=True,
                        account_max_size=2000 * 1024 * 1024,
                        is_premium=False,
                    )
                    DisplayTarget.show_ambiguous_targets("canal_test", [match])
                    DisplayTarget.show_known_targets([])

                output = capture.get()
                if lang == "es":
                    self.assertIn("Destino verificado", output)
                    self.assertIn("Mensajes Guardados", output)
                else:
                    self.assertIn("Target verified", output)
                    self.assertIn("Saved Messages", output)

    def test_display_snapshot_plan_bilingual(self):
        """Verifica el renderizado de planes de importación de snapshot en ambos idiomas."""
        manifest = UploadManifest(
            chunk_size=1000,
            created_at=MagicMock(),
            chat_id=-100999,
            owner_id=1,
            owner_name="Tester",
            source=SourceMetadata(
                filename="documento.pdf",
                size=2000,
                md5sum="a" * 32,
                mime_type="application/pdf",
                mtime=1.0,
                type=SourceType.FILE,
            ),
            parts=[],
        )
        inspection = SnapshotInspection(
            path=Path("/tmp/documento.pdf.json.xz"),
            status=InspectionStatus.VALID,
            manifest=manifest,
        )
        item = PlanItem(
            inspection=inspection,
            manifest=manifest,
            action=PlanAction.CREATE_NEW,
            target_chat_id=-100999,
            total_parts=1,
            mutations=DbMutations(
                create_source=True, create_job=True, payloads_count=1
            ),
        )
        plan = ImportPlan(items=[item])

        for lang in ("es", "en"):
            with override_locale(lang):
                with console.capture() as capture:
                    DisplaySnapshotPlan.show_balance(plan)
                    DisplaySnapshotPlan.show_action_table(plan)
                    DisplaySnapshotPlan.show_fast_exit(total=5)

                output = capture.get()
                if lang == "es":
                    self.assertIn("BALANCE DEL ESCANEO", output)
                    self.assertIn("ACCIONES A REALIZAR", output)
                else:
                    self.assertIn("SCAN BALANCE", output)
                    self.assertIn("ACTIONS TO PERFORM", output)

    def test_rich_markup_tags_balance_in_catalogs(self):
        """Valida que ninguna traducción en el catálogo .po rompa corchetes de marcado Rich."""
        _tag_pattern = re.compile(r"\[/?([a-zA-Z0-9_# =]+)\]")
        msgstr_pattern = re.compile(r'msgstr(?:\[\d+\])?\s+"(.*)"')

        po_path = LOCALES_DIR / "en" / "LC_MESSAGES" / "totelegram.po"
        self.assertTrue(po_path.exists(), f"El archivo {po_path} no existe.")

        content = po_path.read_text(encoding="utf-8")
        matches = msgstr_pattern.findall(content)

        for line in matches:
            if not line:
                continue
            open_brackets = line.count("[")
            close_brackets = line.count("]")
            self.assertEqual(
                open_brackets,
                close_brackets,
                f"Desbalance de corchetes Rich en [en]: {line}",
            )


if __name__ == "__main__":
    unittest.main()
