from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from gi.repository import Gio, GLib

package = types.ModuleType("savedesktop")
package.__path__ = [str(Path(__file__).resolve().parents[1] / "src")]
sys.modules.setdefault("savedesktop", package)

from savedesktop.core.desktop_configuration import queue_desktop_configuration
from savedesktop.core.nemo_session_restore import apply_pending, read_keyfile
from savedesktop.core.restore_completion import affects_cinnamon_shell, read_completion, restart_cinnamon


class Completion(unittest.TestCase):
    def test_only_shell_configuration_requires_restart(self):
        self.assertTrue(affects_cinnamon_shell("[org/cinnamon]\nenabled-applets=[]"))
        self.assertTrue(affects_cinnamon_shell("[org/cinnamon/theme]\nname='Mint'"))
        self.assertFalse(affects_cinnamon_shell("[org/cinnamon]\n# inactive=1"))
        self.assertFalse(affects_cinnamon_shell(
            "[org/cinnamon/desktop/background]\npicture-uri='file:///photo'\n"
            "[org/nemo/desktop]\nhome-icon-visible=true\n"
        ))

    def test_completion_ignores_unrelated_stderr(self):
        log = 'MESA-EGL warning\nSDPLUS-RESTORE {"nemo_pending": true}\nSDPLUS-RESTORE invalid'
        self.assertEqual(read_completion(log), {"nemo_pending": True})

    def test_only_x11_calls_session_manager_and_does_not_retry(self):
        connection = Mock()
        with patch.object(Gio, "bus_get_sync", return_value=connection) as bus:
            for session in ("wayland", "", "tty"):
                complete = Mock()
                restart_cinnamon(session, complete)
                complete.assert_called_once_with(False)
            bus.assert_not_called()

            complete = Mock()
            restart_cinnamon("x11", complete)
            call = connection.call.call_args.args
            self.assertEqual(call[:4], (
                "org.gnome.SessionManager", "/org/gnome/SessionManager",
                "org.gnome.SessionManager", "RestartCinnamonLauncher",
            ))
            connection.call_finish.side_effect = GLib.Error("Mock D-Bus denial")
            call[-1](connection, object())
            complete.assert_called_once_with(False)
            connection.call.assert_called_once()
            connection.reset_mock()
            connection.call_finish.side_effect = None
            complete.reset_mock()
            restart_cinnamon("x11", complete)
            connection.call.call_args.args[-1](connection, object())
            complete.assert_called_once_with(True)

    def test_last_queued_nemo_import_wins_and_applies_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "General/NemoDesktop.ini"
            source.parent.mkdir()
            config = root / "config"
            app = "io.github.ivanniks_sudo.SaveDesktopPlus"
            for zoom in (3, 4):
                source.write_text(f"[desktop-monitor-0]\nnemo-icon-view-zoom-level={zoom}\n")
                self.assertTrue(queue_desktop_configuration(root, config, app, [(1024, 768)]))
            pending = config / app / "nemo-desktop-pending.json"
            destination = config / "nemo/desktop-metadata"
            self.assertFalse(destination.exists())
            apply_pending(pending, destination)
            self.assertEqual(read_keyfile(destination).get_integer("desktop-monitor-0", "nemo-icon-view-zoom-level"), 4)
            self.assertFalse(pending.exists())
            destination.write_text("[desktop-monitor-0]\nnemo-icon-view-zoom-level=5\n")
            apply_pending(pending, destination)
            self.assertEqual(read_keyfile(destination).get_integer("desktop-monitor-0", "nemo-icon-view-zoom-level"), 5)
