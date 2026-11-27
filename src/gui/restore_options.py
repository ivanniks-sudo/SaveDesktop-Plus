"""Wizard and manual views of the same restore choices."""
import json

from gi.repository import Adw, Gtk

from savedesktop.globals import home, settings
from savedesktop.gui.restore_folders_dialog import FolderDestinations
from savedesktop.gui.restore_help import article_text, fit_restore_dialog, help_button


class RestoreOptions(Adw.AlertDialog):
    def __init__(self, parent, archive, paths, folders, available, cinnamon, finished, needs_host):
        super().__init__(content_width=330)
        self.parent = parent
        fit_restore_dialog(self, parent)
        self.finished = finished
        self.needs_host = needs_host
        self.cinnamon = cinnamon
        self.on_destinations = False
        self.controls = {}
        self.apps = {}

        try:
            saved = json.loads(settings["restore-choices"])
        except ValueError:
            saved = {}
        if not isinstance(saved, dict):
            saved = {}
        self.saved_options = saved.get("options", {})
        if not isinstance(self.saved_options, dict):
            self.saved_options = {}

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        details = Gtk.Box(spacing=6)
        details.append(Gtk.Label(label=archive, xalign=0, wrap=True, hexpand=True, selectable=True))
        details.append(help_button(parent, "archive", _("Backup")))
        content.append(details)
        header = Gtk.Box(spacing=6)
        self.mode = Gtk.DropDown.new_from_strings([_("Wizard"), _("Manual mode")])
        self.mode.set_selected(1 if settings["restore-mode"] == "manual" else 0)
        self.mode.set_hexpand(True)
        self.mode.update_property([Gtk.AccessibleProperty.LABEL], [_("Restore mode")])
        header.append(self.mode)
        header.append(help_button(parent, "mode", _("Restore mode")))
        content.append(header)

        self.pages = Gtk.Stack()
        self.components = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        core = self._list()
        self.components.append(core)
        self._choice(core, "system_settings", _("Desktop environment settings"), True)
        self._choice(core, "wallpaper", _("Wallpaper"), True, available["wallpaper"])
        self._choice(core, "folders", _("My files and folders"), False, bool(folders))
        self._choice(core, "desktop_configuration", _("Desktop settings"), True,
                     cinnamon and available["configuration"])
        self._choice(core, "desktop_contents", _("Desktop files and folders"), False, available["contents"])
        self._choice(core, "flatpak_apps", _("Restore apps"), False, available["flatpak_apps"])
        self._choice(core, "flatpak_data", _("Restore app settings and data"), False, available["flatpak_data"])

        self.destination_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        remembered_destinations = saved.get("folders") if saved.get("home") == str(home) else None
        self.destinations = FolderDestinations(parent, folders, cinnamon, self.refresh, remembered_destinations)
        self.destination_box.append(self.destinations)
        self.destination_expander = Gtk.Expander(label=_("Folder destinations"))
        self.components.append(self.destination_expander)
        advanced = Gtk.Expander(label=_("Additional restore settings"))
        advanced.set_expanded(settings["restore-expand-options"])
        advanced.connect("notify::expanded", lambda expander, _: settings.set_boolean(
            "restore-expand-options", expander.get_expanded()))
        extras = self._list()
        for key, article, title, candidates in (
            ("save-icons", "icons", _("Icons"), ("icon-themes.tgz", "icon-themes-legacy.tgz")),
            ("save-themes", "themes", _("Themes"), (".themes", "themes")),
            ("save-fonts", "fonts", _("Fonts"), ("fonts", ".fonts")),
            ("save-bookmarks", "bookmarks", _("File manager bookmarks"), ("gtk-3.0",)),
            ("save-extensions", "extensions", _("Extensions"), ("cinnamon", "gnome-shell", "plasma")),
        ):
            self._choice(extras, key, title, True, any(term in path.split("/") for path in paths for term in candidates),
                         article=article)
        ids = available.get("application_ids", ()) if available["flatpak_apps"] else ()
        if ids:
            self._add_app_selection(ids, saved)
        self._choice(extras, "keep_flatpaks", _("Keep existing Flatpak apps and data"), True,
                     available["flatpak_apps"], remember=False)
        self._choice(extras, "remember", _("Remember restore choices"), settings["restore-remember"], remember=False)
        advanced.set_child(extras)
        self.components.append(advanced)

        reset = Gtk.Button(label=_("Use recommended settings"), halign=Gtk.Align.START, css_classes=["flat"])
        reset.connect("clicked", self._reset)
        self.components.append(reset)
        self.pages.add_named(self.components, "components")
        self.destinations_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        back = Gtk.Button(label=_("Back"), halign=Gtk.Align.START)
        back.connect("clicked", lambda _: self._folder_view(False))
        self.destinations_page.append(back)
        self.pages.add_named(self.destinations_page, "destinations")
        scroll = Gtk.ScrolledWindow(child=self.pages, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    propagate_natural_height=True, max_content_height=360)
        content.append(scroll)
        self.warnings = Gtk.Label(xalign=0, wrap=True, css_classes=["warning"])
        content.append(self.warnings)
        self.continue_folders = Gtk.Button(label=_("Continue"), halign=Gtk.Align.END,
                                           css_classes=["suggested-action"])
        self.continue_folders.connect("clicked", lambda _: self._folder_view(True))
        content.append(self.continue_folders)
        self.set_extra_child(Gtk.ScrolledWindow(child=content, propagate_natural_height=True,
                                                max_content_height=510, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.add_response("cancel", _("Cancel"))
        self.add_response("ok", _("Apply"))
        self.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        self.connect("response", self._respond)
        self.mode.connect("notify::selected", self._change_mode)
        self._change_mode(self.mode, None)

    def _add_app_selection(self, ids, saved):
        self.app_expander = Gtk.Expander(label=_("Choose applications"))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        actions = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE)
        for label, active in ((_("Select all"), True), (_("Clear selection"), False)):
            button = Gtk.Button(label=label, css_classes=["flat"])
            button.connect("clicked", lambda _button, value=active: self._select_apps(value))
            actions.append(button)
        actions.append(help_button(self.parent, "flatpak_apps", _("Choose applications")))
        box.append(actions)
        self.app_count = Gtk.Label(xalign=0, css_classes=["dim-label"])
        box.append(self.app_count)
        excluded = saved.get("excluded_apps", settings.get_strv("disabled-flatpak-apps-data"))
        if not isinstance(excluded, list):
            excluded = []
        listing = self._list()
        for app in ids:
            selected = Gtk.CheckButton(active=app not in excluded, valign=Gtk.Align.CENTER)
            selected.update_property([Gtk.AccessibleProperty.LABEL], [app])
            row = Adw.ActionRow(title=app, title_lines=3, activatable_widget=selected)
            row.add_suffix(selected)
            listing.append(row)
            self.apps[app] = selected
            selected.connect("toggled", self._count_apps)
        box.append(listing)
        self.app_expander.set_child(box)
        self.components.append(self.app_expander)
        self._count_apps()

    def _select_apps(self, value):
        for selected in self.apps.values():
            selected.set_active(value)

    def _count_apps(self, *_args):
        self.app_count.set_label(_("Selected: {count}").format(
            count=sum(selected.get_active() for selected in self.apps.values())))

    @staticmethod
    def _list():
        return Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])

    def _choice(self, listing, key, title, recommended, available=True, *, article=None, remember=True):
        preference = self.saved_options.get(key, recommended) if remember else recommended
        switch = Gtk.CheckButton(active=bool(available and preference is True), sensitive=available,
                                 valign=Gtk.Align.CENTER)
        switch.update_property([Gtk.AccessibleProperty.LABEL], [title])
        row = Adw.ActionRow(title=title, title_lines=4, subtitle_lines=4)
        if not available:
            row.set_subtitle(_("Unavailable in this backup or on this desktop."))
        button = help_button(self.parent, article or key, title)
        row.add_suffix(button)
        row.add_suffix(switch)
        row.set_activatable_widget(switch)
        listing.append(row)
        self.controls[key] = (switch, recommended, available, row, article or key)
        switch.connect("toggled", self.refresh)

    def _change_mode(self, _widget, _param):
        self.manual = self.mode.get_selected() == 1
        settings["restore-mode"] = "manual" if self.manual else "wizard"
        self._folder_view(False)

    def _folder_view(self, opened):
        self.on_destinations = opened and not self.manual
        if self.on_destinations:
            self.destination_expander.set_child(None)
            self.destinations_page.append(self.destination_box)
        elif self.destination_box.get_parent() is self.destinations_page:
            self.destinations_page.remove(self.destination_box)
        if not self.on_destinations:
            self.destination_expander.set_child(self.destination_box)
        self.pages.set_visible_child_name("destinations" if self.on_destinations else "components")
        self.refresh()

    def _reset(self, _button):
        for key, (switch, default, available, _, _) in self.controls.items():
            if key != "remember":
                switch.set_active(available and default)
        self.destinations.reset(self.cinnamon)
        self._select_apps(True)
        self.refresh()

    def selected(self, key):
        return self.controls[key][0].get_active()

    def refresh(self, *_args):
        if not hasattr(self, "warnings"):
            return
        self.set_heading(_("Folder destinations") if self.on_destinations else _("What should be restored?"))
        folders = self.selected("folders")
        self.destination_expander.set_visible(folders and not self.on_destinations)
        self.destination_box.set_sensitive(folders)
        if self.apps:
            self.app_expander.set_visible(self.selected("flatpak_apps") or self.selected("flatpak_data"))
        self.controls["keep_flatpaks"][0].set_sensitive(self.selected("flatpak_apps"))
        for key, (button, recommended, available, row, article) in self.controls.items():
            if key.startswith("save-"):
                button.set_sensitive(available and self.selected("system_settings"))
            if available and key != "remember":
                if not self.manual:
                    row.set_subtitle(article_text(article).split(". ", 1)[0] + ".")
                else:
                    row.set_subtitle("")
        messages = []
        if self.selected("desktop_configuration"):
            messages.append(_("Before restoring, finish all copy and move operations."))
        if self.selected("flatpak_data"):
            messages.append(_("Close apps whose data will be restored and keep them closed until the restore finishes."))
        if self.selected("flatpak_apps") and not self.selected("keep_flatpaks"):
            messages.append(_("Apps missing from the backup may be removed. Their personal data will be kept."))
        invalid = False
        if folders:
            try:
                self.destinations.validate()
            except ValueError:
                invalid = True
                messages.append(_("Choose a destination for each selected folder, or clear its check box."))
                if self.manual:
                    self.destination_expander.set_expanded(True)
        self.warnings.set_label("\n\n".join(messages))
        self.warnings.set_visible(bool(messages))
        self.continue_folders.set_visible(invalid and not self.manual and not self.on_destinations)
        self.set_response_enabled("ok", not invalid and (
            any(self.selected(key) for key in ("system_settings", "wallpaper", "desktop_configuration",
                                                "desktop_contents", "flatpak_apps", "flatpak_data"))
            or folders and any(row["selected"].get_active() for row in self.destinations.rows)))

        self.set_response_label("ok", _("Continue") if self.needs_host(self.options()) else _("Apply"))

    def options(self):
        values = {key: button.get_active() for key, (button, *_) in self.controls.items()
                  if key not in ("folders", "remember")}
        if not values["flatpak_apps"]:
            values["keep_flatpaks"] = True
        exclusions = set(settings.get_strv("disabled-flatpak-apps-data")) - self.apps.keys()
        exclusions.update(app for app, selected in self.apps.items() if not selected.get_active())
        values["disabled_flatpaks"] = sorted(exclusions)
        return values

    def _respond(self, _dialog, response):
        if response != "ok":
            self.finished(None)
            return
        try:
            folders = self.destinations.validate(probe=True) if self.selected("folders") else {}
        except ValueError:
            self.present(self.parent)
            self._folder_view(True)
            self.destination_expander.set_expanded(True)
            self.warnings.set_label(_("Folder access has changed. Choose the folder again or skip it."))
            self.warnings.set_visible(True)
            return
        options = self.options()
        options["folders"] = folders
        remember = self.selected("remember")
        settings["restore-remember"] = remember
        if remember:
            remembered = {key: self.selected(key) for key in self.controls if key not in ("keep_flatpaks", "remember")}
            settings["restore-choices"] = json.dumps({"home": str(home), "options": remembered,
                                                       "folders": self.destinations.preferences(),
                                                       "excluded_apps": options["disabled_flatpaks"]})
        else:
            settings["restore-choices"] = "{}"
        self.finished(options)
