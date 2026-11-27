import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

# Use the installed module layout without initializing application GSettings.
package = types.ModuleType("savedesktop")
package.__path__ = [str(Path(__file__).resolve().parents[1] / "src")]
sys.modules.setdefault("savedesktop", package)

from savedesktop.core.custom_dirs import destination_from_grant, original_path
from savedesktop.core.desktop_configuration import queue_desktop_configuration
from savedesktop.core.nemo_session_restore import apply_pending, read_keyfile
from savedesktop.core.operation_notices import operation_notices
from savedesktop.core.wallpaper import image_path, restore_wallpaper


class WallpaperDestinations(unittest.TestCase):
    def test_portal_parent_grant_appends_suffix_for_both_mounts(self):
        for mount in ("/run/flatpak/doc", "/run/user/1000/doc"):
            with self.subTest(mount=mount):
                exported = f"{mount}/e2b5667e/sm-test"
                with patch("os.getxattr", return_value=b"/home/sm-test\x00") as read:
                    location = original_path(exported, flatpak=True)
                    access, host = destination_from_grant("/home/sm-test/Новые обои", exported, location)
                    self.assertEqual(access, exported + "/Новые обои")
                    self.assertEqual(host, original_path(access, flatpak=True))
                    self.assertEqual(read.call_args.args[0], Path(exported))

    def test_wallpaper_uses_host_path_and_declines_unresolved_grant(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            access = root / "access"
            access.mkdir()
            photo = "Фото 1.jpg"
            (access / photo).touch()
            (root / "General").mkdir()
            folder = "Custom_Dirs/Wallpaper"
            (root / "General/Wallpaper.json").write_text(
                json.dumps({"folder": folder, "relative": photo}), encoding="utf-8"
            )
            settings = "[org/cinnamon/desktop/background]\npicture-uri='file:///home/sm/old.jpg'\n"
            plan = {folder: str(access)}
            for target in (access, Path("/home/sm-test/Новые обои")):
                output = restore_wallpaper(root, settings, [], plan, {folder: str(target)}, root, root)
                self.assertEqual(image_path(output), target / photo)
                self.assertIn("%20", output)
                self.assertNotIn("Фото", output)
            for host in (None, "/run/flatpak/doc/e2b5667e/sm"):
                with patch("savedesktop.core.wallpaper.warn") as warn:
                    self.assertEqual(restore_wallpaper(root, settings, [], plan, {folder: host}, root, root), "")
                    warn.assert_called_once()


class NemoMetadata(unittest.TestCase):
    def test_legacy_metadata_waits_for_next_session_and_preserves_devices(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / ".config"
            dest = config / "nemo/desktop-metadata"
            dest.parent.mkdir(parents=True)
            dest.write_text("[home]\nnemo-icon-position=1,2\n[USB.volume]\nmonitor=1\n")
            backup = root / "DE/nemo/desktop-metadata"
            backup.parent.mkdir(parents=True)
            backup.write_text(
                "[desktop-monitor-0]\nnemo-icon-view-zoom-level=4\n"
                "[home]\nnemo-icon-position=40,30\nmonitor=0\n"
                "[trash]\nnemo-icon-position=1753,631\nmonitor=1\n"
                "[foreign.volume]\nmonitor=2\n"
            )
            app = "io.github.ivanniks_sudo.SaveDesktopPlus"
            with patch("savedesktop.core.desktop_configuration.warn"):
                queue_desktop_configuration(root, config, app, [(1024, 768)])
            self.assertEqual(read_keyfile(dest).get_value("home", "nemo-icon-position"), "1,2")
            with dest.open("a") as live:
                live.write("[phone.volume]\nmonitor=0\n")
            pending = config / app / "nemo-desktop-pending.json"
            apply_pending(pending, dest)
            data = read_keyfile(dest)
            self.assertEqual(data.get_integer("desktop-monitor-0", "nemo-icon-view-zoom-level"), 4)
            self.assertEqual(data.get_value("home", "nemo-icon-position"), "40,30")
            self.assertEqual(data.get_integer("trash", "monitor"), 0)
            self.assertNotIn("nemo-icon-position", data.get_keys("trash")[0])
            self.assertTrue(data.has_group("USB.volume"))
            self.assertTrue(data.has_group("phone.volume"))
            self.assertFalse(data.has_group("foreign.volume"))
            self.assertFalse(pending.exists())
            apply_pending(pending, dest)


class Notices(unittest.TestCase):
    def test_mesa_diagnostics_are_not_a_functional_notice(self):
        log = 'MESA-EGL: warning: DRI3 error\nSDPLUS-NOTICE "Could not save wallpaper"\n'
        self.assertEqual(operation_notices(log), "Could not save wallpaper")
