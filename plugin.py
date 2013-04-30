###
# Copyright (c) 2006, Ilya Kuznetsov
# Copyright (c) 2008,2012 Kevin Funk
# Copyright (c) 2013 Andrew Northall <andrew@northall.me.uk>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#   * Redistributions of source code must retain the above copyright notice,
#     this list of conditions, and the following disclaimer.
#   * Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions, and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#   * Neither the name of the author of this software nor the name of
#     contributors to this software may be used to endorse or promote products
#     derived from this software without specific prior written consent.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

###

import supybot.utils as utils
from supybot.commands import *
import supybot.ircmsgs as ircmsgs
import supybot.conf as conf
import supybot.plugins as plugins
import supybot.ircutils as ircutils
import supybot.callbacks as callbacks
import supybot.world as world
import supybot.log as log

import urllib2
import urllib
from xml.dom import minidom
from time import time

from LastFMDB import *

class LastFMParser:

    def parseRecentTracks(self, stream):
        """
        @return Tuple with track information of last track
        """

        xml = minidom.parse(stream).getElementsByTagName("recenttracks")[0]
        user = xml.getAttribute("user")

        t = xml.getElementsByTagName("track")[0] # most recent track
        isNowPlaying = (t.getAttribute("nowplaying") == "true")
        if not isNowPlaying:
            time = int(t.getElementsByTagName("date")[0].getAttribute("uts"))
        else:
            time = None

        artist = t.getElementsByTagName("artist")[0].firstChild.data
        track = t.getElementsByTagName("name")[0].firstChild.data
        try:
            albumNode = t.getElementsByTagName("album")[0].firstChild
            album = albumNode.data
        except (IndexError, AttributeError):
            album = None
        return (user, isNowPlaying, artist, track, album, time)

    def parseTrackInformation(self, stream):
	"""
	@return Tuple with information on a track in relation to a user
	"""

	xml = minidom.parse(stream).getElementsByTagName("track")[0]
	listeners = xml.getElementsByTagName("listeners")[0].firstChild.data
	playcount = xml.getElementsByTagName("playcount")[0].firstChild.data
	userloved = xml.getElementsByTagName("userloved")[0].firstChild.data
	try:
	    usercountNode = xml.getElementsByTagName("userplaycount")[0].firstChild
	    usercount = usercountNode.data
	except (IndexError, AttributeError):
	    usercount = 0
	
	tags = list()
	try:
	    toptags = xml.getElementsByTagName("toptags")[0]
    	    tagslist = toptags.getElementsByTagName("tag")
	    for tag in tagslist:
	        tags.append(tag.getElementsByTagName("name")[0].firstChild.data)

	except:
	    pass
	
	return (int(listeners), int(playcount), int(usercount), int(userloved), tags)

