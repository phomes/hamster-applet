# - coding: utf-8 -

# Copyright (C) 2007 Patryk Zawadzki <patrys at pld-linux.org>
# Copyright (C) 2007-2009 Toms Baugis <toms.baugis@gmail.com>

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


import datetime as dt
from calendar import timegm
import db
import gobject
from lib import stuff, trophies

def to_dbus_fact(fact):
    """Perform the conversion between fact database query and
    dbus supported data types
    """
    return (fact['id'],
            timegm(fact['start_time'].timetuple()),
            timegm(fact['end_time'].timetuple()) if fact['end_time'] else 0,
            fact['description'] or '',
            fact['name'] or '',
            fact['activity_id'] or 0,
            fact['category'] or '',
            fact['tags'],
            timegm(fact['date'].timetuple()),
            fact['delta'].days * 24 * 60 * 60 + fact['delta'].seconds)

def from_dbus_fact(fact):
    """unpack the struct into a proper dict"""
    return stuff.Fact(fact[4],
                      start_time  = dt.datetime.utcfromtimestamp(fact[1]),
                      end_time = dt.datetime.utcfromtimestamp(fact[2]) if fact[2] else None,
                      description = fact[3],
                      activity_id = fact[5],
                      category = fact[6],
                      tags = fact[7],
                      date = dt.datetime.utcfromtimestamp(fact[8]).date(),
                      delta = dt.timedelta(days = fact[9] // (24 * 60 * 60),
                                           seconds = fact[9] % (24 * 60 * 60)),
            id = fact[0]
            )

