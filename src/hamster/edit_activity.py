# -*- coding: utf-8 -*-

# Copyright (C) 2007-2009 Toms Bauģis <toms.baugis at gmail.com>

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

import gtk
import time
import datetime as dt

""" TODO:
     * hook into notifications and refresh our days if some evil neighbour edit
       fact window has dared to edit facts
"""
from configuration import runtime
import stuff, widgets

class CustomFactController:
    def __init__(self,  parent = None, fact_date = None, fact_id = None):

        self._gui = stuff.load_ui_file("edit_activity.ui")
        self.window = self.get_widget('custom_fact_window')

        self.parent, self.fact_id = parent, fact_id
        start_date, end_date = None, None

        #TODO - should somehow hint that time is not welcome here
        self.new_name = widgets.ActivityEntry()
        self.get_widget("activity_box").add(self.new_name)

        self.new_tags = widgets.TagsEntry()
        self.get_widget("tags_box").add(self.new_tags)

        if fact_id:
            fact = runtime.storage.get_fact(fact_id)

            label = fact['name']
            if fact['category'] != _("Unsorted"):
                label += "@%s" %  fact['category']

            self.new_name.set_text(label)

            self.new_tags.set_text(", ".join(fact['tags']))


            start_date = fact["start_time"]
            end_date = fact["end_time"]

            buf = gtk.TextBuffer()
            buf.set_text(fact["description"] or "")
            self.get_widget('description').set_buffer(buf)

            self.get_widget("save_button").set_label("gtk-save")
            self.window.set_title(_("Update activity"))

        else:
            # if there is previous activity with end time - attach to it
            # otherwise let's start at 8am (unless it is today - in that case
            # we will assume that the user wants to start from this moment)
            fact_date = fact_date or dt.date.today()

            last_activity = runtime.storage.get_facts(fact_date)
            if last_activity and last_activity[-1]["end_time"]:
                start_date = last_activity[-1]["end_time"]

                if fact_date != dt.date.today():
                    end_date = start_date + dt.timedelta(minutes=30)
            else:
                if fact_date == dt.date.today():
                    start_date = dt.datetime.now()
                else:
                    start_date = dt.datetime(fact_date.year, fact_date.month,
                                             fact_date.day, 8)


        if not end_date:
            self.get_widget("in_progress").set_active(True)
            if (dt.datetime.now() - start_date).days == 0:
                end_date = dt.datetime.now()


        start_date = start_date or dt.datetime.now()
        end_date = end_date or start_date + dt.timedelta(minutes = 30)


        self.start_date = widgets.DateInput(start_date)
        self.get_widget("start_date_placeholder").add(self.start_date)

        self.start_time = widgets.TimeInput(start_date)
        self.get_widget("start_time_placeholder").add(self.start_time)

        self.end_time = widgets.TimeInput(end_date, start_date)
        self.get_widget("end_time_placeholder").add(self.end_time)
        self.set_end_date_label(end_date)


        self.dayline = widgets.DayLine()
        self.dayline.on_time_changed = self.update_time
        self.dayline.on_more_data = runtime.storage.get_facts
        self._gui.get_object("day_preview").add(self.dayline)

        self.on_in_progress_toggled(self.get_widget("in_progress"))

        self.start_date.connect("date-entered", self.on_start_date_entered)
        self.start_time.connect("time-entered", self.on_start_time_entered)
        self.new_name.connect("changed", self.on_new_name_changed)
        self.end_time.connect("time-entered", self.on_end_time_entered)
        self._gui.connect_signals(self)

        self.window.show_all()

    def update_time(self, start_time, end_time):
        self.start_time.set_time(start_time)
        self.start_date.set_date(start_time)
        self.end_time.set_time(end_time)
        self.set_end_date_label(end_time)


    def draw_preview(self, date, highlight = None):
        day_facts = runtime.storage.get_facts(date)
        self.dayline.draw(day_facts, highlight)

    def get_widget(self, name):
        """ skip one variable (huh) """
        return self._gui.get_object(name)

    def show(self):
        self.window.show()


    def figure_description(self):
        activity = self.new_name.get_text().decode("utf-8")

        # juggle with description - break into parts and then put together
        buf = self.get_widget('description').get_buffer()
        description = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), 0)\
                         .decode("utf-8")
        return description.strip()


    def _get_datetime(self, prefix):
        start_time = self.start_time.get_time()
        start_date = self.start_date.get_date()

        if prefix == "end":
            end_time = self.end_time.get_time()
            end_date = start_date
            if end_time < start_time:
                end_date = start_date + dt.timedelta(days=1)

            if end_date:
                self.set_end_date_label(end_date)
            time, date = end_time, end_date
        else:
            time, date = start_time, start_date

        if time is not None and date:
            return dt.datetime.combine(date, time)
        else:
            return None

    def on_save_button_clicked(self, button):
        activity = self.new_name.get_text().decode("utf-8")

        if not activity:
            return False


        # user might also type description in the activity name - strip it here
        # and remember value
        inline_description = None
        if activity.find(",") != -1:
            activity, inline_description  = activity.split(",", 1)
            inline_description = inline_description.strip()

        # explicit takes precedence
        description = self.figure_description() or inline_description

        tags = self.new_tags.get_text().decode('utf8', 'replace')

        start_time = self._get_datetime("start")

        if self.get_widget("in_progress").get_active():
            end_time = None
        else:
            end_time = self._get_datetime("end")

        if self.fact_id:
            runtime.storage.update_fact(self.fact_id, activity, tags, start_time, end_time, description)
        else:
            runtime.storage.add_fact(activity, tags, start_time, end_time, description = description)


        # hide panel only on add - on update user will want to see changes
        if not self.fact_id:
            runtime.dispatcher.dispatch('panel_visible', False)

        self.close_window()

    def on_activity_list_key_pressed(self, entry, event):
        #treating tab as keydown to be able to cycle through available values
        if event.keyval == gtk.keysyms.Tab:
            event.keyval = gtk.keysyms.Down
        return False

    def on_in_progress_toggled(self, check):
        sensitive = not check.get_active()
        self.end_time.set_sensitive(sensitive)
        self.get_widget("end_label").set_sensitive(sensitive)
        self.get_widget("end_date_label").set_sensitive(sensitive)
        self.validate_fields()
        self.dayline.set_in_progress(not sensitive)

    def on_cancel_clicked(self, button):
        self.close_window()

    def on_new_name_changed(self, combo):
        self.validate_fields()

    def on_start_date_entered(self, widget):
        self.validate_fields()
        self.start_time.grab_focus()

    def on_start_time_entered(self, widget):
        start_time = self.start_time.get_time()
        if not start_time:
            return

        self.end_time.set_start_time(start_time)
        self.validate_fields()
        self.end_time.grab_focus()

    def on_end_time_entered(self, widget):
        self.validate_fields()

    def set_end_date_label(self, some_date):
        self.get_widget("end_date_label").set_text(some_date.strftime("%x"))

    def validate_fields(self, widget = None):
        activity_text = self.new_name.get_text().decode('utf8', 'replace')
        start_time = self._get_datetime("start")

        end_time = self._get_datetime("end")

        if start_time and end_time:
            # make sure we are within 24 hours of start time
            end_time -= dt.timedelta(days=(end_time - start_time).days)


            self.draw_preview(start_time.date(), [start_time, end_time])
        else:
            self.draw_preview(dt.datetime.today().date(), [dt.datetime.now(),
                                                           dt.datetime.now()])

        looks_good = False
        if activity_text != "" and start_time and end_time and \
           (end_time - start_time).days == 0:
            looks_good = True

        self.get_widget("save_button").set_sensitive(looks_good)
        return looks_good

    def on_window_key_pressed(self, tree, event_key):
        if (event_key.keyval == gtk.keysyms.Escape
          or (event_key.keyval == gtk.keysyms.w
              and event_key.state & gtk.gdk.CONTROL_MASK)):

            if self.start_date.popup.get_property("visible") or \
               self.start_time.popup.get_property("visible") or \
               self.end_time.popup.get_property("visible") or \
               self.new_name.popup.get_property("visible") or \
               self.new_tags.popup.get_property("visible"):
                return False

            self.close_window()

    def on_close(self, widget, event):
        self.close_window()

    def close_window(self):
        self.window.destroy()
        return False