class LastFM(callbacks.Plugin):
    # 1.0 API (deprecated)
    APIURL_1_0 = "http://ws.audioscrobbler.com/1.0/user"

    # 2.0 API (see http://www.lastfm.de/api/intro)
    APIKEY = "b7638a70725eea60737f9ad9f56f3099"
    APIURL_2_0 = "http://ws.audioscrobbler.com/2.0/?api_key=%s&" % APIKEY

    def __init__(self, irc):
        self.__parent = super(LastFM, self)
        self.__parent.__init__(irc)
        self.db = LastFMDB(dbfilename)
        world.flushers.append(self.db.flush)

    def die(self):
        if self.db.flush in world.flushers:
            world.flushers.remove(self.db.flush)
        self.db.close()
        self.__parent.die()

    def lastfm(self, irc, msg, args, method, optionalId):
        """<method> [<id>]

        Lists LastFM info where <method> is in
        [friends, neighbours, profile, recenttracks, tags, topalbums,
        topartists, toptracks].
        Set your LastFM ID with the set method (default is your current nick)
        or specify <id> to switch for one call.
        """

        id = (optionalId or self.db.getId(msg.nick) or msg.nick)
        channel = msg.args[0]
        maxResults = self.registryValue("maxResults", channel)
        method = method.lower()

        url = "%s/%s/%s.txt" % (self.APIURL_1_0, id, method)
        try:
            f = urllib2.urlopen(url)
        except urllib2.HTTPError:
            irc.error("Unknown ID (%s) or unknown method (%s)"
                    % (msg.nick, method))
            return


        lines = f.read().split("\n")
        content = map(lambda s: s.split(",")[-1], lines)

        irc.reply("%s's %s: %s (with a total number of %i entries)"
                % (id, method, ", ".join(content[0:maxResults]),
                    len(content)))

    lastfm = wrap(lastfm, ["something", optional("something")])

    def nowPlaying(self, irc, msg, args, optionalId):
        """[<id>]

        Announces the now playing track of the specified LastFM ID.
        Set your LastFM ID with the set method (default is your current nick)
        or specify <id> to switch for one call.
        """
        nick = msg.nick
        id = (self.db.getId(nick) or nick)
        if optionalId:
            id = (self.db.getId(optionalId) or optionalId)
            
        # see http://www.lastfm.de/api/show/user.getrecenttracks
        url = "%s&method=user.getrecenttracks&user=%s" % (self.APIURL_2_0, id)
        try:
            f = urllib2.urlopen(url)
        except urllib2.HTTPError:
            irc.error("Unknown ID (%s)" % id)
            return
            
        parser = LastFMParser()
        (user, isNowPlaying, artist, track, album, time) = parser.parseRecentTracks(f)
        
        # extra API call to get: listeners, playcount, user playcount, user loved (0/1 toggle), track tags
        # doc: http://www.last.fm/api/show/track.getInfo
        try:
    	    params = urllib.urlencode({'username': id, 'track': track, 'artist': artist})
            urlTwo = "%smethod=track.getInfo&%s" % (self.APIURL_2_0, params)
            fTwo = urllib2.urlopen(urlTwo)
        except urllib2.HTTPError:
            irc.error("Error getting now playing track infomation for %s" % id)

        (listeners, playcount, usercount, userloved, tags) = parser.parseTrackInformation(fTwo)
        
        if self.db.getId(nick) == id:
	        user = nick
        elif self.db.getId(optionalId) == id:
            user = optionalId

        albumStr = ", from the album " + album if album else ""
        
        # display 10 tags
        tagStr = "Tags: " + ", ".join(tags[0:10]) + "."
        
        # if no tags, replace with 'none'.
        if len(tags) == 0:
            tagStr = "This track has no tags."
        
        usercountStr = " for the " + self._formatPlaycount(usercount + 1) + " time" if usercount > 0 else " for the 1st time"
        average = str(int(round(float(playcount) / float(listeners)))) 
        averageStr = "- an average of " + average + " listens per user." if listeners > 100 else "."
        lovedStr = " a loved track," if userloved == 1 else ""
        
        if isNowPlaying:
       	    irc.reply(('%s (%s) is now playing%s "%s" by %s%s%s. This track has been played %s times by %s listeners %s' \
                % (user, id, lovedStr, track, artist, albumStr, usercountStr, playcount, listeners, averageStr)).encode("utf8"))
            irc.reply(tagStr.encode("utf8"))

        else:
       	    irc.reply(('%s (%s) last played%s "%s" by %s%s%s. This track has been played %s times by %s listeners %s' \
                % (user, id, lovedStr, track, artist, albumStr, usercountStr, playcount, listeners, averageStr)).encode("utf8"))
            irc.reply(tagStr.encode("utf8"))

    np = wrap(nowPlaying, [optional("something")])

    def setUserId(self, irc, msg, args, newId):
        """<id>

        Sets the LastFM ID for the caller and saves it in a database.
        """

        self.db.set(msg.nick, newId)

        irc.reply("LastFM ID changed.")
        self.profile(irc, msg, args)

    set = wrap(setUserId, ["something"])

    def profile(self, irc, msg, args, optionalId):
        """[<id>]

        Prints the profile info for the specified LastFM ID.
        Set your LastFM ID with the set method (default is your current nick)
        or specify <id> to switch for one call.
        """

        id = (optionalId or self.db.getId(msg.nick) or msg.nick)

        url = "%s/%s/profile.xml" % (self.APIURL_1_0, id)
        try:
            f = urllib2.urlopen(url)
        except urllib2.HTTPError:
            irc.error("Unknown user (%s)" % id)
            return

        xml = minidom.parse(f).getElementsByTagName("profile")[0]
        keys = "realname registered age gender country playcount".split()
        profile = tuple([self._parse(xml, node) for node in keys])

        irc.reply(("%s (realname: %s) registered on %s; age: %s / %s; \
Country: %s; Tracks played: %s" % ((id,) + profile)).encode("utf8"))

    profile = wrap(profile, [optional("something")])

    def compareUsers(self, irc, msg, args, user1, optionalUser2):
        """user1 [<user2>]

        Compares the taste from two users
        If <user2> is ommitted, the taste is compared against the ID of the calling user.
        """

	name1 = user1
        name2 = msg.nick
        user2 = (self.db.getId(msg.nick) or msg.nick)

	if optionalUser2:
            name2 = optionalUser2
            user2 = (self.db.getId(optionalUser2) or optionalUser2)

	if self.db.getId(user1): user1 = self.db.getId(user1)
	    

        channel = msg.args[0]
        maxResults = self.registryValue("maxResults", channel)
        # see http://www.lastfm.de/api/show/tasteometer.compare
        url = "%s&method=tasteometer.compare&type1=user&type2=user&value1=%s&value2=%s&limit=%s" % (
            self.APIURL_2_0, user1, user2, maxResults
        )
        try:
            f = urllib2.urlopen(url)
        except urllib2.HTTPError, e:
            irc.error("Failure: %s" % (e))
            return

        xml = minidom.parse(f)
        resultNode = xml.getElementsByTagName("result")[0]
        score = float(self._parse(resultNode, "score")) 
        scoreStr = "%s (%s)" % (int(round(score, 2) * 100), self._formatRating(score))
        # Note: XPath would be really cool here...
        artists = [el for el in resultNode.getElementsByTagName("artist")]
        artistNames = [el.getElementsByTagName("name")[0].firstChild.data for el in artists]
        irc.reply(("Result of comparison between %s (%s) and %s (%s): score: %s, common artists: %s" \
                % (name1, user1, name2, user2, scoreStr, ", ".join(artistNames))
            ).encode("utf-8")
        )

    compare = wrap(compareUsers, ["something", optional("something")])

    def _parse(self, node, tagName, exceptMsg="not specified"):
            try:
                return node.getElementsByTagName(tagName)[0].firstChild.data
            except IndexError:
                return exceptMsg

    def _formatRating(self, score):
        """Format rating

        @param score Value in the form of [0:1] (float)
        """

        if score >= 0.9:
            return "Super"
        elif score >= 0.7:
            return "Very High"
        elif score >= 0.5:
            return "High"
        elif score >= 0.3:
            return "Medium"
        elif score >= 0.1:
            return "Low"
        else:
            return "Very Low"

    def _formatPlaycount(self, num):
        """Format playcount
    
	    @param num Number to ordinalize	
	    """
        # Taken from http://teachthe.net/?p=1165
        
        special_suffixes = { '1': 'st', '2': 'nd', '3': 'rd' }
        default_return = 'th'
        digits = str(abs(num)) # To work with negative numbers
        last_digit = digits[-1:]
        if last_digit in special_suffixes.keys():
            if len(digits) == 1 or digits[-2] != '1':
                default_return = special_suffixes[last_digit]
        
        return str(num) + default_return


dbfilename = conf.supybot.directories.data.dirize("LastFM.db")

Class = LastFM


# vim:set shiftwidth=4 tabstop=4 expandtab textwidth=79:
