from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import errno

from backup_input import restore_file, restore_tree
from flatpaks_installer import merge_app_data


class RestoreConflicts(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.incoming = self.root / "incoming"
        self.target = self.root / "target"
        self.incoming.mkdir()
        self.target.mkdir()

    def test_personal_files_are_kept_and_new_files_are_added(self):
        for name, value in (("same", "identical"), ("new", "from backup"), ("different", "archived")):
            (self.incoming / name).write_text(value)
        for name, value in (("same", "identical"), ("different", "current")):
            (self.target / name).write_text(value)
        conflicts = []
        restore_tree(self.incoming, self.target, conflicts=conflicts)
        self.assertEqual(conflicts, [self.target / "different"])
        self.assertEqual((self.target / "different").read_text(), "current")
        self.assertEqual((self.target / "new").read_text(), "from backup")

    def test_configuration_can_replace_existing_file(self):
        source = self.incoming / "preferences"
        target = self.target / source.name
        source.write_text("archived")
        target.write_text("default")
        restore_tree(self.incoming, self.target)
        self.assertEqual(target.read_text(), "archived")

    def test_personal_symlink_and_directory_conflicts_are_not_traversed(self):
        linked = self.root / "linked"
        linked.mkdir()
        (self.target / "link").symlink_to(linked)
        (self.incoming / "link").mkdir()
        (self.incoming / "link/unsafe").touch()
        (self.incoming / "file").write_text("archived")
        (self.target / "file").mkdir()
        conflicts = []
        restore_tree(self.incoming, self.target, conflicts=conflicts)
        self.assertEqual(set(conflicts), {self.target / "link", self.target / "file"})
        self.assertFalse(list(linked.iterdir()))

    def test_portal_fallback_never_replaces_existing_data(self):
        source = self.incoming / "file"
        source.write_text("archived")
        target = self.target / source.name
        target.write_text("current")
        conflicts = []
        with patch("backup_input.os.link", side_effect=OSError(errno.EOPNOTSUPP, "no hardlink")):
            restore_file(source, target, conflicts=conflicts)
            restore_file(source, self.target / "new", conflicts=conflicts)
        self.assertEqual(conflicts, [target])
        self.assertEqual(target.read_text(), "current")
        self.assertEqual((self.target / "new").read_text(), "archived")

    def test_app_data_replacement_retains_private_original(self):
        (self.incoming / "file").write_text("archived")
        (self.target / "file").write_text("current")
        with patch("flatpaks_installer.running_apps", return_value=set()):
            self.assertTrue(merge_app_data([self.incoming], self.target))
        self.assertEqual((self.target / "file").read_text(), "archived")
        recovery, = self.root.glob(".savedesktop-plus-previous-*")
        self.assertEqual((recovery / "target/file").read_text(), "current")
        self.assertEqual(recovery.stat().st_mode & 0o777, 0o700)
