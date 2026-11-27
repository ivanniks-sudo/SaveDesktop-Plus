import os
import sys
from pathlib import Path

from gi.repository import Adw, Gio, Gtk, GLib, Pango

from savedesktop.core.custom_dirs import DestinationAccessError, check_destination, destination_from_grant, folder_destination, original_path, validate_folder_plan
from savedesktop.globals import flatpak, home
from savedesktop.gui.restore_help import help_button


class FolderDestinations(Gtk.Box):
    def __init__(self, parent, folders, cinnamon, changed, saved=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.changed = changed
        saved = saved if isinstance(saved, dict) else {}
        self.parent = parent
        self.folders = folders
        self.rows = []
        if len(folders) > 1:
            actions = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, row_spacing=4, column_spacing=4)
            for label, active in ((_("Select all"), True), (_("Clear selection"), False)):
                button = Gtk.Button(label=label, css_classes=["flat"])
                button.connect("clicked", lambda _button, value=active: self.select_all(value))
                actions.append(button)
            self.append(actions)
        self.count = Gtk.Label(xalign=0, css_classes=["dim-label"])
        self.count.set_visible(len(folders) > 1)
        self.append(self.count)
        listing = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        self.append(Gtk.ScrolledWindow(
            child=listing, hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True, max_content_height=350,
        ))
        for folder in folders:
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                          margin_top=8, margin_bottom=8, margin_start=12, margin_end=12)
            heading = Gtk.Box(spacing=8)
            selected = Gtk.CheckButton()
            selected.update_property([Gtk.AccessibleProperty.LABEL], [folder["name"]])
            heading.append(selected)
            name = Gtk.Label(label=folder["name"], xalign=0, wrap=True)
            heading.append(name)
            row.append(heading)
            original = folder.get("original_path")
            if original:
                row.append(Gtk.Label(label=original, xalign=0, wrap=True, selectable=True, css_classes=["dim-label"]))
            target = Gtk.Box(spacing=6)
            entry = Gtk.Entry(hexpand=True, width_chars=8, placeholder_text=_("Destination folder"))
            identity = self._identity(folder)
            previous = saved.get(identity, {}) if identity else {}
            fields = ("path", "grant")
            if not isinstance(previous, dict) or any(not isinstance(previous.get(key, ""), str) for key in fields):
                previous = {}
            entry.set_text(previous.get("path") or folder_destination(folder, home, cinnamon))
            target.append(entry)
            choose = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text=_("Choose folder"))
            target.append(choose)
            target.append(help_button(self.parent, "destination", _("Destination folder")))
            row.append(target)
            full_path = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=True, css_classes=["dim-label"])
            row.append(full_path)
            status = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label"])
            row.append(status)
            record = {"folder": folder, "selected": selected, "entry": entry, "status": status, "grant": None, "full_path": full_path}
            self.rows.append(record)
            grant = previous.get("grant")
            if grant:
                location = original_path(grant, flatpak=flatpak)
                if location == entry.get_text():
                    record["grant"] = grant
            self._update(record)
            # Unknown or external destinations need an explicit user choice.
            if original and not folder.get("home_relative") and not previous:
                entry.set_text("")
                self._update(record)
            selected.set_active(previous.get("selected", True) is True)
            entry.connect("changed", self._edited, record)
            choose.connect("clicked", self._choose, record)
            selected.connect("toggled", lambda _button: self._update_apply())
            listing.append(row)
        self._update_count()

    def select_all(self, active):
        for row in self.rows:
            row["selected"].set_active(active)

    def _update_count(self):
        self.count.set_label(_("Selected: {count}").format(
            count=sum(row["selected"].get_active() for row in self.rows)))

    @staticmethod
    def _identity(folder):
        if folder.get("home_relative"):
            return "home:" + folder["home_relative"]
        if folder.get("original_path"):
            return "absolute:" + folder["original_path"]
        return None

    def preferences(self):
        return {
            self._identity(row["folder"]): {
                "path": row["entry"].get_text(),
                "grant": row["grant"] or "",
                "selected": row["selected"].get_active(),
            }
            for row in self.rows if self._identity(row["folder"])
        }

    def reset(self, cinnamon):
        for row in self.rows:
            folder = row["folder"]
            path = folder_destination(folder, home, cinnamon)
            if folder.get("original_path") and not folder.get("home_relative"):
                path = ""
            row["entry"].set_text(path)
            row["grant"] = None
            self._update(row)
            row["selected"].set_active(True)
        self._update_apply()

    def _edited(self, entry, record):
        record["grant"] = None
        if self._update(record):
            record["selected"].set_active(True)
        self._update_apply()

    def _update(self, record, *, probe_portal=False):
        text = os.path.expanduser(record["entry"].get_text())
        record["entry"].set_tooltip_text(text)
        record["full_path"].set_text(text)
        try:
            path = check_destination(record["grant"] or text, probe_portal=probe_portal)
        except ValueError as error:
            record["error"] = self._destination_error(error, text)
            record["status"].set_text(record["error"])
            return False
        record["error"] = None
        record["status"].set_text("" if path.exists() else _("This folder will be created."))
        return True

    def _initial_folder(self, path):
        target = Path(path)
        if not target.is_absolute() or ".." in target.parts:
            return None
        return next((folder for folder in (target, *target.parents)
                     if folder != Path("/") and os.path.isdir(folder)), None)

    def _destination_error(self, error, path):
        if not flatpak or not isinstance(error, DestinationAccessError):
            return str(error)
        print(f"Custom folder destination: {error}", file=sys.stderr)
        if self._initial_folder(path):
            return _("To restore this folder, grant access. Click the folder icon on the right, then click Select in the window that opens.")
        return _("To restore this folder, grant access. Click the folder icon and select the destination folder or its nearest existing parent.")

    def _choose(self, button, record):
        chooser = Gtk.FileDialog(title=_("Choose folder"), modal=True)
        path = os.path.expanduser(record["entry"].get_text())
        initial = self._initial_folder(path)
        if initial:
            chooser.set_initial_folder(Gio.File.new_for_path(str(initial)))

        def chosen(source, result, _data):
            try:
                access = source.select_folder_finish(result).get_path()
            except GLib.Error:
                return
            if access:
                location = original_path(access, flatpak=flatpak)
                grant, display = destination_from_grant(path, access, location)
                record["entry"].set_text(display or "")
                record["grant"] = grant
                if self._update(record, probe_portal=True):
                    if not location:
                        record["status"].set_text(_("Access granted to the selected folder. Its original location is unavailable."))
                    record["selected"].set_active(True)
                self._update_apply()

        chooser.select_folder(self.parent, None, chosen, None)

    def _plan(self):
        return {
            row["folder"]["archive_path"]: row["grant"] or os.path.expanduser(row["entry"].get_text())
            for row in self.rows if row["selected"].get_active()
        }

    def validate(self, *, probe=False):
        for record in self.rows:
            if record["selected"].get_active() and record["error"]:
                raise ValueError(record["error"])
        return validate_folder_plan(self.folders, self._plan(), probe_portal=probe)

    def _update_apply(self):
        self._update_count()
        self.changed()


