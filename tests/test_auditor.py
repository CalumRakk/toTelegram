import tempfile
import time
import unittest
from pathlib import Path

import peewee

from totelegram.database import DatabaseSession, db_proxy
from totelegram.migration import setup_database_schema
from totelegram.models import (
    Job,
    Payload,
    RemotePayload,
    RemoteSnapshot,
    Source,
    TelegramChat,
    TelegramUser,
)
from totelegram.packaging.auditor import AuditStatus, LocalAuditService
from totelegram.schemas import SourceType


class TestLocalAuditorSubdirectoryIsolation(unittest.TestCase):
    def setUp(self):
        self.test_db = peewee.SqliteDatabase(":memory:")
        db_proxy.initialize(self.test_db)
        setup_database_schema(self.test_db)

        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name).resolve()

        # Crear estructura:
        # base_path/
        #   ├── root_file.mkv
        #   └── Tell-Tale-Heart/
        #         └── Tell-Tale-Heart-pt1.mp4
        self.root_file = self.base_path / "root_file.mkv"
        self.root_file.write_bytes(b"A" * 1024)

        self.sub_dir = self.base_path / "Tell-Tale-Heart"
        self.sub_dir.mkdir()
        self.sub_file = self.sub_dir / "Tell-Tale-Heart-pt1.mp4"
        self.sub_file.write_bytes(b"B" * 2048)

        # Crear registros en base de datos
        self.chat = TelegramChat.create(id=1001, title="Media 2", type="channel")
        self.user = TelegramUser.create(id=2001, first_name="Tester")

        # 1. Registrar y respaldar root_file
        self.source_root = Source.get_or_create_from_filepath(self.root_file)
        self.job_root = Job.create(
            source=self.source_root, chat=self.chat, chunk_size=2000 * 1024 * 1024
        )
        self.payload_root = Payload.create(
            job=self.job_root,
            sequence_index=0,
            filename=self.root_file.name,
            filename_short=self.root_file.name,
            start_offset=0,
            end_offset=1024,
            size=1024,
        )
        RemotePayload.create(
            payload=self.payload_root,
            message_id=50,
            chat=self.chat,
            owner=self.user,
            json_metadata={},
        )
        RemoteSnapshot.register(self.job_root, 51, self.chat, self.user)

        # 2. Registrar y respaldar sub_file (dentro de Tell-Tale-Heart)
        self.source_sub = Source.get_or_create_from_filepath(self.sub_file)
        self.job_sub = Job.create(
            source=self.source_sub, chat=self.chat, chunk_size=2000 * 1024 * 1024
        )
        self.payload_sub = Payload.create(
            job=self.job_sub,
            sequence_index=0,
            filename=self.sub_file.name,
            filename_short=self.sub_file.name,
            start_offset=0,
            end_offset=2048,
            size=2048,
        )
        RemotePayload.create(
            payload=self.payload_sub,
            message_id=60,
            chat=self.chat,
            owner=self.user,
            json_metadata={},
        )
        RemoteSnapshot.register(self.job_sub, 61, self.chat, self.user)

    def tearDown(self):
        self.temp_dir.cleanup()
        if not self.test_db.is_closed():
            self.test_db.close()

    def test_shallow_audit_does_not_leak_subfolder_files(self):
        """Verifica que el escaneo de primer nivel no muestre archivos de subcarpetas."""
        items = LocalAuditService.audit_path(self.base_path)
        items_by_name = {item.filename: item for item in items}

        # En la raíz SOLO deben existir 'root_file.mkv' y la carpeta 'Tell-Tale-Heart'
        self.assertIn("root_file.mkv", items_by_name)
        self.assertIn("Tell-Tale-Heart", items_by_name)

        # El archivo de la subcarpeta NO debe figurar aquí ni como BACKED_UP ni como REMOTE_ONLY
        self.assertNotIn("Tell-Tale-Heart-pt1.mp4", items_by_name)

        # Verificar estados
        self.assertEqual(items_by_name["root_file.mkv"].status, AuditStatus.BACKED_UP)
        self.assertEqual(items_by_name["Tell-Tale-Heart"].status, AuditStatus.DIRECTORY)

    def test_shallow_audit_inside_subdirectory(self):
        """Verifica que al auditar directamente la subcarpeta, sus archivos sí aparezcan respaldados."""
        items = LocalAuditService.audit_path(self.sub_dir)
        items_by_name = {item.filename: item for item in items}

        self.assertIn("Tell-Tale-Heart-pt1.mp4", items_by_name)
        self.assertEqual(
            items_by_name["Tell-Tale-Heart-pt1.mp4"].status, AuditStatus.BACKED_UP
        )
        self.assertNotIn("root_file.mkv", items_by_name)

    def test_remote_only_applies_strictly_to_direct_children(self):
        """Verifica que borrar un archivo en raíz solo marque ese como REMOTE_ONLY sin filtrar los de subcarpetas."""
        # Eliminamos el archivo raíz del disco
        self.root_file.unlink()

        items = LocalAuditService.audit_path(self.base_path)
        items_by_name = {item.filename: item for item in items}

        # root_file.mkv ahora es REMOTE_ONLY
        self.assertIn("root_file.mkv", items_by_name)
        self.assertEqual(items_by_name["root_file.mkv"].status, AuditStatus.REMOTE_ONLY)

        # Tell-Tale-Heart sigue siendo un Directorio neutral
        self.assertEqual(items_by_name["Tell-Tale-Heart"].status, AuditStatus.DIRECTORY)

        # Tell-Tale-Heart-pt1.mp4 SIGUE sin aparecer en el nivel raíz
        self.assertNotIn("Tell-Tale-Heart-pt1.mp4", items_by_name)

    def test_recursive_audit_shows_subfolder_files_with_relative_paths(self):
        """Verifica que con recursive=True se auditen archivos de subcarpetas con ruta relativa."""
        items = LocalAuditService.audit_path(self.base_path, recursive=True)
        items_by_name = {item.filename: item for item in items}

        # En modo recursivo deben figurar ambos archivos
        self.assertIn("root_file.mkv", items_by_name)
        expected_sub_rel = str(Path("Tell-Tale-Heart") / "Tell-Tale-Heart-pt1.mp4")
        self.assertIn(expected_sub_rel, items_by_name)

        self.assertEqual(items_by_name["root_file.mkv"].status, AuditStatus.BACKED_UP)
        self.assertEqual(items_by_name[expected_sub_rel].status, AuditStatus.BACKED_UP)

    def test_recursive_audit_remote_only_shows_relative_path(self):
        """Verifica que si se elimina un archivo en subcarpeta, en modo recursivo se reporte con ruta relativa."""
        self.sub_file.unlink()

        items = LocalAuditService.audit_path(self.base_path, recursive=True)
        items_by_name = {item.filename: item for item in items}

        expected_sub_rel = str(Path("Tell-Tale-Heart") / "Tell-Tale-Heart-pt1.mp4")
        self.assertIn(expected_sub_rel, items_by_name)
        self.assertEqual(
            items_by_name[expected_sub_rel].status, AuditStatus.REMOTE_ONLY
        )

    def test_shallow_prune_does_not_touch_subfolder_backed_up_files(self):
        """Verifica que una poda superficial (sin recursión) NO marque como podables
        los archivos de subcarpetas, protegiéndolos de borrado accidental."""
        items = LocalAuditService.audit_path(self.base_path, recursive=False)
        prune_candidates = [item for item in items if item.is_safe_to_prune]

        # Solo root_file.mkv debe ser podable en la raíz
        candidate_names = [c.filename for c in prune_candidates]
        self.assertEqual(candidate_names, ["root_file.mkv"])

        # Simulamos la poda de candidatos
        for c in prune_candidates:
            assert c.path is not None
            c.path.unlink()

        # El archivo raíz se borró, pero el archivo de la subcarpeta sigue intacto
        self.assertFalse(self.root_file.exists())
        self.assertTrue(self.sub_file.exists())

    def test_recursive_prune_deletes_subfolder_backed_up_files(self):
        """Verifica que la poda recursiva (recursive=True) identifique y permita podar
        archivos respaldados en subcarpetas con sus rutas físicas correctas."""
        items = LocalAuditService.audit_path(self.base_path, recursive=True)
        prune_candidates = [item for item in items if item.is_safe_to_prune]

        candidate_names = {c.filename for c in prune_candidates}
        expected_sub_rel = str(Path("Tell-Tale-Heart") / "Tell-Tale-Heart-pt1.mp4")

        self.assertIn("root_file.mkv", candidate_names)
        self.assertIn(expected_sub_rel, candidate_names)

        # Simulamos la poda de todos los candidatos seguros
        for c in prune_candidates:
            assert c.path is not None
            c.path.unlink()

        # Ambos archivos respaldados fueron eliminados físicamente
        self.assertFalse(self.root_file.exists())
        self.assertFalse(self.sub_file.exists())

        # La subcarpeta vacía se mantiene sin romper el árbol
        self.assertTrue(self.sub_dir.exists())

    def test_recursive_prune_protects_unbacked_files_in_subfolders(self):
        """Verifica que en poda recursiva, un archivo dentro de una subcarpeta
        que NO esté respaldado NUNCA sea marcado como candidato para podar."""
        # Creamos un archivo nuevo no respaldado en la subcarpeta
        unbacked_sub_file = self.sub_dir / "unbacked_draft.txt"
        unbacked_sub_file.write_text("borrador importante")

        items = LocalAuditService.audit_path(self.base_path, recursive=True)
        prune_candidates = [item for item in items if item.is_safe_to_prune]

        candidate_paths = {c.path.resolve() for c in prune_candidates}  # type: ignore
        self.assertNotIn(unbacked_sub_file.resolve(), candidate_paths)

        # Al podar, el no respaldado sobrevive
        for c in prune_candidates:
            assert c.path is not None
            c.path.unlink()

        self.assertTrue(unbacked_sub_file.exists())


