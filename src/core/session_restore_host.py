"""User-launched helper for the current host desktop session."""
from contextlib import redirect_stdout
from gettext import translation
import io
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import UUID
from threading import Thread
from time import monotonic, sleep, time

import gi
from gi.repository import Gio, GLib

APP_ID = "io.github.ivanniks_sudo.SaveDesktopPlus"
NEMO_BUS = "org.NemoDesktop"
ROOT = Path(__file__).resolve().parent
HOME = Path.home()
STATE = HOME / ".config" / APP_ID
SERVICE = APP_ID + ".RestoreHost"
OBJECT = "/" + SERVICE.replace(".", "/")
PROTOCOL = 2
_ = translation("savedesktop", localedir=ROOT / "locale", fallback=True).gettext


def bus_call(bus, method, name):
    return bus.call_sync(
        "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
        method, GLib.Variant("(s)", (name,)), None, Gio.DBusCallFlags.NONE, 1000, None,
    ).unpack()[0]


def owner(bus):
    try:
        return bus_call(bus, "GetNameOwner", NEMO_BUS)
    except GLib.Error as error:
        if Gio.DBusError.get_remote_error(error) == "org.freedesktop.DBus.Error.NameHasNoOwner":
            return None
        raise


def wait_for_owner(bus, previous, seconds):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        current = owner(bus)
        if current and current != previous:
            return current
        sleep(0.1)
    return None


def set_icons(settings, enabled):
    if not settings.set_boolean("show-desktop-icons", enabled):
        raise RuntimeError("Nemo desktop visibility setting was refused")
    Gio.Settings.sync()


def desktop_process_fd(bus, name):
    pid = bus_call(bus, "GetConnectionUnixProcessID", name)
    if Path(f"/proc/{pid}/exe").resolve(strict=True) != Path("/usr/bin/nemo-desktop").resolve(strict=True):
        raise RuntimeError("The desktop bus owner is not nemo-desktop")
    return os.pidfd_open(pid)


def refresh_nemo(pending, snapshot=None):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from nemo_session_restore import NEMO_FILE, _apply_pending, pending_lock

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    schema = Gio.SettingsSchemaSource.get_default().lookup("org.nemo.desktop", True)
    if schema is None or not schema.has_key("show-desktop-icons"):
        raise RuntimeError("Nemo desktop settings are unavailable")
    settings = Gio.Settings.new_full(schema, None, None)
    if any(path.is_symlink() for path in (pending.parent, *pending.parent.parents)):
        raise ValueError("Nemo restore state cannot traverse a symbolic link")
    with pending_lock(pending.parent):
        if not pending.is_file():
            if snapshot is not None:
                raise ValueError("Nemo restore request no longer exists")
            return
        if snapshot is not None and read_request(pending) != snapshot:
            raise ValueError("The Nemo restore request has changed")
        if not settings.get_boolean("show-desktop-icons"):
            raise RuntimeError("Nemo desktop icons are disabled")
        previous = owner(bus)
        if not previous:
            raise RuntimeError("The current Nemo desktop process cannot be identified")
        destination = pending.parent.parent / "nemo" / NEMO_FILE
        process = desktop_process_fd(bus, previous)
        try:
            stopped = select.poll()
            stopped.register(process, select.POLLIN)
            if owner(bus) != previous:
                raise RuntimeError("Nemo desktop changed before the refresh")
            set_icons(settings, False)
            try:
                if not stopped.poll(8000) or owner(bus) is not None:
                    raise RuntimeError("The session manager did not stop the old Nemo desktop")
                _apply_pending(pending, destination, consume=False, snapshot=snapshot)
                expected = destination.read_bytes()
            finally:
                set_icons(settings, True)
                current = wait_for_owner(bus, previous, 5)
                if not current and owner(bus) is None:
                    # GApplication owns org.NemoDesktop before opening its desktop.
                    subprocess.Popen(("/usr/bin/nemo-desktop",), start_new_session=True,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    current = wait_for_owner(bus, previous, 5)
            if not current or destination.read_bytes() != expected:
                raise RuntimeError("The new Nemo desktop or its restored metadata could not be verified")
            if snapshot is not None and read_request(pending) != snapshot:
                raise ValueError("The Nemo request changed during application")
            pending.unlink()
        finally:
            os.close(process)


def prompt(message, action, fallback):
    dialog = Gtk.MessageDialog(
        message_type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.NONE, text=message,
    )
    dialog.set_title("SaveDesktop Plus")
    dialog.add_button(fallback, Gtk.ResponseType.CANCEL)
    dialog.add_button(action, Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.CANCEL)
    accepted = dialog.run() == Gtk.ResponseType.OK
    dialog.destroy()
    return accepted


def show_result(text):
    dialog = Gtk.MessageDialog(
        message_type=Gtk.MessageType.INFO, buttons=Gtk.ButtonsType.OK, text=text,
    )
    dialog.set_title("SaveDesktop Plus")
    dialog.run()
    dialog.destroy()


def install_host():
    target = HOME / ".local/libexec" / APP_ID
    service_dir = HOME / ".local/share/dbus-1/services"
    if Path(GLib.get_user_data_dir()) != HOME / ".local/share":
        raise RuntimeError("D-Bus activation requires the standard XDG data directory")
    for destination in (target, service_dir):
        if any(path.is_symlink() for path in (destination, *destination.parents)):
            raise RuntimeError("A symbolic link prevents isolation of the restore service")
    target.mkdir(parents=True, exist_ok=True)
    for name in ("session_restore_host.py", "nemo_session_restore.py", "flatpaks_installer.py", "backup_input.py"):
        temporary = target / (name + ".new")
        shutil.copyfile(ROOT / name, temporary)
        temporary.replace(target / name)
    locale = ROOT / "locale"
    if locale.is_dir():
        shutil.copytree(locale, target / "locale", dirs_exist_ok=True)
    service_dir.mkdir(parents=True, exist_ok=True)
    helper = str(target / "session_restore_host.py")
    service = service_dir / (SERVICE + ".service")
    temp_service = service.with_suffix(".tmp")
    temp_service.write_text(
        f"[D-BUS Service]\nName={SERVICE}\nExec=/usr/bin/python3 -I {GLib.shell_quote(helper)} --dbus\n",
        encoding="utf-8",
    )
    temp_service.replace(service)
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                      "ReloadConfig", None, None, Gio.DBusCallFlags.NONE, 1000, None)
    except GLib.Error as error:
        print(f"Refreshing session bus configuration: {error}", file=sys.stderr)
    return helper


