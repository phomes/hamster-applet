# - coding: utf-8 -

# Copyright (C) 2007-2009 Toms Bauģis <toms.baugis at gmail.com>
# Copyright (C) 2007-2009 Patryk Zawadzki <patrys at pld-linux.org>
# Copyright (C) 2008 Pēteris Caune <cuu508 at gmail.com>

# This file is part of Project Hamster.

# Project Hamster is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# Project Hamster is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with Project Hamster.  If not, see <http://www.gnu.org/licenses/>.

import logging
import datetime as dt

import pygtk
pygtk.require("2.0")
import gtk

import gnomeapplet
import gobject
import dbus, dbus.service, dbus.mainloop.glib

import eds
from configuration import conf, runtime, dialogs

import stuff
import keybinder
from hamsterdbus import HAMSTER_URI, HamsterDbusController

# controllers for other windows
import widgets
import idle

import pango

try:
    import wnck
except:
    logging.warning("Could not import wnck - workspace tracking will be disabled")
    wnck = None

try:
    import pynotify
    pynotify.init('Hamster Applet')
except:
    logging.warning("Could not import pynotify - notifications will be disabled")
    pynotify = None

class PanelButton(gtk.ToggleButton):
    def __init__(self):
        gtk.ToggleButton.__init__(self)
        self.set_relief(gtk.RELIEF_NONE)
        self.set_border_width(0)

        self.label = gtk.Label()
        self.label.set_justify(gtk.JUSTIFY_CENTER)

        self.label.connect('style-set', self.on_label_style_set)
        self.connect('size_allocate', self.on_size_allocate)
        self.connect('button_press_event', self.on_button_press)

        self.add(self.label)

        self.activity, self.duration = None, None
        self.prev_size = 0


        # remove padding, so we fit on small panels (adapted from clock applet)
        gtk.rc_parse_string ("""style "hamster-applet-button-style" {
                GtkWidget::focus-line-width=0
                GtkWidget::focus-padding=0
            }

            widget "*.hamster-applet-button" style "hamster-applet-button-style"
        """);
        gtk.Widget.set_name (self, "hamster-applet-button");


    def set_active(self, is_active):
        self.set_property('active', is_active)

    def set_text(self, activity, duration):
        activity = stuff.escape_pango(activity)
        if len(activity) > 25:  #ellipsize at some random length
            activity = "%s%s" % (activity[:25], "&#8230;")

        self.activity = activity
        self.duration = duration
        self.reformat_label()

    def reformat_label(self):
        label = self.activity
        if self.duration:
            if self.use_two_line_format():
                label = "%s\n%s" % (self.activity, self.duration)
            else:
                label = "%s %s" % (self.activity, self.duration)

        label = '<span gravity="south">%s</span>' % label
        self.label.set_markup("") #clear - seems to fix the warning
        self.label.set_markup(label)

    def use_two_line_format(self):
        if not self.get_parent():
            return False

        popup_dir = self.get_parent().get_orient()

        orient_vertical = popup_dir in [gnomeapplet.ORIENT_LEFT] or \
                          popup_dir in [gnomeapplet.ORIENT_RIGHT]


        context = self.label.get_pango_context()
        metrics = context.get_metrics(self.label.style.font_desc,
                                      pango.Context.get_language(context))
        ascent = pango.FontMetrics.get_ascent(metrics)
        descent = pango.FontMetrics.get_descent(metrics)

        if orient_vertical == False:
            thickness = self.style.ythickness;
        else:
            thickness = self.style.xthickness;

        focus_width = self.style_get_property("focus-line-width")
        focus_pad = self.style_get_property("focus-padding")

        required_size = 2 * ((pango.PIXELS(ascent + descent) ) + 2 * (focus_width + focus_pad + thickness))

        if orient_vertical:
            available_size = self.get_allocation().width
        else:
            available_size = self.get_allocation().height

        return required_size <= available_size

    def on_label_style_set(self, widget, something):
        self.reformat_label()

    def on_size_allocate(self, widget, allocation):
        if not self.get_parent():
            return

        self.popup_dir = self.get_parent().get_orient()

        orient_vertical = True
        new_size = allocation.width
        if self.popup_dir in [gnomeapplet.ORIENT_LEFT]:
            new_angle = 270
        elif self.popup_dir in [gnomeapplet.ORIENT_RIGHT]:
            new_angle = 90
        else:
            new_angle = 0
            orient_vertical = False
            new_size = allocation.height

        if new_angle != self.label.get_angle():
            self.label.set_angle(new_angle)

        if new_size != self.prev_size:
            self.reformat_label()

        self.prev_size = new_size

    def on_button_press(self, widget, event):
        # this allows dragging applet around panel and friends
        if event.button != 1:
            widget.stop_emission('button_press_event')
        return False