class Storage(gobject.GObject):
    """Hamster client class, communicating to hamster storage daemon via d-bus.
       Subscribe to the `tags-changed`, `facts-changed` and `activities-changed`
       signals to be notified when an appropriate factoid of interest has been
       changed.

       In storage a distinguishment is made between the classificator of
       activities and the event in tracking log.
       When talking about the event we use term 'fact'. For the classificator
       we use term 'activity'.
       The relationship is - one activity can be used in several facts.
       The rest is hopefully obvious. But if not, please file bug reports!
    """
    __gsignals__ = {
        "tags-changed": (gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE, ()),
        "facts-changed": (gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE, ()),
        "activities-changed": (gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE, ()),
        "toggle-called": (gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE, ()),
    }

    def __init__(self):
        gobject.GObject.__init__(self)

        self._connection = None # will be initiated on demand

    @staticmethod
    def _to_dict(columns, result_list):
        return [dict(zip(columns, row)) for row in result_list]

    @property
    def conn(self):
        if not self._connection:
            self._connection = db.Storage()
        return self._connection

    def _on_dbus_connection_change(self, name, old, new):
        self._connection = None

    def _on_tags_changed(self):
        self.emit("tags-changed")

    def _on_facts_changed(self):
        self.emit("facts-changed")

    def _on_activities_changed(self):
        self.emit("activities-changed")

    def _on_toggle_called(self):
        self.emit("toggle-called")

    def toggle(self):
        """toggle visibility of the main application window if any"""
        self.conn.Toggle()

    def get_todays_facts(self):
        """returns facts of the current date, respecting hamster midnight
           hamster midnight is stored in gconf, and presented in minutes
        """
        return [from_dbus_fact(fact) for fact in self.GetTodaysFacts()]

    def get_facts(self, date, end_date = None, search_terms = ""):
        """Returns facts for the time span matching the optional filter criteria.
           In search terms comma (",") translates to boolean OR and space (" ")
           to boolean AND.
           Filter is applied to tags, categories, activity names and description
        """
        date = timegm(date.timetuple())
        end_date = end_date or 0
        if end_date:
            end_date = timegm(end_date.timetuple())

        return [from_dbus_fact(fact) for fact in self.GetFacts(date,
                                                                    end_date,
                                                                    search_terms)]

    def get_activities(self, search = ""):
        """returns list of activities name matching search criteria.
           results are sorted by most recent usage.
           search is case insensitive
        """
        return self._to_dict(('name', 'category'), self.GetActivities(search))

    def get_categories(self):
        """returns list of categories"""
        return self._to_dict(('id', 'name'), self.GetCategories())

    def get_tags(self, only_autocomplete = False):
        """returns list of all tags. by default only those that have been set for autocomplete"""
        return self._to_dict(('id', 'name', 'autocomplete'), self.GetTags(only_autocomplete))


    def get_tag_ids(self, tags):
        """find tag IDs by name. tags should be a list of labels
           if a requested tag had been removed from the autocomplete list, it
           will be ressurrected. if tag with such label does not exist, it will
           be created.
           on database changes the `tags-changed` signal is emitted.
        """
        return self._to_dict(('id', 'name', 'autocomplete'), self.GetTagIds(tags))

    def update_autocomplete_tags(self, tags):
        """update list of tags that should autocomplete. this list replaces
           anything that is currently set"""
        self.SetTagsAutocomplete(tags)

    def get_fact(self, id):
        """returns fact by it's ID"""
        return from_dbus_fact(self.GetFact(id))

    def add_fact(self, fact, temporary_activity = False):
        """Add fact. activity name can use the
        `[-]start_time[-end_time] activity@category, description #tag1 #tag2`
        syntax, or params can be stated explicitly.
        Params will take precedence over the derived values.
        start_time defaults to current moment.
        """
        if not fact.activity:
            return None

        serialized = fact.serialized_name()

        start_timestamp = timegm((fact.start_time or dt.datetime.now()).timetuple())

        end_timestamp = fact.end_time or 0
        if end_timestamp:
            end_timestamp = timegm(end_timestamp.timetuple())

        new_id = self.AddFact(serialized,
                                   start_timestamp,
                                   end_timestamp,
                                   temporary_activity)

        # TODO - the parsing should happen just once and preferably here
        # we should feed (serialized_activity, start_time, end_time) into AddFact and others
        if new_id:
            trophies.checker.check_fact_based(fact)
        return new_id

    def stop_tracking(self, end_time = None):
        """Stop tracking current activity. end_time can be passed in if the
        activity should have other end time than the current moment"""
        end_time = timegm((end_time or dt.datetime.now()).timetuple())
        return self.StopTracking(end_time)

    def remove_fact(self, fact_id):
        "delete fact from database"
        self.RemoveFact(fact_id)

    def update_fact(self, fact_id, fact, temporary_activity = False):
        """Update fact values. See add_fact for rules.
        Update is performed via remove/insert, so the
        fact_id after update should not be used anymore. Instead use the ID
        from the fact dict that is returned by this function"""


        start_time = timegm((fact.start_time or dt.datetime.now()).timetuple())

        end_time = fact.end_time or 0
        if end_time:
            end_time = timegm(end_time.timetuple())

        new_id =  self.UpdateFact(fact_id,
                                       fact.serialized_name(),
                                       start_time,
                                       end_time,
                                       temporary_activity)

        trophies.checker.check_update_based(fact_id, new_id, fact)
        return new_id


    def get_category_activities(self, category_id = None):
        """Return activities for category. If category is not specified, will
        return activities that have no category"""
        category_id = category_id or -1
        return self._to_dict(('id', 'name', 'category_id', 'category'), self.GetCategoryActivities(category_id))

    def get_category_id(self, category_name):
        """returns category id by name"""
        return self.GetCategoryId(category_name)

    def get_activity_by_name(self, activity, category_id = None, resurrect = True):
        """returns activity dict by name and optionally filtering by category.
           if activity is found but is marked as deleted, it will be resurrected
           unless told otherise in the resurrect param
        """
        category_id = category_id or 0
        return self.GetActivityByName(activity, category_id, resurrect)

    # category and activity manipulations (normally just via preferences)
    def remove_activity(self, id):
        self.RemoveActivity(id)

    def remove_category(self, id):
        self.RemoveCategory(id)

    def change_category(self, id, category_id):
        return self.ChangeCategory(id, category_id)

    def update_activity(self, id, name, category_id):
        return self.UpdateActivity(id, name, category_id)

    def add_activity(self, name, category_id = -1):
        return self.AddActivity(name, category_id)

    def update_category(self, id, name):
        return self.UpdateCategory(id, name)

    def add_category(self, name):
        return self.AddCategory(name)

    def AddFact(self, fact, start_time, end_time, temporary = False):
        start_time = start_time or None
        if start_time:
            start_time = dt.datetime.utcfromtimestamp(start_time)

        end_time = end_time or None
        if end_time:
            end_time = dt.datetime.utcfromtimestamp(end_time)

#        self.start_transaction()
        result = self.conn.__add_fact(fact, start_time, end_time, temporary)