def remove_host():
    if not prompt(
        _("Remove the current-session restore service? Pending sign-in restores will be kept."),
        _("Remove the restore service"), _("Cancel"),
    ):
        return
    base = HOME / ".local"
    service = base / "share/dbus-1/services" / (SERVICE + ".service")
    installed = base / "libexec" / APP_ID
    launcher = base / "share/applications" / (SERVICE + ".desktop")
    for destination in (service, installed, launcher):
        if any(path.is_symlink() for path in (destination, *destination.parents)):
            raise ValueError("The restore service path contains a symbolic link")
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    if bus_call(bus, "NameHasOwner", SERVICE):
        raise RuntimeError("Close the running restore service before removing it")
    service.unlink(missing_ok=True)
    launcher.unlink(missing_ok=True)
    if installed.is_dir():
        shutil.rmtree(installed)
    show_result(_("The current-session restore service was removed."))


def read_request(path):
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("Restore request path contains a symbolic link")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        import stat
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4 * 1024 * 1024:
            raise ValueError("Invalid restore request file")
        return stream.read(4 * 1024 * 1024 + 1)


def current_request(path, job):
    content = read_request(path)
    if json.loads(content).get("job") != job or time() - path.stat().st_mtime > 3600:
        raise ValueError("The prepared restore is no longer current")
    return content


def snapshot_flatpaks(workspace, snapshot, preferences):
    from backup_input import restore_file, restore_tree
    if (workspace / "flatpak-prefs.json").is_symlink():
        raise ValueError("Invalid Flatpak restore preferences")
    (snapshot / "flatpak-prefs.json").write_bytes(preferences)
    for name in ("Flatpak_Apps", "app"):
        source = workspace / name
        if source.is_symlink():
            raise ValueError(f"Invalid Flatpak restore directory: {name}")
        if source.is_dir():
            restore_tree(source, snapshot / name)
    for name in ("installed_flatpaks.sh", "installed_user_flatpaks.sh",
                 "flatpak-apps-data.tgz", "flatpak-completed.json"):
        source = workspace / name
        if source.is_symlink():
            raise ValueError(f"Invalid Flatpak restore file: {name}")
        if source.is_file():
            restore_file(source, snapshot / name)