class RestoreFoldersDialog(Adw.AlertDialog):
    """Standalone destination editor for callers outside the restore flow."""

    def __init__(self, parent, folders, cinnamon, finished):
        super().__init__(heading=_("Restore custom folders"))
        self.parent = parent
        self.finished = finished
        self.destinations = FolderDestinations(parent, folders, cinnamon, self._update_apply)
        self.rows = self.destinations.rows
        self.set_extra_child(self.destinations)
        self.add_response("cancel", _("Cancel"))
        self.add_response("ok", _("Restore"))
        self.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        self.connect("response", self._respond)
        self._update_apply()

    def _plan(self):
        return self.destinations._plan()

    def _update_apply(self):
        try:
            self.destinations.validate()
        except ValueError as error:
            self.set_response_enabled("ok", False)
            self.set_body(str(error))
        else:
            self.set_response_enabled("ok", True)
            self.set_body(_("Confirm a destination for each selected folder."))

    def _respond(self, dialog, response):
        if response != "ok":
            self.finished(None)
            return
        try:
            plan = self.destinations.validate(probe=True)
        except ValueError as error:
            self.finished(None)
            notice = Adw.AlertDialog.new(_("An error occurred"), str(error))
            notice.add_response("close", _("Close"))
            notice.present(self.parent)
        else:
            self.finished(plan)