class HamsterApplet(object):
    def __init__(self, applet):
        self.applet = applet

        self.button = PanelButton()
        self.button.connect('toggled', self.on_toggle)
        self.applet.add(self.button)

        self.applet.setup_menu_from_file (runtime.data_dir,
                                          "Hamster_Applet.xml",
                                          None,
                                          [("about", self.on_about),
                                          ("overview", self.show_overview),
                                          ("preferences", self.show_preferences),
                                          ("help", self.on_more_info_button_clicked),
                                          ])

        # load window of activity switcher and todays view
        self._gui = stuff.load_ui_file("applet.ui")
        self.window = self._gui.get_object('hamster-window')
        # on close don't destroy the popup, just hide it instead
        self.window.connect("delete_event", lambda *args: self.__show_toggle(None, False))
        self.window.connect("window-state-event", self.on_window_state_changed)

        self.new_name = widgets.ActivityEntry()
        self.new_name.connect("value-entered", self.on_switch_activity_clicked)
        widgets.add_hint(self.new_name, _("Activity"))
        self.get_widget("new_name_box").add(self.new_name)
        self.new_name.connect("changed", self.on_activity_text_changed)

        self.new_tags = widgets.TagsEntry()
        self.new_tags.connect("tags_selected", self.on_switch_activity_clicked)
        widgets.add_hint(self.new_tags, _("Tags"))
        self.get_widget("new_tags_box").add(self.new_tags)

        self.tag_box = widgets.TagBox(interactive = False)
        self.get_widget("tag_box").add(self.tag_box)

        self.treeview = widgets.FactTree()
        self.treeview.connect("key-press-event", self.on_todays_keys)
        self.treeview.connect("edit-clicked", self._open_edit_activity)
        self.treeview.connect("row-activated", self.on_today_row_activated)

        self.get_widget("today_box").add(self.treeview)

        # DBus Setup
        try:
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            name = dbus.service.BusName(HAMSTER_URI, dbus.SessionBus())
            self.dbusController = HamsterDbusController(bus_name = name)

            # Set up connection to the screensaver
            self.dbusIdleListener = idle.DbusIdleListener(runtime.dispatcher)
            runtime.dispatcher.add_handler('active_changed', self.on_idle_changed)

        except dbus.DBusException, e:
            logging.error("Can't init dbus: %s" % e)

        # configuration
        self.timeout_enabled = conf.get("enable_timeout")
        self.notify_on_idle = conf.get("notify_on_idle")
        self.notify_interval = conf.get("notify_interval")
        self.workspace_tracking = conf.get("workspace_tracking")

        self.hotkey = conf.get("keybinding")
        self.bind_hotkey()

        runtime.dispatcher.add_handler('conf_changed', self.on_conf_changed)

        # Load today's data, activities and set label
        self.last_activity = None
        self.load_day()
        self.update_label()

        # Hamster DBusController current fact initialising
        self.__update_fact()

        # refresh hamster every 60 seconds to update duration
        gobject.timeout_add_seconds(60, self.refresh_hamster)

        runtime.dispatcher.add_handler('panel_visible', self.__show_toggle)
        runtime.dispatcher.add_handler('activity_updated', self.after_activity_update)
        runtime.dispatcher.add_handler('day_updated', self.after_fact_update)

        self.screen = None
        if self.workspace_tracking:
            self.init_workspace_tracking()

        self.notification = None
        if pynotify:
            self.notification = pynotify.Notification("Oh hi",
                                                      "Greetings from hamster!")
            self.notification.set_urgency(pynotify.URGENCY_LOW) # lower than grass

        self._gui.connect_signals(self)

        self.prev_size = None


    def bind_hotkey(self):
        try:
            print 'Binding shortcut %s to popup hamster' % self.hotkey
            keybinder.bind(self.hotkey, self.on_keybinding_activated)
        except KeyError:
            pass # don't care


    def init_workspace_tracking(self):
        if not wnck: # can't track if we don't have the trackable
            return

        self.screen = wnck.screen_get_default()
        self.screen.workspace_handler = self.screen.connect("active-workspace-changed", self.on_workspace_changed)
        self.workspace_activities = {}

    """UI functions"""
    def refresh_hamster(self):
        """refresh hamster every x secs - load today, check last activity etc."""
        try:
            #if we the day view is visible - update day's durations
            if self.button.get_active():
                self.load_day()

            self.update_label()
            self.check_user()
        finally:  # we want to go on no matter what, so in case of any error we find out about it sooner
            return True

    def update_label(self):
        if self.last_activity and self.last_activity['end_time'] is None:
            delta = dt.datetime.now() - self.last_activity['start_time']
            duration = delta.seconds /  60
            label = "%s %s" % (self.last_activity['name'],
                               stuff.format_duration(duration, False))
            self.button.set_text(self.last_activity['name'],
                                 stuff.format_duration(duration, False))
        else:
            label = "%s" % _(u"No activity")
            self.button.set_text(label, None)


    def check_user(self):
        if not self.notification:
            return

        if self.notify_interval <= 0 or self.notify_interval >= 121:
            return

        now = dt.datetime.now()
        message = None
        if self.last_activity:
            delta = now - self.last_activity['start_time']
            duration = delta.seconds /  60

            if duration and duration % self.notify_interval == 0:
                message = self.last_activity['name']

        elif self.notify_on_idle:
            #if we have no last activity, let's just calculate duration from 00:00
            if (now.minute + now.hour *60) % self.notify_interval == 0:
                message = _(u"No activity")


        if message:
            self.notification.update(_("Time Tracker"), message, "hamster-applet")
            self.notification.show()


    def load_day(self):
        """sets up today's tree and fills it with records
           returns information about last activity"""

        facts = runtime.storage.get_todays_facts()

        self.treeview.detach_model()
        self.treeview.clear()

        if facts and facts[-1]["end_time"] == None:
            self.last_activity = facts[-1]
        else:
            self.last_activity = None

        if len(facts) > 15:
            self._gui.get_object("today_box").set_size_request(-1, 360)
            self._gui.get_object("today_box").set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_ALWAYS)
        else:
            self._gui.get_object("today_box").set_size_request(-1, -1)
            self._gui.get_object("today_box").set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_NEVER)

        by_category = {}
        for fact in facts:
            duration = 24 * 60 * fact["delta"].days + fact["delta"].seconds / 60
            by_category[fact['category']] = \
                          by_category.setdefault(fact['category'], 0) + duration
            self.treeview.add_fact(fact)

        self.treeview.attach_model()

        if not facts:
            self._gui.get_object("today_box").hide()
            self._gui.get_object("fact_totals").set_text(_("No records today"))
        else:
            self._gui.get_object("today_box").show()

            total_strings = []
            for category in by_category:
                # listing of today's categories and time spent in them
                duration = "%.1f" % (by_category[category] / 60.0)
                total_strings.append(_("%(category)s: %(duration)s") % \
                        ({'category': category,
                          #duration in main drop-down per category in hours
                          'duration': _("%sh") % duration
                          }))

            total_string = ", ".join(total_strings)
            self._gui.get_object("fact_totals").set_text(total_string)

        self.set_last_activity()

    def set_last_activity(self):
        activity = self.last_activity
        #sets all the labels and everything as necessary
        self.get_widget("stop_tracking").set_sensitive(activity != None)


        if activity:
            self.get_widget("switch_activity").show()
            self.get_widget("start_tracking").hide()

            delta = dt.datetime.now() - activity['start_time']
            duration = delta.seconds /  60

            self.get_widget("last_activity_name").set_text(activity['name'])
            if activity['category'] != _("Unsorted"):
                self.get_widget("last_activity_category") \
                    .set_text(" - %s" % activity['category'])
            self.get_widget("last_activity_category").show()


            self._gui.get_object("more_info_button").hide()

            self.get_widget("last_activity_duration").set_text(stuff.format_duration(duration) or _("Just started"))
            self.get_widget("last_activity_description").set_text(activity['description'] or "")
            self.get_widget("activity_info_box").show()

            self.tag_box.draw(activity["tags"])
        else:
            self.get_widget("switch_activity").hide()
            self.get_widget("start_tracking").show()

            self.get_widget("last_activity_name").set_text(_("No activity"))
            self.get_widget("last_activity_category").hide()

            self.get_widget("activity_info_box").hide()
            self._gui.get_object("more_info_button").show()

            self.tag_box.draw([])


    def delete_selected(self):
        fact = self.treeview.get_selected_fact()
        runtime.storage.remove_fact(fact["id"])

    def __update_fact(self):
        """dbus controller current fact updating"""
        last_activity_id = 0

        if not self.last_activity:
            self.dbusController.TrackingStopped()
        else:
            last_activity_id = self.last_activity['id']

        self.dbusController.FactUpdated(last_activity_id)

    def __show_toggle(self, event, is_active):
        """main window display and positioning"""
        self.button.set_active(is_active)

        if not is_active:
            self.window.hide()
            return True

        self.load_day() # reload day each time before showing to avoid outdated last activity
        self.update_label() #update also label, otherwise we can get 1 minute difference in durations (due to timers refreshed once a minute)

        self.position_popup()


        # doing unstick / stick here, because sometimes while switching
        # between workplaces window still manages to disappear
        self.window.unstick()
        self.window.stick() #show on all desktops

        self.new_name.set_text("");
        self.new_tags.set_text("");
        gobject.idle_add(self._delayed_display)


    def position_popup(self):
        label = self.button.get_allocation()
        window = self.window.get_allocation()

        x, y = self.button.get_parent_window().get_origin()

        self.popup_dir = self.applet.get_orient()

        if self.popup_dir in (gnomeapplet.ORIENT_DOWN, gnomeapplet.ORIENT_UP):
            if self.popup_dir == gnomeapplet.ORIENT_DOWN:
                y = y + label.height
            else:
                y = y - window.height

            screen_w = self.button.get_screen().get_width()
            if x + window.width > screen_w:
                x = screen_w - window.width

        elif self.popup_dir in (gnomeapplet.ORIENT_RIGHT, gnomeapplet.ORIENT_LEFT):
            if self.popup_dir == gnomeapplet.ORIENT_RIGHT:
                x = x + label.width
            else:
                x = x - window.width

            screen_h = self.button.get_screen().get_height()
            if y + window.height > screen_h:
                y = screen_h - window.height

        self.window.move(x, y)

    def _delayed_display(self):
        """show window only when gtk has become idle. otherwise we get
        mixed results. TODO - this looks like a hack though"""
        self.window.show()
        self.window.present()
        self.new_name.grab_focus()


    """events"""
    def on_window_state_changed(self, window, event):
        """untoggle the button when window gets minimized"""
        if (event.changed_mask & gtk.gdk.WINDOW_STATE_ICONIFIED \
            and event.new_window_state & gtk.gdk.WINDOW_STATE_ICONIFIED):
            self.button.set_active(False)


    def on_toggle(self, widget):
        self.__show_toggle(None, self.button.get_active())


    def on_todays_keys(self, tree, event):
        if (event.keyval == gtk.keysyms.Delete):
            self.delete_selected()
            return True

        return False

    def _open_edit_activity(self, row, fact):
        """opens activity editor for selected row"""
        dialogs.edit.show(self.applet, fact_id = fact["id"])

    def on_today_row_activated(self, tree, path, column):
        fact = tree.get_selected_fact()

        if fact:
            runtime.storage.add_fact(fact["name"],
                                     ", ".join(fact["tags"]),
                                     category_name = fact["category"],
                                     description = fact["description"])
            runtime.dispatcher.dispatch('panel_visible', False)


    def on_windows_keys(self, tree, event_key):
        if (event_key.keyval == gtk.keysyms.Escape
          or (event_key.keyval == gtk.keysyms.w
              and event_key.state & gtk.gdk.CONTROL_MASK)):
            if self.new_name.popup.get_property("visible") == False \
               and self.new_tags.popup.get_property("visible") == False:
                runtime.dispatcher.dispatch('panel_visible', False)
                return True
        return False

    """button events"""
    def on_overview(self, menu_item):
        runtime.dispatcher.dispatch('panel_visible', False)
        dialogs.overview.show(self.applet)

    def show_overview(self, menu_item, verb):
        return self.on_overview(menu_item)

    def on_custom_fact(self, menu_item):
        dialogs.edit.show(self.applet)

    def on_about (self, component, verb):
        dialogs.about.show(self.window)

    def show_preferences(self, menu_item, verb):
        runtime.dispatcher.dispatch('panel_visible', False)
        dialogs.prefs.show(self.applet)


    """signals"""
    def after_activity_update(self, widget, renames):
        self.new_name.refresh_activities()
        self.load_day()
        self.update_label()

    def after_fact_update(self, event, date):
        self.load_day()
        self.update_label()
        self.__update_fact()

    def on_idle_changed(self, event, state):
        # state values: 0 = active, 1 = idle

        # refresh when we are out of idle
        # for example, instantly after coming back from suspend
        if state == 0:
            self.refresh_hamster()
        elif self.timeout_enabled and self.last_activity and \
             self.last_activity['end_time'] is None:

            runtime.storage.touch_fact(self.last_activity,
                                       end_time = self.dbusIdleListener.getIdleFrom())

    def on_workspace_changed(self, screen, previous_workspace):
        if not previous_workspace:
            # wnck has a slight hiccup on init and after that calls
            # workspace changed event with blank previous state that should be
            # ignored
            return

        if not self.workspace_tracking:
            return # default to not doing anything

        current_workspace = screen.get_active_workspace()

        # rely on workspace numbers as names change
        prev = previous_workspace.get_number()
        new = current_workspace.get_number()

        # on switch, update our mapping between spaces and activities
        self.workspace_activities[prev] = self.last_activity


        activity = None
        if "name" in self.workspace_tracking:
            # first try to look up activity by desktop name
            mapping = conf.get("workspace_mapping")

            parsed_activity = None
            if new < len(mapping):
                parsed_activity = stuff.parse_activity_input(mapping[new])

            if parsed_activity:
                category_id = None
                if parsed_activity.category_name:
                    category_id = runtime.storage.get_category_by_name(parsed_activity.category_name)

                activity = runtime.storage.get_activity_by_name(parsed_activity.activity_name,
                                                                category_id,
                                                                ressurect = False)
                if activity:
                    # we need dict below
                    activity = dict(name = activity['name'],
                                    category = activity['category'],
                                    description = parsed_activity.description,
                                    tags = parsed_activity.tags)


        if not activity and "memory" in self.workspace_tracking:
            # now see if maybe we have any memory of the new workspace
            # (as in - user was here and tracking Y)
            # if the new workspace is in our dict, switch to the specified activity
            if new in self.workspace_activities and self.workspace_activities[new]:
                activity = self.workspace_activities[new]

        if not activity:
            return

        # check if maybe there is no need to switch, as field match:
        if self.last_activity and \
           self.last_activity['name'].lower() == activity['name'].lower() and \
           (self.last_activity['category'] or "").lower() == (activity['category'] or "").lower() and \
           ", ".join(self.last_activity['tags']).lower() == ", ".join(activity['tags']).lower():
            return

        # ok, switch
        runtime.storage.add_fact(activity['name'],
                                 ", ".join(activity['tags']),
                                 category_name = activity['category'],
                                 description = activity['description'])

        if self.notification:
            self.notification.update(_("Changed activity"),
                                     _("Switched to '%s'") % activity['name'],
                                     "hamster-applet")
            self.notification.show()

    """global shortcuts"""
    def on_keybinding_activated(self):
        self.__show_toggle(None, not self.button.get_active())


    def on_conf_changed(self, event, data):
        key, value = data

        if key == "enable_timeout":
            self.timeout_enabled = value
        elif key == "notify_on_idle":
            self.notify_on_idle = value
        elif key == "notify_interval":
            self.notify_interval = value
        elif key == "day_start_minutes":
            self.load_day()
            self.update_label()
        elif key == "workspace_tracking":
            self.workspace_tracking = value
            if self.workspace_tracking and not self.screen:
                self.init_workspace_tracking()
            elif not self.workspace_tracking:
                if self.screen:
                    self.screen.disconnect(self.screen.workspace_handler)
                    self.screen = None
        elif key == "keybinding":
            try:
                keybinder.unbind(self.hotkey)
            except KeyError:
                pass # don't care

            self.hotkey = value
            self.bind_hotkey()



    def on_activity_text_changed(self, widget):
        self.get_widget("switch_activity").set_sensitive(widget.get_text() != "")

    def on_switch_activity_clicked(self, widget):
        if not self.new_name.get_text():
            return False

        runtime.storage.add_fact(self.new_name.get_text().decode("utf8", "replace"),
                                 self.new_tags.get_text().decode("utf8", "replace"))
        self.new_name.set_text("")
        self.new_tags.set_text("")
        runtime.dispatcher.dispatch('panel_visible', False)

    def on_stop_tracking_clicked(self, widget):
        runtime.storage.touch_fact(self.last_activity)
        self.last_activity = None
        runtime.dispatcher.dispatch('panel_visible', False)

    def on_window_size_request(self, window, event):
        box = self.window.get_allocation()
        if self.prev_size and self.prev_size != (box.width, box.height):
            self.treeview.fix_row_heights()
            self.position_popup()
        self.prev_size = (box.width, box.height)

    def show(self):
        self.window.show_all()

    def get_widget(self, name):
        return self._gui.get_object(name)

    def on_more_info_button_clicked(self, *args):
        gtk.show_uri(gtk.gdk.Screen(), "ghelp:hamster-applet", 0L)
        return False
