"""覆盖恢复明确选择及默认新项目隔离保持；原生PG回退另在CI运行。"""

import unittest

import restore_postgres as pg
import restore_postgres_existing as existing
import test_restore_postgres as fixtures


class ExistingPostgresTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PostgresTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.manifest["project"] = "recovery"

    def test_default_guard_still_refuses_source_and_explicit_restore_keeps_owner(self):
        f = self.fixture
        with self.assertRaises(ValueError):
            pg.target("recovery", f.manifest, f.identity, f.docker)
        self.assertFalse(f.calls)
        existing.restore("recovery", f.manifest, f.identity, f.payload, f.docker)
        writes = [(args, path) for args, path in f.calls if path]
        self.assertEqual(len(writes), 3)
        self.assertTrue(
            all("--no-owner" in args[-3] and "--no-privileges" in args[-3] for args, _ in writes)
        )
        drops = [args for args, _ in f.calls if any("dropdb" in arg for arg in args)]
        self.assertEqual([args[-1] for args in drops], ["dify", "dify_plugin", "postgres"])
        self.assertTrue(
            all(
                "--maintenance-db=template1" in args[-3] and "--force" in args[-3] for args in drops
            )
        )

    def test_bad_origin_template_or_running_application_refused_before_drop(self):
        f = self.fixture
        for defect in ("origin", "template", "writer"):
            with self.subTest(defect=defect):
                f.calls.clear()
                f.manifest["project"] = "other" if defect == "origin" else "recovery"
                (f.payload / "databases.json").write_text(
                    '["template1"]' if defect == "template" else '["dify"]'
                )
                f.item["Config"]["Labels"][pg.LABEL + "service"] = (
                    "api" if defect == "writer" else "db_postgres"
                )
                with self.assertRaises(ValueError):
                    existing.restore("recovery", f.manifest, f.identity, f.payload, f.docker)
                self.assertFalse(
                    any(path or any("dropdb" in arg for arg in args) for args, path in f.calls)
                )
