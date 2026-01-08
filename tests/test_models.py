import unittest

from peewee import SqliteDatabase

from totelegram.core.enums import JobStatus
from totelegram.core.setting import Settings
from totelegram.store.models import Job, SourceFile, Strategy, db_proxy


class TestJobLogic(unittest.TestCase):
    def setUp(self):
        self.test_db = SqliteDatabase(":memory:")
        db_proxy.initialize(self.test_db)
        db_proxy.create_tables([SourceFile, Job])

        self.settings = Settings(
            profile_name="test", chat_id=123, max_filesize_bytes=100
        )

    def tearDown(self):
        self.test_db.close()

    def test_job_strategy_selection_chunked(self):
        """Si el archivo es mayor al límite, debe ser CHUNKED"""
        source = SourceFile.create(
            path_str="big_file.dat",
            md5sum="abc",
            size=150,
            mtime=1.0,
            mimetype="application/octet-stream",
        )

        job = Job.get_or_create_from_source(source, self.settings)
        self.assertEqual(job.strategy, Strategy.CHUNKED)
        self.assertEqual(job.status, JobStatus.PENDING)

    def test_job_strategy_selection_single(self):
        """Si el archivo es menor al límite, debe ser SINGLE"""
        source = SourceFile.create(
            path_str="small_file.dat",
            md5sum="def",
            size=50,
            mtime=1.0,
            mimetype="text/plain",
        )

        job = Job.get_or_create_from_source(source, self.settings)
        self.assertEqual(job.strategy, Strategy.SINGLE)
