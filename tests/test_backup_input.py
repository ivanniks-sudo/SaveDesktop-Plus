import io
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from backup_input import prepare_backup, restore_tar, restore_tree


class SelectiveZipRestore(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name)
        self.archive = self.workspace / "backup.zip"

    def test_preview_and_selection_open_only_requested_files(self):
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr("Backup/General/dconf-settings.ini", "settings")
            archive.writestr("Backup/custom-dirs.json", "{}")
            archive.writestr("Backup/Custom_Dirs/one/config.ini", b"uri='/home/source'\n")
            archive.writestr("Backup/Custom_Dirs/two/large.bin", b"x" * 1024)
        opened = []
        original = zipfile.ZipFile.open

        def track(archive, entry, *args, **kwargs):
            opened.append(entry.filename if isinstance(entry, zipfile.ZipInfo) else entry)
            return original(archive, entry, *args, **kwargs)

        with patch.object(zipfile.ZipFile, "open", track), prepare_backup(self.archive, parent=self.workspace) as root:
            self.assertEqual(opened, [])
            self.assertEqual((root / "custom-dirs.json").read_text(), "{}")
            root.backup.home = Path("/home/target")
            restore_tree(root / "Custom_Dirs/one", self.workspace / "chosen")
        self.assertEqual(opened, ["Backup/custom-dirs.json", "Backup/Custom_Dirs/one/config.ini"])
        self.assertFalse(list(self.workspace.glob("restore-*")))
        self.assertEqual((self.workspace / "chosen/config.ini").read_bytes(), b"uri='/home/target'\n")

    def test_corrupt_selected_payload_cannot_replace_existing_file(self):
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr("General/dconf-settings.ini", "settings")
            archive.writestr("Custom_Dirs/one/file.txt", b"original contents")
        self.archive.write_bytes(self.archive.read_bytes().replace(b"original contents", b"modified contents"))
        destination = self.workspace / "target"
        destination.mkdir()
        (destination / "file.txt").write_text("preserve me")
        with prepare_backup(self.archive) as root:
            with self.assertRaisesRegex(ValueError, "CRC"):
                restore_tree(root / "Custom_Dirs/one", destination)
        self.assertEqual(list(destination.iterdir()), [destination / "file.txt"])
        self.assertEqual((destination / "file.txt").read_text(), "preserve me")

    def test_rejects_unsafe_paths_before_copying(self):
        for name in ("../outside", "/outside", "General\\outside"):
            with self.subTest(name=name):
                with zipfile.ZipFile(self.archive, "w") as archive:
                    archive.writestr("General/dconf-settings.ini", "settings")
                    archive.writestr(name, "invalid")
                with self.assertRaisesRegex(ValueError, "Unsafe backup path"):
                    with prepare_backup(self.archive):
                        self.fail("Unsafe archive passed preflight")

    def test_rejects_unsafe_symlink_even_in_unselected_component(self):
        link = zipfile.ZipInfo("Custom_Dirs/unselected/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr("General/dconf-settings.ini", "settings")
            archive.writestr(link, "../../outside")
        with self.assertRaisesRegex(ValueError, "Symbolic link leaves"):
            with prepare_backup(self.archive):
                self.fail("Unsafe link passed preflight")

    def test_nested_tar_stream(self):
        nested = io.BytesIO()
        with tarfile.open(fileobj=nested, mode="w:gz") as archive:
            entry = tarfile.TarInfo("Desktop/example")
            entry.size = 4
            archive.addfile(entry, io.BytesIO(b"file"))
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr("General/dconf-settings.ini", "settings")
            archive.writestr("General/desktop-folder.tgz", nested.getvalue())
        with prepare_backup(self.archive) as root:
            restore_tar(root / "General/desktop-folder.tgz", self.workspace / "desktop", single_folder=True)
        self.assertEqual((self.workspace / "desktop/example").read_bytes(), b"file")


    def test_icon_theme_relative_links_in_zip_and_nested_tar(self):
        link_name = "Theme/apps/22/application-menu.svg"
        image_name = "Theme/actions/22/application-menu.svg"
        target = "../../actions/22/application-menu.svg"
        link = zipfile.ZipInfo("Custom_Dirs/icons/" + link_name)
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        nested = io.BytesIO()
        with tarfile.open(fileobj=nested, mode="w:gz") as archive:
            image = tarfile.TarInfo("icons/" + image_name)
            image.size = 3
            archive.addfile(image, io.BytesIO(b"svg"))
            alias = tarfile.TarInfo("icons/" + link_name)
            alias.type = tarfile.SYMTYPE
            alias.linkname = target
            archive.addfile(alias)
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr("General/dconf-settings.ini", "settings")
            archive.writestr("General/icon-themes.tgz", nested.getvalue())
            archive.writestr("Custom_Dirs/icons/" + image_name, b"svg")
            archive.writestr(link, target)
        with prepare_backup(self.archive) as root:
            restore_tree(root / "Custom_Dirs/icons", self.workspace / "zip-icons")
            restore_tar(root / "General/icon-themes.tgz", self.workspace / "tar-icons", single_folder=True)
        for directory in ("zip-icons", "tar-icons"):
            restored = self.workspace / directory / link_name
            self.assertEqual(str(restored.readlink()), target)
            self.assertEqual(restored.read_bytes(), b"svg")

    def test_rejects_escape_through_existing_destination_chain(self):
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr("General/dconf-settings.ini", "settings")
            alias = zipfile.ZipInfo("Custom_Dirs/icons/Theme/alias")
            alias.create_system = 3
            alias.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(alias, "redirect/application-menu.svg")
        destination = self.workspace / "destination"
        (destination / "Theme").mkdir(parents=True)
        (destination / "Theme/redirect").symlink_to("../../outside")
        with prepare_backup(self.archive) as root:
            with self.assertRaisesRegex(ValueError, "Symbolic link leaves"):
                restore_tree(root / "Custom_Dirs/icons", destination)
        self.assertFalse((destination / "Theme/alias").is_symlink())

    def test_rejects_archive_link_loop(self):
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr("General/dconf-settings.ini", "settings")
            for name, target in (("first", "second"), ("second", "first")):
                alias = zipfile.ZipInfo("Custom_Dirs/icons/" + name)
                alias.create_system = 3
                alias.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(alias, target)
        with self.assertRaisesRegex(ValueError, "Symbolic link loop"):
            with prepare_backup(self.archive):
                self.fail("Cyclic archive link passed preflight")
