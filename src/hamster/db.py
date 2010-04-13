# - coding: utf-8 -

# Copyright (C) 2007-2009 Toms Bauģis <toms.baugis at gmail.com>
# Copyright (C) 2007 Patryk Zawadzki <patrys at pld-linux.org>

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


"""separate file for database operations"""
import logging

try:
    import sqlite3 as sqlite
except ImportError:
    try:
        logging.warn("Using sqlite2")
        from pysqlite2 import dbapi2 as sqlite
    except ImportError:
        logging.error("Neither sqlite3 nor pysqlite2 found")
        raise

import os, time
import datetime
import storage
import stuff
from shutil import copy as copyfile
import datetime as dt
import gettext

import itertools

DB_FILE = 'hamster.db'

class Storage(storage.Storage):
    con = None # Connection will be created on demand
    def __setup(self):
        """
        Delayed setup so we don't do everything at the same time
        """
        if self.__setup.im_func.complete:
            return

        self.__con = None
        self.__cur = None

        from configuration import runtime

        db_file = runtime.database_path
        db_path, _ = os.path.split(os.path.realpath(db_file))

        if not os.path.exists(db_path):
            try:
                os.makedirs(db_path, 0744)
            except Exception, msg:
                logging.error("could not create user dir (%s): %s" % (db_path, msg))

        data_dir = runtime.data_dir

        #check if db is here
        if not os.path.exists(db_file):
            logging.info("Database not found in %s - installing default from %s!" % (db_file, data_dir))
            copyfile(os.path.join(data_dir, DB_FILE), db_file)

            #change also permissions - sometimes they are 444
            try:
                os.chmod(db_file, 0664)
            except Exception, msg:
                logging.error("Could not change mode on %s!" % (db_file))
        self.__setup.im_func.complete = True
        self.run_fixtures()



    __setup.complete = False

    #tags, here we come!
    def __get_tags(self, autocomplete = None):
        query = "select * from tags"
        if autocomplete:
            query += " where autocomplete='true'"

        query += " order by name"
        return self.fetchall(query)

    def __get_tag_ids(self, tags):
        """look up tags by their name. create if not found"""

        db_tags = self.fetchall("select * from tags where name in (%s)"
                                            % ",".join(["?"] * len(tags)), tags) # bit of magic here - using sqlites bind variables

        changes = False

        # check if any of tags needs ressurection
        set_complete = [str(tag["id"]) for tag in db_tags if tag["autocomplete"] == "false"]
        if set_complete:
            changes = True
            self.execute("update tags set autocomplete='true' where id in (%s)" % ", ".join(set_complete))


        found_tags = [tag["name"] for tag in db_tags]

        add = set(tags) - set(found_tags)
        if add:
            statement = "insert into tags(name) values(?)"

            self.execute([statement] * len(add), [(tag,) for tag in add])

            return self.__get_tag_ids(tags)[0], True # all done, recurse
        else:
            return db_tags, changes

    def __update_autocomplete_tags(self, tags):
        tags = [tag.strip() for tag in tags.split(",") if tag.strip()]  # split by comma

        #first we will create new ones
        tags, changes = self.__get_tag_ids(tags)
        tags = [tag["id"] for tag in tags]

        #now we will find which ones are gone from the list
        query = """
                    SELECT b.id as id, b.autocomplete, count(a.fact_id) as occurences
                      FROM tags b
                 LEFT JOIN fact_tags a on a.tag_id = b.id
                     WHERE b.id not in (%s)
                  GROUP BY b.id
                """ % ",".join(["?"] * len(tags)) # bit of magic here - using sqlites bind variables

        gone = self.fetchall(query, tags)

        to_delete = [str(tag["id"]) for tag in gone if tag["occurences"] == 0]
        to_uncomplete = [str(tag["id"]) for tag in gone if tag["occurences"] > 0 and tag["autocomplete"] == "true"]

        if to_delete:
            self.execute("delete from tags where id in (%s)" % ", ".join(to_delete))

        if to_uncomplete:
            self.execute("update tags set autocomplete='false' where id in (%s)" % ", ".join(to_uncomplete))

        return changes or len(to_delete + to_uncomplete) > 0

    def __get_category_list(self):
        return self.fetchall("SELECT * FROM categories ORDER BY category_order")

    def __change_category(self, id, category_id):
        # first check if we don't have an activity with same name before us
        activity = self.fetchone("select name from activities where id = ?", (id, ))
        existing_activity = self.__get_activity_by_name(activity['name'], category_id)

        if existing_activity: #ooh, we have something here!
            if id == existing_activity['id']: # we are already there, go home
                return False

            # first move all facts that belong to movable activity to the new one
            update = """
                       UPDATE facts
                          SET activity_id = ?
                        WHERE activity_id = ?
            """

            self.execute(update, (existing_activity['id'], id))

            # and now get rid of our friend
            self.__remove_activity(id)

        else: #just moving
            query = "SELECT max(activity_order) + 1 FROM activities WHERE category_id = ?"
            max_order = self.fetchone(query, (category_id, ))[0] or 1

            statement = """
                       UPDATE activities
                          SET category_id = ?, activity_order = ?
                        WHERE id = ?
            """

            self.execute(statement, (category_id, max_order, id))

        return True

    def __add_category(self, name):
        order = self.fetchone("select max(category_order) + 1  from categories")[0] or 1
        query = """
                   INSERT INTO categories (name, category_order)
                        VALUES (?, ?)
        """
        self.execute(query, (name, order))
        return self.__last_insert_rowid()

    def __update_category(self, id,  name):
        if id > -1: # Update, and ignore unsorted, if that was somehow triggered
            update = """
                       UPDATE categories
                           SET name = ?
                         WHERE id = ?
            """
            self.execute(update, (name, id))

    def __move_activity(self, source_id, target_order, insert_after = True):
        statement = "UPDATE activities SET activity_order = activity_order + 1"

        if insert_after:
            statement += " WHERE activity_order > ?"
        else:
            statement += " WHERE activity_order >= ?"

        self.execute(statement, (target_order, ))

        statement = "UPDATE activities SET activity_order = ? WHERE id = ?"

        if insert_after:
            self.execute(statement, (target_order + 1, source_id))
        else:
            self.execute(statement, (target_order, source_id))



    def __get_activity_by_name(self, name, category_id = None, ressurect = True):
        """get most recent, preferably not deleted activity by it's name"""

        if category_id:
            query = """
                       SELECT a.id, a.name, a.deleted, coalesce(b.name, ?) as category
                         FROM activities a
                    LEFT JOIN categories b ON category_id = b.id
                        WHERE lower(a.name) = lower(?)
                          AND category_id = ?
                     ORDER BY a.deleted, a.id desc
                        LIMIT 1
            """

            res = self.fetchone(query, (_("Unsorted"), name, category_id))
        else:
            query = """
                       SELECT a.id, a.name, a.deleted, coalesce(b.name, ?) as category
                         FROM activities a
                    LEFT JOIN categories b ON category_id = b.id
                        WHERE lower(a.name) = lower(?)
                     ORDER BY a.deleted, a.id desc
                        LIMIT 1
            """
            res = self.fetchone(query, (_("Unsorted"), name, ))

        if res:
            # if the activity was marked as deleted, ressurect on first call
            # and put in the unsorted category
            if res['deleted'] and not ressurect:
                return None
            elif res['deleted']:
                update = """
                            UPDATE activities
                               SET deleted = null, category_id = -1
                             WHERE id = ?
                        """
                self.execute(update, (res['id'], ))

            return res

        return None

    def __get_category_by_name(self, name):
        """returns category by it's name"""

        query = """
                   SELECT id from categories
                    WHERE lower(name) = lower(?)
                 ORDER BY id desc
                    LIMIT 1
        """

        res = self.fetchone(query, (name, ))

        if res:
            return res['id']

        return None

    def __get_fact(self, id):
        query = """
                   SELECT a.id AS id,
                          a.start_time AS start_time,
                          a.end_time AS end_time,
                          a.description as description,
                          b.name AS name, b.id as activity_id,
                          coalesce(c.name, ?) as category, coalesce(c.id, -1) as category_id,
                          e.name as tag
                     FROM facts a
                LEFT JOIN activities b ON a.activity_id = b.id
                LEFT JOIN categories c ON b.category_id = c.id
                LEFT JOIN fact_tags d ON d.fact_id = a.id
                LEFT JOIN tags e ON e.id = d.tag_id
                    WHERE a.id = ?
                 ORDER BY e.name
        """

        return self.__group_tags(self.fetchall(query, (_("Unsorted"), id)))[0]

    def __group_tags(self, facts):
        """put the fact back together and move all the unique tags to an array"""
        if not facts: return facts  #be it None or whatever

        grouped_facts = []
        for fact_id, fact_tags in itertools.groupby(facts, lambda f: f["id"]):
            fact_tags = list(fact_tags)

            # first one is as good as the last one
            grouped_fact = fact_tags[0]

            # we need dict so we can modify it (sqlite.Row is read only)
            # in python 2.5, sqlite does not have keys() yet, so we hardcode them (yay!)
            keys = ["id", "start_time", "end_time", "description", "name",
                    "activity_id", "category", "tag"]
            grouped_fact = dict([(key, grouped_fact[key]) for key in keys])

            grouped_fact["tags"] = [ft["tag"] for ft in fact_tags if ft["tag"]]
            grouped_facts.append(grouped_fact)
        return grouped_facts

    def __get_last_activity(self):
        facts = self.__get_todays_facts()
        last_activity = None
        if facts and facts[-1]["end_time"] == None:
            last_activity = facts[-1]
        return last_activity

    def __touch_fact(self, fact, end_time):
        # tasks under one minute do not count
        if end_time - fact['start_time'] < datetime.timedelta(minutes = 1):
            self.__remove_fact(fact['id'])
        else:
            end_time = end_time.replace(microsecond = 0)
            query = """
                       UPDATE facts
                          SET end_time = ?
                        WHERE id = ?
            """
            self.execute(query, (end_time, fact['id']))

    def __squeeze_in(self, start_time):
        # tries to put task in the given date
        # if there are conflicts, we will only truncate the ongoing task
        # and replace it's end part with our activity

        # we are checking if our start time is in the middle of anything
        # or maybe there is something after us - so we know to adjust end time
        # in the latter case go only few days ahead. everything else is madness, heh
        query = """
                   SELECT a.*, b.name
                     FROM facts a
                LEFT JOIN activities b on b.id = a.activity_id
                    WHERE ((start_time < ? and end_time > ?)
                           OR (start_time > ? and start_time < ?))
                 ORDER BY start_time
                    LIMIT 1
                """
        fact = self.fetchone(query, (start_time,
                                     start_time,
                                     start_time,
                                     start_time + dt.timedelta(seconds = 60 * 60 * 12)))

        end_time = None

        if fact:
            if fact["end_time"] and start_time > fact["start_time"]:
                #we are in middle of a fact - truncate it to our start
                self.execute("UPDATE facts SET end_time=? WHERE id=?",
                             (start_time, fact["id"]))

                # hamster is second-aware, but the edit dialog naturally is not
                # so when an ongoing task is being edited, the seconds get truncated
                # and the start time will be before previous task's end time.
                # so set our end time only if it is not about seconds
                if fact["end_time"].replace(second = 0) > start_time:
                    end_time = fact["end_time"]
            else: #otherwise we have found a task that is after us
                end_time = fact["start_time"]

        return end_time

    def __solve_overlaps(self, start_time, end_time):
        """finds facts that happen in given interval and shifts them to
        make room for new fact"""

        # this function is destructive - can't go with a wildcard
        if not end_time or not start_time:
            return

        # activities that we are overlapping.
        # second OR clause is for elimination - |new fact--|---old-fact--|--new fact|
        query = """
                   SELECT a.*, b.name, c.name as category
                     FROM facts a
                LEFT JOIN activities b on b.id = a.activity_id
                LEFT JOIN categories c on b.category_id = c.id
                    WHERE ((start_time < ? and end_time > ?)
                           OR (start_time < ? and end_time > ?))

                       OR ((start_time < ? and start_time > ?)
                           OR (end_time < ? and end_time > ?))
                 ORDER BY start_time
                """
        conflicts = self.fetchall(query, (start_time, start_time, end_time, end_time,
                                          end_time, start_time, end_time, start_time))

        for fact in conflicts:
            # split - truncate until beginning of new entry and create new activity for end
            if fact["start_time"] < start_time < fact["end_time"] and \
               fact["start_time"] < end_time < fact["end_time"]:

                logging.info("splitting %s" % fact["name"])
                self.execute("""UPDATE facts
                                   SET end_time = ?
                                 WHERE id = ?""", (start_time, fact["id"]))
                fact_name = fact["name"]
                new_fact = self.__add_fact(fact["name"],
                                           "", # will create tags in the next step
                                           end_time,
                                           fact["end_time"],
                                           fact["category"],
                                           fact["description"])
                tag_update = """INSERT INTO fact_tags(fact_id, tag_id)
                                     SELECT ?, tag_id
                                       FROM fact_tags
                                      WHERE fact_id = ?"""
                self.execute(tag_update, (new_fact["id"], fact["id"])) #clone tags

            #eliminate
            elif fact["end_time"] and \
                 start_time < fact["start_time"] < end_time and \
                 start_time < fact["end_time"] < end_time:
                logging.info("eliminating %s" % fact["name"])
                self.__remove_fact(fact["id"])

            # overlap start
            elif start_time < fact["start_time"] < end_time:
                logging.info("Overlapping start of %s" % fact["name"])
                self.execute("UPDATE facts SET start_time=? WHERE id=?",
                             (end_time, fact["id"]))

            # overlap end
            elif start_time < fact["end_time"] < end_time:
                logging.info("Overlapping end of %s" % fact["name"])
                self.execute("UPDATE facts SET end_time=? WHERE id=?",
                             (start_time, fact["id"]))


    def __add_fact(self, activity_name, tags, start_time = None,
                     end_time = None, category_name = None, description = None):

        activity = stuff.parse_activity_input(activity_name)

        # make sure that we do have an activity name after parsing
        if not activity.activity_name:
            return

        # explicitly stated takes precedence
        activity.description = description or activity.description

        tags = [tag.strip() for tag in tags.split(",") if tag.strip()]  # split by comma
        tags = tags or activity.tags # explicitly stated tags take priority

        tags = self.get_tag_ids(tags) #this will create any missing tags too


        if category_name:
            activity.category_name = category_name
        if description:
            activity.description = description #override

        start_time = activity.start_time or start_time or datetime.datetime.now()

        if start_time > datetime.datetime.now():
            return None #no facts in future, please

        start_time = start_time.replace(microsecond = 0)
        end_time = activity.end_time or end_time
        if end_time:
            end_time = end_time.replace(microsecond = 0)

        if not start_time or not activity.activity_name:  # sanity check
            return

        # now check if maybe there is also a category
        category_id = None
        if activity.category_name:
            category_id = self.__get_category_by_name(activity.category_name)
            if not category_id:
                category_id = self.__add_category(activity.category_name)

        # try to find activity
        activity_id = self.__get_activity_by_name(activity.activity_name,
                                                  category_id)
        if not activity_id:
            activity_id = self.__add_activity(activity.activity_name,
                                              category_id)
        else:
            activity_id = activity_id['id']


        # if we are working on +/- current day - check the last_activity
        if (dt.datetime.now() - start_time <= dt.timedelta(days=1)):

            # pull in previous facts
            facts = self.__get_todays_facts()

            previous = None
            if facts and facts[-1]["end_time"] == None:
                previous = facts[-1]

            if previous and previous['start_time'] < start_time:
                # check if maybe that is the same one, in that case no need to restart
                if previous["activity_id"] == activity_id \
                   and previous["tags"] == sorted([tag["name"] for tag in tags]) \
                   and previous["description"] == (description or ""):
                    return previous

                # otherwise, if no description is added
                # see if maybe it is too short to qualify as an activity
                if not previous["description"] \
                    and 60 >= (start_time - previous['start_time']).seconds >= 0:
                    self.__remove_fact(previous['id'])

                    # now that we removed the previous one, see if maybe the one
                    # before that is actually same as the one we want to start
                    # (glueing)
                    if len(facts) > 1 and 60 >= (start_time - facts[-2]['end_time']).seconds >= 0:
                        before = facts[-2]
                        if before["activity_id"] == activity_id \
                           and before["tags"] == sorted([tag["name"] for tag in tags]):
                            # essentially same activity - resume it and return
                            update = """
                                       UPDATE facts
                                          SET end_time = null
                                        WHERE id = ?
                            """
                            self.execute(update, (before["id"],))

                            return before
                else:
                    # otherwise stop
                    update = """
                               UPDATE facts
                                  SET end_time = ?
                                WHERE id = ?
                    """
                    self.execute(update, (start_time, previous["id"]))


        # done with the current activity, now we can solve overlaps
        if not end_time:
            end_time = self.__squeeze_in(start_time)
        else:
            self.__solve_overlaps(start_time, end_time)


        # finally add the new entry
        insert = """
                    INSERT INTO facts (activity_id, start_time, end_time, description)
                               VALUES (?, ?, ?, ?)
        """
        self.execute(insert, (activity_id, start_time, end_time, activity.description))

        fact_id = self.__last_insert_rowid()

        #now link tags
        insert = ["insert into fact_tags(fact_id, tag_id) values(?, ?)"] * len(tags)
        params = [(fact_id, tag["id"]) for tag in tags]
        self.execute(insert, params)

        return fact_id

    def __last_insert_rowid(self):
        return self.fetchone("SELECT last_insert_rowid();")[0]


    def __get_todays_facts(self):
        from configuration import conf
        day_start = conf.get("day_start_minutes")
        day_start = dt.time(day_start / 60, day_start % 60)
        today = (dt.datetime.now() - dt.timedelta(hours = day_start.hour,
                                                  minutes = day_start.minute)).date()
        return self.__get_facts(today)


    def __get_facts(self, date, end_date = None, search_terms = ""):
        query = """
                   SELECT a.id AS id,
                          a.start_time AS start_time,
                          a.end_time AS end_time,
                          a.description as description,
                          b.name AS name, b.id as activity_id,
                          coalesce(c.name, ?) as category,
                          e.name as tag
                     FROM facts a
                LEFT JOIN activities b ON a.activity_id = b.id
                LEFT JOIN categories c ON b.category_id = c.id
                LEFT JOIN fact_tags d ON d.fact_id = a.id
                LEFT JOIN tags e ON e.id = d.tag_id
                    WHERE (a.end_time >= ? OR a.end_time IS NULL) AND a.start_time <= ?
        """

        # let's see what we can do with search terms
        # we will be looking in activity names, descriptions, categories and tags
        # comma will be treated as OR
        # space will be treated as AND or possible join


        # split by comma and then by space and remove all extra spaces
        or_bits = [[term.strip().lower().replace("'", "''") #striping removing case sensitivity and escaping quotes in term
                          for term in terms.strip().split(" ") if term.strip()]
                          for terms in search_terms.split(",") if terms.strip()]

        def all_fields(term):
            return """(lower(a.description) like '%%%(term)s%%'
                       or lower(b.name) = '%(term)s'
                       or lower(c.name) = '%(term)s'
                       or lower(e.name) = '%(term)s' )""" % dict(term = term)

        if or_bits:
            search_query = "1<>1 " # will be building OR chain, so start with a false

            for and_bits in or_bits:
                if len(and_bits) == 1:
                    and_query = all_fields(and_bits[0])
                else:
                    and_query = "1=1 "  # will be building AND chain, so start with a true
                    # if we have more than one word, go for "(a and b) or ab"
                    # to match two word tags
                    for bit1, bit2 in zip(and_bits, and_bits[1:]):
                        and_query += "and (%s and %s) or %s" % (all_fields(bit1),
                                                                all_fields(bit2),
                                                                all_fields("%s %s" % (bit1, bit2)))

                search_query = "%s or (%s) " % (search_query, and_query)

            query = "%s and (%s)" % (query, search_query)



        query += " ORDER BY a.start_time, e.name"
        end_date = end_date or date

        from configuration import conf
        day_start = conf.get("day_start_minutes")
        day_start = dt.time(day_start / 60, day_start % 60)

        split_time = day_start
        datetime_from = dt.datetime.combine(date, split_time)
        datetime_to = dt.datetime.combine(end_date, split_time) + dt.timedelta(days = 1)

        facts = self.fetchall(query, (_("Unsorted"),
                                      datetime_from,
                                      datetime_to))

        #first let's put all tags in an array
        facts = self.__group_tags(facts)

        res = []
        for fact in facts:
            # heuristics to assign tasks to proper days

            # if fact has no end time, set the last minute of the day,
            # or current time if fact has happened in last 24 hours
            if fact["end_time"]:
                fact_end_time = fact["end_time"]
            elif (dt.date.today() - fact["start_time"].date()) <= dt.timedelta(days=1):
                fact_end_time = dt.datetime.now().replace(microsecond = 0)
            else:
                fact_end_time = fact["start_time"]

            fact_start_date = fact["start_time"].date() \
                - dt.timedelta(1 if fact["start_time"].time() < split_time else 0)
            fact_end_date = fact_end_time.date() \
                - dt.timedelta(1 if fact_end_time.time() < split_time else 0)
            fact_date_span = fact_end_date - fact_start_date

            # check if the task spans across two dates
            if fact_date_span.days == 1:
                datetime_split = dt.datetime.combine(fact_end_date, split_time)
                start_date_duration = datetime_split - fact["start_time"]
                end_date_duration = fact_end_time - datetime_split
                if start_date_duration > end_date_duration:
                    # most of the task was done during the previous day
                    fact_date = fact_start_date
                else:
                    fact_date = fact_end_date
            else:
                # either doesn't span or more than 24 hrs tracked
                # (in which case we give up)
                fact_date = fact_start_date

            if fact_date < date or fact_date > end_date:
                # due to spanning we've jumped outside of given period
                continue

            fact["date"] = fact_date
            fact["delta"] = fact_end_time - fact["start_time"]
            res.append(fact)

        return res

    def __get_popular_categories(self):
        """returns categories used in the specified interval"""
        query = """
                   SELECT coalesce(c.name, ?) as category, count(a.id) as popularity
                     FROM facts a
                LEFT JOIN activities b on a.activity_id = b.id
                LEFT JOIN categories c on c.id = b.category_id
                 GROUP BY b.category_id
                 ORDER BY popularity desc
        """
        return self.fetchall(query, (_("Unsorted"), ))

    def __remove_fact(self, fact_id):
        statements = ["DELETE FROM fact_tags where fact_id = ?",
                      "DELETE FROM facts where id = ?"]
        self.execute(statements, [(fact_id,)] * 2)

    def __get_activities(self, category_id = None):
        """returns list of activities, if category is specified, order by name
           otherwise - by activity_order"""
        if category_id:
            query = """
                       SELECT a.*, b.name as category
                         FROM activities a
                    LEFT JOIN categories b on coalesce(b.id, -1) = a.category_id
                        WHERE category_id = ?
                          AND deleted is null
            """

            # unsorted entries we sort by name - others by ID
            if category_id == -1:
                query += "ORDER BY lower(a.name)"
            else:
                query += "ORDER BY a.activity_order"

            activities = self.fetchall(query, (category_id, ))

        else:
            query = """
                       SELECT a.*, b.name as category
                         FROM activities a
                    LEFT JOIN categories b on coalesce(b.id, -1) = a.category_id
                        WHERE deleted IS NULL
                     ORDER BY lower(a.name)
            """
            activities = self.fetchall(query)

        return activities

    def __get_autocomplete_activities(self, search):
        """returns list of activities for autocomplete,
           activity names converted to lowercase"""

        query = """
                   SELECT lower(a.name) AS name, b.name AS category
                     FROM activities a
                LEFT JOIN categories b ON coalesce(b.id, -1) = a.category_id
                LEFT JOIN facts f ON a.id = f.activity_id
                    WHERE deleted IS NULL
                      AND a.name LIKE ? ESCAPE '\\'
                 GROUP BY a.id
                 ORDER BY max(f.start_time) DESC, lower(a.name)
                    LIMIT 50
        """
        search = search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        activities = self.fetchall(query, (u'%s%%' % search, ))

        return activities

    def __remove_activity(self, id):
        """ check if we have any facts with this activity and behave accordingly
            if there are facts - sets activity to deleted = True
            else, just remove it"""

        query = "select count(*) as count from facts where activity_id = ?"
        bound_facts = self.fetchone(query, (id,))['count']

        if bound_facts > 0:
            self.execute("UPDATE activities SET deleted = 1 WHERE id = ?", (id,))
        else:
            self.execute("delete from activities where id = ?", (id,))

    def __remove_category(self, id):
        """move all activities to unsorted and remove category"""

        update = "update activities set category_id = -1 where category_id = ?"
        self.execute(update, (id, ))

        self.execute("delete from categories where id = ?", (id, ))


    def __swap_activities(self, id1, priority1, id2, priority2):
        """ swaps nearby activities """
        # TODO - 2 selects and 2 updates is wrong we could live without selects
        self.execute(["update activities set activity_order = ? where id = ?",
                      "update activities set activity_order = ? where id = ?"],
                      [(priority1, id2), (priority2, id1)])

    def __add_activity(self, name, category_id = None):
        # first check that we don't have anything like that yet
        activity = self.__get_activity_by_name(name, category_id)
        if activity:
            return activity['id']

        #now do the create bit
        category_id = category_id or -1
        new_order = self.fetchone("select max(activity_order) + 1  from activities")[0] or 1

        query = """
                   INSERT INTO activities (name, category_id, activity_order)
                        VALUES (?, ?, ?)
        """
        self.execute(query, (name, category_id, new_order))
        return self.__last_insert_rowid()

    def __update_activity(self, id, name, category_id):
        query = """
                   UPDATE activities
                       SET name = ?,
                           category_id = ?
                     WHERE id = ?
        """
        self.execute(query, (name, category_id, id))

    """ Here be dragons (lame connection/cursor wrappers) """
    def get_connection(self):
        from configuration import runtime
        if self.con is None:
            db_file = runtime.database_path
            self.con = sqlite.connect(db_file, detect_types=sqlite.PARSE_DECLTYPES|sqlite.PARSE_COLNAMES)
            self.con.row_factory = sqlite.Row

        return self.con

    connection = property(get_connection, None)

    def fetchall(self, query, params = None):
        from configuration import runtime
        self.__setup()

        con = self.connection
        cur = con.cursor()

        logging.debug("%s %s" % (query, params))

        if params:
            cur.execute(query, params)
        else:
            cur.execute(query)

        res = cur.fetchall()
        cur.close()

        return res

    def fetchone(self, query, params = None):
        res = self.fetchall(query, params)
        if res:
            return res[0]
        else:
            return None

    def execute(self, statement, params = ()):
        """
        execute sql statement. optionally you can give multiple statements
        to save on cursor creation and closure
        """
        from configuration import runtime
        self.__setup()

        con = self.__con or self.connection
        cur = self.__cur or con.cursor()

        if isinstance(statement, list) == False: #we kind of think that we will get list of instructions
            statement = [statement]
            params = [params]

        if isinstance(statement, list):
            for i in range(len(statement)):
                logging.debug("%s %s" % (statement[i], params[i]))

                res = cur.execute(statement[i], params[i])

        if not self.__con:
            con.commit()
            cur.close()
            runtime.register_modification()


    def start_transaction(self):
        # will give some hints to execute not to close or commit anything
        self.__con = self.connection
        self.__cur = self.__con.cursor()

    def end_transaction(self):
        self.__con.commit()
        self.__cur.close()
        self.__con = None
        from configuration import runtime
        runtime.register_modification()

    def run_fixtures(self):
        self.start_transaction()

        # defaults
        work_category = {"name": _("Work"),
                         "entries": [_("Reading news"),
                                     _("Checking stocks"),
                                     _("Super secret project X"),
                                     _("World domination")]}

        nonwork_category = {"name": _("Day-to-day"),
                            "entries": [_("Lunch"),
                                        _("Watering flowers"),
                                        _("Doing handstands")]}

        """upgrade DB to hamster version"""
        version = self.fetchone("SELECT version FROM version")["version"]
        current_version = 7

        if version < 2:
            """moving from fact_date, fact_time to start_time, end_time"""

            self.execute("""
                               CREATE TABLE facts_new
                                            (id integer primary key,
                                             activity_id integer,
                                             start_time varchar2(12),
                                             end_time varchar2(12))
            """)

            self.execute("""
                               INSERT INTO facts_new
                                           (id, activity_id, start_time)
                                    SELECT id, activity_id, fact_date || fact_time
                                      FROM facts
            """)

            self.execute("DROP TABLE facts")
            self.execute("ALTER TABLE facts_new RENAME TO facts")

            # run through all facts and set the end time
            # if previous fact is not on the same date, then it means that it was the
            # last one in previous, so remove it
            # this logic saves our last entry from being deleted, which is good
            facts = self.fetchall("""
                                        SELECT id, activity_id, start_time,
                                               substr(start_time,1, 8) start_date
                                          FROM facts
                                      ORDER BY start_time
            """)
            prev_fact = None

            for fact in facts:
                if prev_fact:
                    if prev_fact['start_date'] == fact['start_date']:
                        self.execute("UPDATE facts SET end_time = ? where id = ?",
                                   (fact['start_time'], prev_fact['id']))
                    else:
                        #otherwise that's the last entry of the day - remove it
                        self.execute("DELETE FROM facts WHERE id = ?", (prev_fact["id"],))

                prev_fact = fact

        #it was kind of silly not to have datetimes in first place
        if version < 3:
            self.execute("""
                               CREATE TABLE facts_new
                                            (id integer primary key,
                                             activity_id integer,
                                             start_time timestamp,
                                             end_time timestamp)
            """)

            self.execute("""
                               INSERT INTO facts_new
                                           (id, activity_id, start_time, end_time)
                                    SELECT id, activity_id,
                                           substr(start_time,1,4) || "-"
                                           || substr(start_time, 5, 2) || "-"
                                           || substr(start_time, 7, 2) || " "
                                           || substr(start_time, 9, 2) || ":"
                                           || substr(start_time, 11, 2) || ":00",
                                           substr(end_time,1,4) || "-"
                                           || substr(end_time, 5, 2) || "-"
                                           || substr(end_time, 7, 2) || " "
                                           || substr(end_time, 9, 2) || ":"
                                           || substr(end_time, 11, 2) || ":00"
                                      FROM facts;
               """)

            self.execute("DROP TABLE facts")
            self.execute("ALTER TABLE facts_new RENAME TO facts")


        #adding categories table to categorize activities
        if version < 4:
            #adding the categories table
            self.execute("""
                               CREATE TABLE categories
                                            (id integer primary key,
                                             name varchar2(500),
                                             color_code varchar2(50),
                                             category_order integer)
            """)

            # adding default categories, and make sure that uncategorized stays on bottom for starters
            # set order to 2 in case, if we get work in next lines
            self.execute("""
                               INSERT INTO categories
                                           (id, name, category_order)
                                    VALUES (1, ?, 2);
               """, (nonwork_category["name"],))

            #check if we have to create work category - consider work everything that has been determined so, and is not deleted
            work_activities = self.fetchone("""
                                    SELECT count(*) as work_activities
                                      FROM activities
                                     WHERE deleted is null and work=1;
               """)['work_activities']

            if work_activities > 0:
                self.execute("""
                               INSERT INTO categories
                                           (id, name, category_order)
                                    VALUES (2, ?, 1);
                  """, (work_category["name"],))

            # now add category field to activities, before starting the move
            self.execute("""   ALTER TABLE activities
                                ADD COLUMN category_id integer;
               """)


            # starting the move

            # first remove all deleted activities with no instances in facts
            self.execute("""
                               DELETE FROM activities
                                     WHERE deleted = 1
                                       AND id not in(select activity_id from facts);
             """)


            # moving work / non-work to appropriate categories
            # exploit false/true = 0/1 thing
            self.execute("""       UPDATE activities
                                      SET category_id = work + 1
                                    WHERE deleted is null
               """)

            #finally, set category to -1 where there is none
            self.execute("""       UPDATE activities
                                      SET category_id = -1
                                    WHERE category_id is null
               """)

            # drop work column and forget value of deleted
            # previously deleted records are now unsorted ones
            # user will be able to mark them as deleted again, in which case
            # they won't appear in autocomplete, or in categories
            # ressurection happens, when user enters the exact same name
            self.execute("""
                               CREATE TABLE activities_new (id integer primary key,
                                                            name varchar2(500),
                                                            activity_order integer,
                                                            deleted integer,
                                                            category_id integer);
            """)

            self.execute("""
                               INSERT INTO activities_new
                                           (id, name, activity_order, category_id)
                                    SELECT id, name, activity_order, category_id
                                      FROM activities;
               """)

            self.execute("DROP TABLE activities")
            self.execute("ALTER TABLE activities_new RENAME TO activities")

        if version < 5:
            self.execute("ALTER TABLE facts add column description varchar2")

        if version < 6:
            # facts table could use an index
            self.execute("CREATE INDEX idx_facts_start_end ON facts(start_time, end_time)")
            self.execute("CREATE INDEX idx_facts_start_end_activity ON facts(start_time, end_time, activity_id)")

            # adding tags
            self.execute("""CREATE TABLE tags (id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                                               name TEXT NOT NULL,
                                               autocomplete BOOL DEFAULT true)""")
            self.execute("CREATE INDEX idx_tags_name ON tags(name)")

            self.execute("CREATE TABLE fact_tags(fact_id integer, tag_id integer)")
            self.execute("CREATE INDEX idx_fact_tags_fact ON fact_tags(fact_id)")
            self.execute("CREATE INDEX idx_fact_tags_tag ON fact_tags(tag_id)")


        if version < 7:
            self.execute("""CREATE TABLE increment_facts (id integer primary key autoincrement,
                                                          activity_id integer,
                                                          start_time timestamp,
                                                          end_time timestamp,
                                                          description varchar2)""")
            self.execute("""INSERT INTO increment_facts(id, activity_id, start_time, end_time, description)
                                 SELECT id, activity_id, start_time, end_time, description from facts""")
            self.execute("DROP table facts")
            self.execute("ALTER TABLE increment_facts RENAME TO facts")


        # at the happy end, update version number
        if version < current_version:
            #lock down current version
            self.execute("UPDATE version SET version = %d" % current_version)

        """we start with an empty database and then populate with default
           values. This way defaults can be localized!"""

        category_count = self.fetchone("select count(*) from categories")[0]

        if category_count == 0:
            work_cat_id = self.__add_category(work_category["name"])
            for entry in work_category["entries"]:
                self.__add_activity(entry, work_cat_id)

            nonwork_cat_id = self.__add_category(nonwork_category["name"])
            for entry in nonwork_category["entries"]:
                self.__add_activity(entry, nonwork_cat_id)


        self.end_transaction()
