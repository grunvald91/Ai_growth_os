import unittest

from create_deploy_archive import should_exclude


class ShouldExcludeTests(unittest.TestCase):
    def test_excludes_globbed_backup_and_temp_files(self):
        self.assertTrue(should_exclude("bot/handlers/client.py.backup"))
        self.assertTrue(should_exclude("bot/handlers/tmpclaude-1377-cwd"))
        self.assertTrue(should_exclude("deploy_update.tar.gz"))

    def test_excludes_environment_files(self):
        self.assertTrue(should_exclude(".env"))
        self.assertTrue(should_exclude(".env.production"))

    def test_keeps_regular_source_files(self):
        self.assertFalse(should_exclude("bot/main.py"))


if __name__ == "__main__":
    unittest.main()