#        self.end_transaction()

        if result:
            self._on_facts_changed()

        return result or 0

    def GetFact(self, fact_id):
        """Get fact by id. For output format see GetFacts"""
        fact = dict(self.conn.__get_fact(fact_id))
        fact['date'] = fact['start_time'].date()
        fact['delta'] = dt.timedelta()
        return to_dbus_fact(fact)

    def UpdateFact(self, fact_id, fact, start_time, end_time, temporary = False):
        if start_time:
            start_time = dt.datetime.utcfromtimestamp(start_time)
        else:
            start_time = None

        if end_time:
            end_time = dt.datetime.utcfromtimestamp(end_time)
        else:
            end_time = None

#        self.start_transaction()
        self.conn.__remove_fact(fact_id)
        result = self.conn.__add_fact(fact, start_time, end_time, temporary)

#        self.end_transaction()

        if result:
            self._on_facts_changed()
        return result

    def StopTracking(self, end_time):
        """Stops tracking the current activity"""
        end_time = dt.datetime.utcfromtimestamp(end_time)

        facts = self.conn.__get_todays_facts()
        if facts:
            self.conn.__touch_fact(facts[-1], end_time)
            self._on_facts_changed()

    def RemoveFact(self, fact_id):
        """Remove fact from storage by it's ID"""
        fact = self.conn.__get_fact(fact_id)
        if fact:
            self.conn.__remove_fact(fact_id)
            self._on_facts_changed()


    def GetFacts(self, start_date, end_date, search_terms):
        """Gets facts between the day of start_date and the day of end_date.
        Parameters:
        i start_date: Seconds since epoch (timestamp). Use 0 for today
        i end_date: Seconds since epoch (timestamp). Use 0 for today
        s search_terms: Bleh
        Returns Array of fact where fact is struct of:
            i  id
            i  start_time
            i  end_time
            s  description
            s  activity name
            i  activity id
            i  category name
            as List of fact tags
            i  date
            i  delta
        """
        #TODO: Assert start > end ?
        start = dt.date.today()
        if start_date:
            start = dt.datetime.utcfromtimestamp(start_date).date()

        end = None
        if end_date:
            end = dt.datetime.utcfromtimestamp(end_date).date()

        return [to_dbus_fact(fact) for fact in self.conn.__get_facts(start, end, search_terms)]

    def GetTodaysFacts(self):
        """Gets facts of today, respecting hamster midnight. See GetFacts for
        return info"""
        return [to_dbus_fact(fact) for fact in self.conn.__get_todays_facts()]


    # categories

    def AddCategory(self, name):
        res = self.conn.__add_category(name)
        self._on_activities_changed()
        return res


    def GetCategoryId(self, category):
        return self.conn.__get_category_id(category)


    def UpdateCategory(self, id, name):
        self.conn.__update_category(id, name)
        self._on_activities_changed()

    def RemoveCategory(self, id):
        self.conn.__remove_category(id)
        self._on_activities_changed()

    def GetCategories(self):
        return [(category['id'], category['name']) for category in self.conn.__get_categories()]

    # activities

    def AddActivity(self, name, category_id = -1):
        new_id = self.conn.__add_activity(name, category_id)
        self._on_activities_changed()
        return new_id

    def UpdateActivity(self, id, name, category_id):
        self.conn.__update_activity(id, name, category_id)
        self._on_activities_changed()



    def RemoveActivity(self, id):
        result = self.conn.__remove_activity(id)
        self._on_activities_changed()
        return result

    def GetCategoryActivities(self, category_id = -1):

        return [(row['id'],
                 row['name'],
                 row['category_id'],
                 row['name'] or '') for row in
                      self.conn.__get_category_activities(category_id = category_id)]


    def GetActivities(self, search = ""):
        return [(row['name'], row['category'] or '') for row in self.conn.__get_activities(search)]


    def ChangeCategory(self, id, category_id):
        changed = self.conn.__change_category(id, category_id)
        if changed:
            self._on_activities_changed()
        return changed


    def GetActivityByName(self, activity, category_id, resurrect = True):
        category_id = category_id or None

        if activity:
            return dict(self.conn.__get_activity_by_name(activity, category_id, resurrect))
        else:
            return {}

    # tags
    def GetTags(self, only_autocomplete):
        return [(tag['id'], tag['name'], tag['autocomplete']) for tag in self.conn.__get_tags(only_autocomplete)]


    def GetTagIds(self, tags):
        tags, new_added = self.conn.__get_tag_ids(tags)
        if new_added:
            self._on_tags_changed()
        return [(tag['id'], tag['name'], tag['autocomplete']) for tag in tags]


    def SetTagsAutocomplete(self, tags):
        changes = self.conn.__update_autocomplete_tags(tags)
        if changes:
            self._on_tags_changed()