class TestLocalAuditService(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workdir = Path(self.temp_dir.name) / "archivos"
        self.workdir.mkdir()

        # Base de datos SQLite aislada en memoria
        self.db_session = DatabaseSession("sqlite:///:memory:", auto_init_schema=True)
        self.db = self.db_session.start()

        self.chat = TelegramChat.create(
            id=-100111222333, title="Canal Auditoría", type="channel"
        )
        self.user = TelegramUser.create(
            id=1, first_name="AuditorUser", is_premium=False
        )

        # Archivo local de prueba
        self.file_a = self.workdir / "video1.mp4"
        self.file_a.write_bytes(b"DATA_VIDEO_1" * 100)

    def tearDown(self):
        self.db_session.close()
        self.temp_dir.cleanup()

    def _register_file_in_db(self, file_path: Path, chat: TelegramChat) -> Job:
        stat = file_path.stat()
        source = Source.create(
            path_str=str(file_path.resolve()),
            md5sum="hash_a1234567890",
            size=stat.st_size,
            mtime=stat.st_mtime,
            mimetype="video/mp4",
            type=SourceType.FILE,
        )
        job = Job.create(source=source, chat=chat, chunk_size=2000)
        p = Payload.create(
            job=job,
            sequence_index=0,
            start_offset=0,
            end_offset=stat.st_size,
            size=stat.st_size,
            filename=file_path.name,
            filename_short=file_path.name,
        )
        RemotePayload.create(
            payload=p,
            message_id=50,
            chat=chat,
            owner=self.user,
            json_metadata={"id": 50},
        )
        RemoteSnapshot.register(job, 51, chat, self.user)
        return job

    def test_audit_unbacked_file_is_pending(self):
        """Un archivo que solo existe en disco aparece como PENDING."""
        items = LocalAuditService.audit_path(self.workdir)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].filename, "video1.mp4")
        self.assertEqual(items[0].status, AuditStatus.PENDING)
        self.assertFalse(items[0].is_safe_to_prune)

    def test_audit_backed_up_file(self):
        """Un archivo en disco con Job y RemoteSnapshot aparece como BACKED_UP."""
        self._register_file_in_db(self.file_a, self.chat)

        items = LocalAuditService.audit_path(self.workdir)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.status, AuditStatus.BACKED_UP)
        self.assertTrue(item.is_safe_to_prune)
        self.assertIn("Canal Auditoría", item.destinations[0])

    def test_audit_protects_modified_file(self):
        """Si el archivo en disco cambia de tamaño o mtime, pasa a MODIFIED."""
        self._register_file_in_db(self.file_a, self.chat)

        time.sleep(0.05)
        self.file_a.write_bytes(b"CONTENIDO_TOTALMENTE_NUEVO_Y_MAS_LARGO")

        items = LocalAuditService.audit_path(self.workdir)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.status, AuditStatus.MODIFIED)
        self.assertFalse(item.is_safe_to_prune)

    def test_audit_moved_file_path_auto_update(self):
        """Camino 2: Si el archivo se movió de carpeta, se detecta por (filename, size, mtime) y se actualiza path_str."""
        job = self._register_file_in_db(self.file_a, self.chat)

        nuevo_dir = self.workdir / "subcarpeta"
        nuevo_dir.mkdir()
        nuevo_path = nuevo_dir / "video1.mp4"
        self.file_a.rename(nuevo_path)

        items = LocalAuditService.audit_path(nuevo_dir)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.status, AuditStatus.BACKED_UP)

        source_refreshed = Source.get_by_id(job.source.id)
        self.assertEqual(source_refreshed.path_str, str(nuevo_path.resolve()))

    def test_audit_detects_legacy_snapshot_without_parsing(self):
        """Comprueba que un snapshot .json.xz corrupto o no registrado se detecte sin abrirlo ni fallar."""
        sidecar = self.workdir / f"{self.file_a.name}.json.xz"
        sidecar.write_bytes(b"ESTO NO ES UN ARCHIVO LZMA VALIDO")

        items = LocalAuditService.audit_path(self.workdir)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.status, AuditStatus.LOCAL_SNAPSHOT)
        self.assertIn("Local (Snapshot)", item.destinations)
        self.assertFalse(item.is_safe_to_prune)

    def test_audit_unbacked_folder_is_neutral_directory(self):
        """Una carpeta sin respaldo de cinta aparece como neutral (DIRECTORIO)."""
        sub_dir = self.workdir / "mis_documentos"
        sub_dir.mkdir()

        items = LocalAuditService.audit_path(self.workdir)
        item_dir = next(i for i in items if i.filename == "mis_documentos")
        self.assertEqual(item_dir.status, AuditStatus.DIRECTORY)
        self.assertTrue(item_dir.is_dir)
        self.assertFalse(item_dir.is_safe_to_prune)
        self.assertEqual(len(item_dir.destinations), 0)

    def test_audit_tape_folder_backup(self):
        """Una carpeta registrada como cinta TAR aparece como TAPE_BACKUP."""
        sub_dir = self.workdir / "fotos_vacaciones"
        sub_dir.mkdir()

        source_folder = Source.create(
            path_str=str(sub_dir.resolve()),
            md5sum="fingerprint_folder_abc",
            size=5000,
            mtime=1700000000.0,
            mimetype="application/x-tar",
            type=SourceType.FOLDER,
        )
        job = Job.create(source=source_folder, chat=self.chat, chunk_size=2000)
        p = Payload.create(
            job=job,
            sequence_index=0,
            start_offset=0,
            end_offset=5000,
            size=5000,
            filename="fotos.tar.01-01",
            filename_short="f.tar.01-01",
        )
        RemotePayload.create(
            payload=p,
            message_id=60,
            chat=self.chat,
            owner=self.user,
            json_metadata={"id": 60},
        )
        RemoteSnapshot.register(job, 61, self.chat, self.user)

        items = LocalAuditService.audit_path(self.workdir)
        item_folder = next(i for i in items if i.filename == "fotos_vacaciones")
        self.assertEqual(item_folder.status, AuditStatus.TAPE_BACKUP)
        self.assertTrue(item_folder.is_dir)
        self.assertIn("Canal Auditoría", item_folder.destinations[0])

    def test_audit_remote_only_file(self):
        """Un archivo registrado en DB que ya fue borrado de disco (prune) aparece como REMOTE_ONLY."""
        self._register_file_in_db(self.file_a, self.chat)
        self.file_a.unlink()

        items = LocalAuditService.audit_path(self.workdir)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.status, AuditStatus.REMOTE_ONLY)
        self.assertIsNone(item.path)
        self.assertFalse(item.is_safe_to_prune)

    def test_prune_deletes_file_and_preserves_db_records(self):
        """Verifica que al podar se borre el archivo físico pero se conserve el registro en SQLite."""
        self._register_file_in_db(self.file_a, self.chat)

        # Archivo secundario no respaldado para confirmar que prune no lo toca
        file_unbacked = self.workdir / "no_respaldado.mp4"
        file_unbacked.write_bytes(b"DATA_NO_RESPALDADA")

        # Localizar candidatos seguros para liberar espacio
        candidates = [
            item
            for item in LocalAuditService.audit_path(self.workdir)
            if item.is_safe_to_prune
        ]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].filename, "video1.mp4")

        # Borrado físico simulado
        for c in candidates:
            assert c.path is not None
            c.path.unlink()

        # El archivo respaldado se borró físicamente
        self.assertFalse(self.file_a.exists())
        # El archivo no respaldado sigue en su lugar intacto
        self.assertTrue(file_unbacked.exists())

        # En SQLite el Source y Job siguen existiendo intactos
        source = Source.get_or_none(Source.path_str == str(self.file_a.resolve()))
        self.assertIsNotNone(source)
        assert source is not None
        self.assertEqual(source.jobs.count(), 1)

        # La siguiente auditoría lo detecta como REMOTE_ONLY
        refreshed_audit = LocalAuditService.audit_path(self.workdir)
        res_map = {r.filename: r for r in refreshed_audit}
        self.assertEqual(res_map["video1.mp4"].status, AuditStatus.REMOTE_ONLY)


if __name__ == "__main__":
    unittest.main()
