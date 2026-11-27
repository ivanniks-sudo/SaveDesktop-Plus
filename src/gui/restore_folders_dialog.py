import os
from pathlib import Path

from gi.repository import Adw, Gio, Gtk, GLib, Pango

from savedesktop.core.custom_dirs import check_destination, destination_from_grant, folder_destination, original_path, validate_folder_plan
from savedesktop.globals import flatpak, home


class RestoreFoldersDialog(Adw.AlertDialog):
    def __init__(self, parent, folders, cinnamon, finished):
        super().__init__()
        self.parent = parent
        self.folders = folders
        self.finished = finished
        self.rows = []
        self.set_heading(_("Restore custom folders"))
        self.set_body(_("Confirm a destination for each selected folder."))
        self.add_response("cancel", _("Cancel"))
        self.add_response("ok", _("Restore"))
        self.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        self.connect("response", self._respond)
        listing = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        self.set_extra_child(Gtk.ScrolledWindow(
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
            entry = Gtk.Entry(hexpand=True, placeholder_text=_("Destination folder"))
            entry.set_text(folder_destination(folder, home, cinnamon))
            target.append(entry)
            choose = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text=_("Choose folder"))
            target.append(choose)
            row.append(target)
            full_path = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=True, css_classes=["dim-label"])
            row.append(full_path)
            status = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label"])
            row.append(status)
            record = {"folder": folder, "selected": selected, "entry": entry, "status": status, "grant": None, "full_path": full_path}
            self.rows.append(record)
            valid = self._update(record)
            # Existing absolute source paths outside the original home require explicit confirmation.
            selected.set_active(valid and (not original or bool(folder.get("home_relative"))))
            entry.connect("changed", self._edited, record)
            choose.connect("clicked", self._choose, record)
            selected.connect("toggled", lambda _button: self._update_apply())
            listing.append(row)
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
            record["error"] = str(error)
            record["status"].set_text(record["error"])
            return False
        record["error"] = None
        record["status"].set_text("" if path.exists() else _("This folder will be created."))
        return True

    def _choose(self, button, record):
        chooser = Gtk.FileDialog(title=_("Choose folder"), modal=True)
        path = os.path.expanduser(record["entry"].get_text())
        if Path(path).is_absolute():
            initial = Path(path) if os.path.isdir(path) else Path(path).parent
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

    def _update_apply(self):
        try:
            for record in self.rows:
                if record["selected"].get_active() and record["error"]:
                    raise ValueError(record["error"])
            validate_folder_plan(self.folders, self._plan())
        except ValueError as error:
            self.set_response_enabled("ok", False)
            self.set_body(str(error))
        else:
            self.set_response_enabled("ok", True)
            self.set_body(_("Confirm a destination for each selected folder."))

    def _respond(self, dialog, response):
        if response == "ok":
            try:
                plan = validate_folder_plan(self.folders, self._plan(), probe_portal=True)
            except ValueError as error:
                self.finished(None)
                notice = Adw.AlertDialog.new(_("An error occurred"), str(error))
                notice.add_response("close", _("Close"))
                notice.present(self.parent)
            else:
                self.finished(plan)
        else:
            self.finished(None)