def run_confirmed(job, nemo, flatpaks, confirm, event, protected):
    from contextlib import nullcontext
    from flatpaks_installer import restore, restore_lock, save_completed

    pending = STATE / "nemo-desktop-pending.json"
    workspace = HOME / ".var/app" / APP_ID / "cache/tmp/workspace"
    prefs_path = workspace / "flatpak-prefs.json"
    consumed = protected / "confirmed-jobs"
    consumed.mkdir(mode=0o700, exist_ok=True)
    if str(UUID(job)) != job or (consumed / job).exists() or not (nemo or flatpaks):
        raise ValueError("Invalid or previously processed restore")
    nemo_status, flatpak_status = "none", "none"
    if flatpaks and any(path.is_symlink() for path in (workspace, *workspace.parents)):
        raise ValueError("Symbolic link in the Flatpak restore workspace")
    with (restore_lock(workspace.parent) if flatpaks else nullcontext(),
          TemporaryDirectory(prefix="restore-", dir=protected) as temporary):
        prefs = current_request(prefs_path, job) if flatpaks else None
        metadata = current_request(pending, job) if nemo else None
        snapshot = Path(temporary)
        if flatpaks:
            if (workspace / "flatpak-succeeded").exists():
                raise ValueError("Flatpak restore has already completed")
            event("prepare_apps", "")
            snapshot_flatpaks(workspace, snapshot, prefs)
        # This receipt lives outside every writable sandbox mount. Even a partial
        # failure cannot be re-executed by calling the service again with this ID.
        with (consumed / job).open("x"):
            pass
        if not confirm(nemo, json.loads(prefs) if prefs is not None else {}):
            return "deferred" if nemo else "none", "deferred" if flatpaks else "none"
        if nemo:
            event("desktop_layout", "")
            try:
                refresh_nemo(pending, metadata)
            except (OSError, ValueError, RuntimeError, GLib.Error) as error:
                event("log", str(error))
                nemo_status = "deferred"
            else:
                nemo_status = "done"
        if flatpaks:
            log = io.StringIO()
            try:
                with redirect_stdout(log):
                    result = restore(snapshot, HOME, event)
                if read_request(prefs_path) != prefs:
                    raise ValueError("The Flatpak restore request has changed")
                journal = snapshot / "flatpak-completed.json"
                if journal.is_file():
                    save_completed(workspace, set(json.loads(journal.read_text(encoding="utf-8"))))
                if result == "success":
                    (workspace / "flatpak-succeeded").touch(exist_ok=False)
                    (HOME / ".config/autostart" / (APP_ID + ".Flatpak.desktop")).unlink(missing_ok=True)
                flatpak_status = "done" if result == "success" else "partial"
            except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
                event("log", str(error))
                flatpak_status = "failed"
            finally:
                if log.getvalue():
                    event("log", log.getvalue())
    return nemo_status, flatpak_status


def confirm_restore(nemo, prefs):
    lines = [_("Apply the selected changes in this session?")]
    if nemo:
        lines.append(_("Before restoring, finish all copy and move operations."))
    if prefs.get("copy-data"):
        lines.append(_("Close apps whose data will be restored and keep them closed until the restore finishes."))
    if prefs.get("install-flatpaks") and not prefs.get("keep-flatpaks", True):
        lines.append(_("Flatpak apps absent from the backup may be uninstalled."))
    return prompt("\n\n".join(lines), _("Apply"), _("At next sign-in"))


def serve():
    xml = f"""<node><interface name="{SERVICE}">
      <method name="GetProtocol"><arg type="u" direction="out"/></method>
      <method name="StartRestore">
        <arg type="s" direction="in"/><arg type="b" direction="in"/><arg type="b" direction="in"/>
        <arg type="s" direction="out"/><arg type="s" direction="out"/>
      </method>
      <signal name="Progress"><arg type="s"/><arg type="s"/><arg type="s"/></signal>
    </interface></node>"""
    interface = Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0]
    loop = GLib.MainLoop()
    busy = False

    def request(connection, sender, path, iface, method, args, invocation):
        nonlocal busy
        if method == "GetProtocol":
            invocation.return_value(GLib.Variant("(u)", (PROTOCOL,)))
            return
        if busy:
            invocation.return_dbus_error(SERVICE + ".Busy", "A restore is already running")
            return
        busy = True
        job, nemo, flatpaks = args.unpack()

        def event(stage, detail):
            # Limit individual diagnostic messages to keep the D-Bus payload bounded.
            connection.emit_signal(sender, OBJECT, SERVICE, "Progress",
                                   GLib.Variant("(sss)", (job, stage, detail[-60000:])))

        def confirm(*components):
            from threading import Event
            answered = Event()
            approved = False

            def show():
                nonlocal approved
                try:
                    approved = confirm_restore(*components)
                finally:
                    answered.set()
                return False

            GLib.idle_add(show)
            answered.wait()
            return approved

        def worker():
            try:
                result = run_confirmed(job, nemo, flatpaks, confirm, event, ROOT)
            except Exception as error:
                event("log", str(error))
                result = ("deferred" if nemo else "none", "deferred" if flatpaks else "none")
            invocation.return_value(GLib.Variant("(ss)", result))
            GLib.idle_add(loop.quit)

        Thread(target=worker, daemon=True).start()

    def acquired(connection, _name):
        connection.register_object(OBJECT, interface, request, None, None)

    identifier = Gio.bus_own_name(Gio.BusType.SESSION, SERVICE, Gio.BusNameOwnerFlags.NONE,
                                  acquired, None, lambda *_: loop.quit())
    GLib.timeout_add_seconds(30, lambda: False if busy else loop.quit())
    loop.run()
    Gio.bus_unown_name(identifier)


def main():
    global Gtk
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    if "--bootstrap" in sys.argv[1:]:
        try:
            install_host()
        except (OSError, RuntimeError) as error:
            print(f"Restore service setup: {error}", file=sys.stderr)
        else:
            return
        show_result(_("The restore helper could not be set up."))
        return
    if "--remove-service" in sys.argv[1:]:
        try:
            remove_host()
        except (OSError, ValueError, RuntimeError, GLib.Error) as error:
            show_result(str(error))
    elif "--dbus" in sys.argv[1:]:
        sys.path.insert(0, str(ROOT))
        serve()


if __name__ == "__main__":
    main()
