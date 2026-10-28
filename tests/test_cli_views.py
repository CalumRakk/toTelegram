import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from totelegram.cli.ui import strip_markup
from totelegram.cli.views.ledger import DisplayLedger
from totelegram.cli.views.settings import DisplayConfig, DisplayProfile, DisplayTarget
from totelegram.cli.views.snapshots import DisplaySnapshotPlan
from totelegram.cli.views.upload import DisplayIndexer, DisplayUpload
from totelegram.identity import Profile, Settings, SettingsManager
from totelegram.packaging.importer import (
    DbMutations,
    ImportPlan,
    InspectionStatus,
    PlanAction,
    PlanItem,
    SnapshotInspection,
)
from totelegram.packaging.ledger import BackupDestinationRecord, FileAuditResult
from totelegram.packaging.schemas import SourceMetadata, UploadManifest
from totelegram.schemas import ScanReport, SourceType
from totelegram.telegram.types import (
    ChatMatch,
    TargetResolution,
    TargetResolutionStatus,
)


class TestCliViews(unittest.TestCase):
    """Batería de pruebas para las vistas desacopladas y formateadores de CLI."""

    def test_strip_markup(self):
        """Verifica que strip_markup elimine tags de Rich correctamente."""
        text = "[bold cyan]Destino:[/] [green]Canal de Pruebas[/green]"
        clean = strip_markup(text)
        self.assertEqual(clean, "Destino: Canal de Pruebas")

    def test_display_upload_skip_report_rendering(self):
        """Verifica que DisplayUpload reporte omisiones sin lanzar excepciones."""
        report = ScanReport(exclusion_patterns=["*.tmp"])
        report.log_skip(Path("archivo_grande.iso"), "size")
        report.log_skip(Path("cache.tmp"), "exclusion")
        report.log_skip(Path("corrupto.bin"), "error")

        # No debe lanzar excepción
        DisplayUpload.show_skip_report(report, item_type="archivo", force_verbose=True)
        DisplayUpload.show_skip_report(report, item_type="archivo", force_verbose=False)

    def test_display_ledger_rendering(self):
        """Verifica el renderizado de la tabla de auditoría y prune."""
        dest = BackupDestinationRecord(
            chat_id=-100123456,
            chat_title="Mi Canal",
            snapshot_message_id=42,
            chunk_size=1000,
            total_parts=1,
        )
        audit_result = FileAuditResult(
            filename="foto.jpg",
            path=Path("/tmp/foto.jpg"),
            size=5000,
            is_on_disk=True,
            is_backed_up=True,
            is_modified=False,
            destinations={"-100123456": dest},
        )

        # Verificar tabla de estado
        DisplayLedger.show_directory_status(
            dir_path=Path("/tmp"),
            audit_results=[audit_result],
        )

        # Verificar tabla de candidatos a prune
        DisplayLedger.show_prune_candidates(
            candidates=[audit_result],
            total_bytes=5000,
        )

        # Verificar resumen final
        DisplayLedger.show_prune_summary(deleted_count=1, freed_bytes=5000)

    def test_display_settings_and_profiles(self):
        """Verifica que las tablas de configuración y perfiles se generen correctamente."""
        with TemporaryDirectory() as tmp_dir:
            mgr = SettingsManager(Path(tmp_dir))
            settings = Settings(profile_name="test_user")

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

    def test_display_target_status(self):
        """Verifica el renderizado de estados de destino."""
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
        DisplayTarget.show_current_status("test_user", resolution_ok, -100111222)

        resolution_err = TargetResolution(
            query="canal_test",
            status=TargetResolutionStatus.AMBIGUOUS,
            error_message="Permisos insuficientes",
        )
        DisplayTarget.show_current_status("test_user", resolution_err, -100111222)

    def test_display_indexer_notice(self):
        """Verifica el aviso de indexación pendiente."""
        DisplayIndexer.show_unindexed_notice(count=5)

    def test_display_snapshot_plan_tables(self):
        """Verifica la generación del balance y tablas del plan de importación."""
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

        DisplaySnapshotPlan.show_balance(plan)
        DisplaySnapshotPlan.show_action_table(plan)
        DisplaySnapshotPlan.show_fast_exit(total=5)


if __name__ == "__main__":
    unittest.main()
