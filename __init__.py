#!/usr/bin/env python
# -*- coding: utf-8 -*-
# vim:fileencoding=UTF-8:ts=4:sw=4:sta:et:sts=4:ai

# TODO:
# - create class to parse records, access data with "get"
# - or at least use functions

from __future__ import unicode_literals

__license__ = 'GPL v3'
__copyright__ = '2017, Bernhard Geier <geierb@geierb.de>'
__docformat__ = 'en'

from calibre.ebooks.metadata.sources.base import Source
from calibre.ebooks.metadata import check_isbn
from calibre.ebooks.metadata.book.base import Metadata
from calibre.library.comments import sanitize_comments_html
from calibre.utils.localization import lang_as_iso639_1
from calibre.ebooks import normalize

import re
import datetime
from unicodedata import numeric
from unicodedata import normalize as unicodedata_normalize

try:
    from urllib import quote  # Python2
except ImportError:
    from urllib.parse import quote  # Python3

from lxml import etree

try:
    from Queue import Queue, Empty  # Python2
except ImportError:
    from queue import Queue, Empty  # Python3

try:
    # Python 2
    from urllib2 import Request, urlopen,  HTTPError
except ImportError:
    # Python 3
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError

from lxml.html import fromstring, tostring
from calibre.utils.cleantext import clean_ascii_chars

load_translations()


class DNB_DE(Source):

    name = 'DNB_DE'
    description = _(
        'Downloads metadata from the DNB (Deutsche National Bibliothek).')
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'Citronalco'
    version = (3, 2, 4)
    minimum_calibre_version = (3, 48, 0)

    capabilities = frozenset(['identify', 'cover'])
    touched_fields = frozenset(['title', 'title_sort', 'authors', 'author_sort', 'publisher', 'pubdate', 'languages', 'tags', 'identifier:urn',
                                'identifier:idn', 'identifier:isbn', 'identifier:ddc', 'series', 'series_index', 'comments'])
    has_html_comments = False  # True
    # can_get_multiple_covers = False  # now optional
    supports_gzip_transfer_encoding = True
    cached_cover_url_is_reliable = True
    # prefer_results_with_isbn = True  # now optional
    ignore_ssl_errors = True

    MAXIMUMRECORDS = 10
    QUERYURL = 'https://services.dnb.de/sru/dnb?version=1.1&maximumRecords=%s&operation=searchRetrieve&recordSchema=MARC21-xml&query=%s'
    COVERURL = 'https://portal.dnb.de/opac/mvb/cover?isbn=%s'

    def load_config(self):
        # Config settings
        import calibre_plugins.DNB_DE.config as cfg
        self.cfg_guess_series = cfg.plugin_prefs[cfg.STORE_NAME].get(
            cfg.KEY_GUESS_SERIES, False)
        self.cfg_append_edition_to_title = cfg.plugin_prefs[cfg.STORE_NAME].get(
            cfg.KEY_APPEND_EDITION_TO_TITLE, False)
        self.cfg_fetch_subjects = cfg.plugin_prefs[cfg.STORE_NAME].get(
            cfg.KEY_FETCH_SUBJECTS, 2)
        self.cfg_fetch_all = cfg.plugin_prefs[cfg.STORE_NAME].get(
            cfg.KEY_FETCH_ALL, False)
        self.cfg_append_subtitle_to_title = cfg.plugin_prefs[cfg.STORE_NAME].get(
            cfg.KEY_APPEND_SUBTITLE_TO_TITLE, True)
        self.cfg_stop_after_first_hit = cfg.plugin_prefs[cfg.STORE_NAME].get(
            cfg.KEY_STOP_AFTER_FIRST_HIT, True)
        self.cfg_prefer_results_with_isbn = cfg.plugin_prefs[cfg.STORE_NAME].get(
            cfg.KEY_PREFER_RESULTS_WITH_ISBN, True)
        self.prefer_results_with_isbn = self.cfg_prefer_results_with_isbn
        self.set_prefer_results_with_isbn(self.cfg_prefer_results_with_isbn)
        self.cfg_can_get_multiple_covers = cfg.plugin_prefs[cfg.STORE_NAME].get(
            cfg.KEY_CAN_GET_MULTIPLE_COVERS, True)
        self.can_get_multiple_covers = self.cfg_can_get_multiple_covers
        self.set_can_get_multiple_covers(self.cfg_can_get_multiple_covers)
        self.cfg_editor_patterns = cfg.plugin_prefs[cfg.STORE_NAME].get(
            cfg.KEY_EDITOR_PATTERNS, [])
        self.cfg_artist_patterns = cfg.plugin_prefs[cfg.STORE_NAME].get(
            cfg.KEY_ARTIST_PATTERNS, [])
        self.cfg_translator_patterns = cfg.plugin_prefs[cfg.STORE_NAME].get(
            cfg.KEY_TRANSLATOR_PATTERNS, [])
        self.cfg_foreword_patterns = cfg.plugin_prefs[cfg.STORE_NAME].get(
            cfg.KEY_FOREWORD_PATTERNS, [])
        self.cfg_show_marc21_field_numbers = cfg.plugin_prefs[cfg.STORE_NAME].get(
            cfg.KEY_SHOW_MARC21_FIELD_NUMBERS, False)
        self.cfg_skip_series_starting_with_publishers_name = cfg.plugin_prefs[cfg.STORE_NAME].get(
            cfg.KEY_SKIP_SERIES_STARTING_WITH_PUBLISHERS_NAME, True)
        self.cfg_unwanted_series_names = cfg.plugin_prefs[cfg.STORE_NAME].get(
            cfg.KEY_UNWANTED_SERIES_NAMES, [])


    @classmethod
    def set_prefer_results_with_isbn(cls, prefer):
        cls.prefer_results_with_isbn = prefer

    @classmethod
    def set_can_get_multiple_covers(cls, prefer):
        cls.can_get_multiple_covers = prefer

    def config_widget(self):
        self.cw = None
        from calibre_plugins.DNB_DE.config import ConfigWidget
        return ConfigWidget(self)

    def is_customizable(self):
        return True

    def num_groups(self, regex):
        return re.compile(regex).groups

    def identify(self, log, result_queue, abort, title=None, authors=[], identifiers={}, timeout=30):
        self.load_config()

        if authors is None:
            authors = []

        # get identifying tags from book
        idn = identifiers.get('dnb-idn', None)
        isbn = check_isbn(identifiers.get('isbn', None))

        #isbn = None
        #authors = []

        # remove pseudo authors from list of authors
        ignored_authors = ['v. a.', 'v.a.', 'va', 'diverse', 'unknown', 'unbekannt', 'anonymous']
        for i in ignored_authors:
            authors = [x for x in authors if x.lower() != i.lower()]

        # exit on insufficient inputs
        if not isbn and not idn and not title and not authors:
            log.info(
                "This plugin requires at least either ISBN, IDN, Title or Author(s).")
            return None

        # process queries
        results = None
        query_success = False

        for query in self.create_query_variations(log, idn, isbn, authors, title):
            results = self.execute_query(log, query, timeout)
            if not results:
                continue

            log.info("Parsing records")

            ns = {'marc21': 'http://www.loc.gov/MARC21/slim'}

            for record in results:
                book = {
                    'series': None,
                    'series_index': None,
                    'pubdate': None,
                    'originally_published': None,
                    'language': None,
                    'languages': [],
                    'title': None,
                    'title_sort': None,
                    'subtitle': None,
                    'subseries': None,
                    'subseries_index': None,
                    'authors': [],
                    'author_sort': None,
                    'edition': None,
                    'editor': None,
                    'foreword': None,
                    'artist': None,
                    'original_language': None,
                    'original_pubdate': None,
                    'original_version_note': None,
                    'translator': None,
                    'extent': None,
                    'other_physical_details': None,
                    'dimensions': None,
                    'accompanying_material': None,
                    'terms_of_availability': None,
                    'mediatype': None,
                    'tags': [],
                    'comments': None,
                    'record_uri': None,
                    'idn': None,
                    'urn': None,
                    'isbn': None,
                    'gnd': None,
                    'ddc': [],
                    'ddc_subject_area': [],
                    'subjects_gnd': [],
                    'subjects_non_gnd': [],
                    'publisher_name': None,
                    'publisher_location': None,

                    'alternative_xmls': [], 
                }

                ##### For plugin extending purposes, document all fields in MARC21 record for this book here
                marc21_fields = record.xpath("//marc21:datafield/@tag", namespaces=ns)

                ##### Field 336: "Content Type" #####
                # Skip Audio Books
                mediatype = None  # Avoid "error message "UnboundLocalError: cannot access local variable 'mediatype'
                # where it is not associated with a value"
                try:
                    mediatype = record.xpath("./marc21:datafield[@tag='336']/marc21:subfield[@code='a' and string-length(text())>0]", namespaces=ns)[0].text.strip().lower()
                    if mediatype in ('gesprochenes wort'):
                        log.info("mediatype %s ignored." % mediatype)
                        continue
                except:
                    pass


                ##### Field 337: "Media Type" #####
                # Skip Audio and Video
                mediatype = None  # Avoid "error message "UnboundLocalError: cannot access local variable 'mediatype'
                # where it is not associated with a value"
                try:
                    mediatype = record.xpath("./marc21:datafield[@tag='337']/marc21:subfield[@code='a' and string-length(text())>0]", namespaces=ns)[0].text.strip().lower()
                    if mediatype in ('audio', 'video'):
                        log.info("mediatype %s ignored." % mediatype)
                        continue
                except:
                    pass

                if mediatype:
                    book['mediatype'] = mediatype
                else:
                    book['mediatype'] = ''

                ##### Field 776: "Additional Physical Form Entry" #####
                # References from ebook's entry to paper book's entry (and vice versa)
                # Often only one of them contains comments or a cover
                # Example: dnb-idb=1136409025
                for i in record.xpath("./marc21:datafield[@tag='776']/marc21:subfield[@code='w' and string-length(text())>0]", namespaces=ns):
                    other_idn = re.sub("^\(.*\)", "", i.text.strip())
                    log.info("[776.w] Found other issue with IDN %s" % other_idn)
                    altquery = 'num=%s NOT (mat=film OR mat=music OR mat=microfiches OR cod=tt)' % other_idn
                    altresults = self.execute_query(log, altquery, timeout)
                    if altresults:
                        book['alternative_xmls'].append(altresults[0])


                ##### Field 264: "Production, Publication, Distribution, Manufacture, and Copyright Notice" #####
                # Get Publisher Name, Publishing Location, Publishing Date
                # Subfields:
                # a: publishing location
                # b: publisher name
                # c: publishing date
                for field in record.xpath("./marc21:datafield[@tag='264']", namespaces=ns):
                    if book['publisher_name'] and book['publisher_location'] and book['pubdate']:
                        break

                    if not book['publisher_location']:
                        location_parts = []
                        for i in field.xpath("./marc21:subfield[@code='a' and string-length(text())>0]", namespaces=ns):
                            # Consider the sequence:
                            # datafield 1:
                            # <subfield code="a">[s. l.] @</subfield>
                            # <subfield code="b">[s. n.] @</subfield>
                            # <subfield code="c">1941</subfield>
                            # datafield 2:
                            # <subfield code="a">Leipzig</subfield>
                            # <subfield code="b">Brockhaus</subfield>
                            # May contain the abbreviation [S.l.] when the place is unknown.
                            if '[s. l.]' not in i.text and '[s.l.]' not in i.text:
                                location_parts.append(i.text.strip())
                        if location_parts:
                            book['publisher_location'] = ' ; '.join(location_parts).strip('[]')

                    if not book['publisher_name']:
                        try:
                            # May contain the abbreviation [s.n.] when the name is unknown.
                            publisher_parts = field.xpath("./marc21:subfield[@code='b' "
                                                                 "and string-length(text())>0]", namespaces=ns)[0].text.strip()
                            if '[s. n.]' not in publisher_parts and '[s.n.]' not in publisher_parts:
                                book['publisher_name'] = publisher_parts
                            log.info("[264.b] Publisher: %s" % book['publisher_name'])
                        except IndexError:
                            pass

                    if not book['pubdate']:
                        try:
                            pubdate = field.xpath("./marc21:subfield[@code='c' "
                                                  "and string-length(text())>=4]", namespaces=ns)[0].text.strip()
                            match = re.search("(\d{4})", pubdate)
                            year = match.group(1)
                            book['pubdate'] = datetime.datetime(int(year), 1, 1, 12, 30, 0)
                            log.info("[264.c] Publication Year: %s" % book['pubdate'])
                        except (IndexError, AttributeError):
                            pass


                ##### Field 534 - "Original Version Note" #####
                for field in record.xpath("./marc21:datafield[@tag='534']", namespaces=ns):
                    field_534 = []
                    for i in field.xpath("./marc21:subfield[string-length(text())>0]", namespaces=ns):
                        field_534.append(field.xpath("./marc21:subfield/name()", namespaces=ns) + ':' + i.text.strip())
                    book['original_version_note'] = ' / '.join(field_534)


                ##### Field 008 - "Control Field 008" #####
                # <controlfield tag="008">240627r20241795xx u||p|o ||| 0||||1ger  </controlfield
                # gives in DBB catalog GUI:
                # Zeitliche Einordnung	Erscheinungsdatum: 2024 Original: 1795
                # see: https://www.loc.gov/marc/archive/2000/concise/ecbd008s.html
                # Character Positions
                # All materials
                # 00-05 - Date entered on file
                # 06 - Type of date/Publication status
                # 07-10 - Date 1
                # 11-14 - Date 2
                # 15-17 - Place of publication, production, or execution
                # 18-34 - [See one of the seven separate 008/18-34 configuration sections for these elements.]
                # 35-37 - Language
                # 38 - Modified record
                # 39 - Cataloging source
                if record.xpath("./marc21:controlfield[@tag='008']", namespaces=ns):
                    date2 = record.xpath("./marc21:controlfield[@tag='008']/text()", namespaces=ns)
                    # log.info("date2=%s" % date2)
                    # ['170608r20171917gw |||||o|||| 00||||ger  ']
                    date2 = ''.join(date2)[11:15].strip()
                    if date2 != '':
                        book['original_pubdate'] = date2


                ##### Field 245: "Title Statement" #####
                # Get Title, Series, Series_Index, Subtitle
                # Subfields:
                # a: title
                # b: subtitle 1
                # n: number of part
                # p: name of part

                # Examples:
                # a = "The Endless Book", n[0] = 2, p[0] = "Second Season", n[1] = 3, p[1] = "Summertime", n[2] = 4, p[2] = "The Return of Foobar"	Example: dnb-id 1008774839
                # ->	Title:		"The Return Of Foobar"
                #	Series:		"The Endless Book 2 - Second Season 3 - Summertime"
                #	Series Index:	4

                # a = "The Endless Book", n[0] = 2, p[0] = "Second Season", n[1] = 3, p[1] = "Summertime", n[2] = 4"
                # ->	Title:		"Summertime 4"
                #	Series:		"The Endless Book 2 - Second Season 3 - Summertime"
                #	Series Index:	4

                # a = "The Endless Book", n[0] = 2, p[0] = "Second Season", n[1] = 3, p[1] = "Summertime"
                # ->	Title:		"Summertime"
                #	Series:		"The Endless Book 2 - Second Season"
                #	Series Index:	3

                # a = "The Endless Book", n[0] = 2, p[0] = "Second Season", n[1] = 3"	Example: 956375146
                # ->	Title:		"Second Season 3"	n=2, p =1
                #	Series:		"The Endless Book 2 - Second Season"
                #	Series Index:	3

                # a = "The Endless Book", n[0] = 2, p[0] = "Second Season"
                # ->	Title:		"Second Season"	n=1,p=1
                #	Series:		"The Endless Book"
                #	Series Index:	2

                # a = "The Endless Book", n[0] = 2"
                # ->	Title: 		"The Endless Book 2"
                #	Series:		"The Endless Book"
                #	Series Index:	2

                # Caching indicators
                if record.xpath("./marc21:datafield[@tag='245']", namespaces=ns):
                    # 0 = Keine Nebeneintragung, 1 = Nebeneintragung (im Datensatz ist eines der Felder 1XX vorhanden)
                    ind1 = record.xpath("./marc21:datafield[@ind1]", namespaces=ns)[0].text.strip()
                    # Anzahl der zu übergehenden Zeichen: 0 = default
                    ind2 = record.xpath("./marc21:datafield[@ind2]", namespaces=ns)[0].text.strip()
                    if len(ind1) > 0:
                        added_entry = not int(ind1)  # 245 Title statement 1 = added entry (Nebeneintragung)
                    else:
                        added_entry = False
                    no_added_entry = not added_entry
                    log.info("added_entry=%s" % added_entry)

                for field in record.xpath("./marc21:datafield[@tag='245']", namespaces=ns):
                    title_parts = []

                    # Title (Titel)
                    code_a = []
                    for i in field.xpath("./marc21:subfield[@code='a' and string-length(text())>0]", namespaces=ns):
                        code_a.append(i.text.strip())
                        log.info("[245.a] code_a=%s" % code_a)
                    code_b = []
                    for i in field.xpath("./marc21:subfield[@code='b' and string-length(text())>0]", namespaces=ns):
                        code_b.append(i.text.strip())
                        log.info("[245.b] code_b=%s" % code_b)
                    # Statement of responsibility etc. (Verfasserangabe etc.)
                    code_c = []
                    code_c_authors = []
                    for i in field.xpath("./marc21:subfield[@code='c' and string-length(text())>0]", namespaces=ns):
                        code_c.append(i.text.strip())
                        log.info("[245.c] code_c=%s" % code_c)
                    # Number of part / section of a work (Zählung eines Teils / einer Abteilung eines Werkes)
                    code_n = []
                    for i in field.xpath("./marc21:subfield[@code='n' and string-length(text())>0]", namespaces=ns):
                        match = re.search("(\d+([,\.]\d+)?)", i.text.strip())
                        if match:
                            code_n.append(match.group(1))
                        else:
                            # looks like sometimes DNB does not know the series_index and uses something like "[...]"
                            match = re.search("\[\.\.\.\]", i.text.strip())
                            if match:
                                code_n.append('0')
                    log.info("[245.n] code_n=%s" % code_n)
                    # Name of part / section of a work (Titel eines Teils / einer Abteilung eines Werkes)
                    code_p = []
                    for i in field.xpath("./marc21:subfield[@code='p' and string-length(text())>0]", namespaces=ns):
                        code_p.append(i.text.strip())
                    log.info("[245.p] code_p=%s" % code_p)

                    # Extract remainder of title (Zusatz zum Titel)
                    if code_c:

                        # Step 1: Cleaning and marking parts by uniforming identifiers
                        code_c = [s + '%%' for s in code_c]  # Mark end of code c entry
                        # code_c = list(map(lambda x: x.replace('[', ''), code_c))  # General replacings
                        code_c = [re.sub('\[', '', code_c_element) for code_c_element in code_c]  # General replacings
                        code_c = [re.sub('\]', '', code_c_element) for code_c_element in code_c]  # General replacings
                        log.info("self.cfg_editor_patterns=%s" % self.cfg_editor_patterns)
                        # No break after first match, because more than one person can be involved
                        for pattern in self.cfg_editor_patterns:
                            log.info("pattern={0}".format(pattern))
                            # code_c = list(map(lambda x: x.replace(delimiter, '%%e:'), code_c))  ## Mark editor
                            code_c = [re.sub(pattern, '%%e:', code_c_element) for code_c_element in code_c]   ## Mark editor
                            log.info("code_c={0}".format(code_c))
                        log.info("self.cfg_artist_patterns=%s" % self.cfg_artist_patterns)
                        for pattern in self.cfg_artist_patterns:
                            log.info("pattern={0}".format(pattern))
                            code_c = [re.sub(pattern, '%%a:', code_c_element) for code_c_element in code_c]   ## Mark artist
                            log.info("code_c={0}".format(code_c))

                        # Translator and original language perhaps combined:
                        log.info("self.cfg_translator_patterns=%s" % self.cfg_translator_patterns)
                        for pattern in self.cfg_translator_patterns:
                            log.info("pattern={0}".format(pattern))
                            pattern = unicodedata_normalize("NFKC", pattern)  # Beware of German umlauts
                            # What type of pattern do we have:
                            if self.num_groups(pattern) > 1:
                                # ['([Aa]us (?:dem|d\.) (.*) .* von) (.*)%%']:
                                # Pattern with original_language and translator, so extract original language first
                                for code_c_element in code_c:  # ToDo: c element is not repetitive
                                    # Use regex to extracting language
                                    log.info("[245.c] code_c_element=%s" % code_c_element)
                                    match = re.search(pattern, code_c_element)  # Search until first '%%' (non-greedy)
                                    if match:
                                        if len(match.groups()) > 1:
                                            if match.group(2) and match.group(3):
                                                book['original_language'] = match.group(2)
                                                book['original_language'].removesuffix('en')
                                                code_c = list(map(lambda x: x.replace(match.group(1), '%%t:'),
                                                                  code_c))  ## Mark translator
                            else:
                                code_c = [re.sub(pattern, '%%t:', code_c_element) for code_c_element in code_c]   ## Mark translator
                            log.info("code_c={0}".format(code_c))

                        log.info("self.cfg_foreword_patterns=%s" % self.cfg_foreword_patterns)
                        for pattern in self.cfg_foreword_patterns:
                            log.info("pattern={0}".format(pattern))
                            code_c = [re.sub(pattern, '%%f:', code_c_element) for code_c_element in code_c]   ## Mark foreword
                            log.info("code_c={0}".format(code_c))

                        log.info("[245.c] code_c after uniforming identifiers=%s" % code_c)

                        # Step 2: Identifiying parts (more than one part of same type possible)
                        # ToDo: 245.c is non repetitive! $c - Statement of responsibility, etc. (NR)
                        # ToDo: Put code duplication in loop? (memory issue!)
                        # ToDo: Parsing ' ; ' as seperator:
                        # 245.c] code_c=['J.R.R. Tolkien ; mit Illustrationen von Pauline Baynes ; aus dem Englischen übertragen von Ebba-Margareta von Freymann']
                        for code_c_element in code_c:
                            match = re.search("%%e:(.*?)%%", code_c_element)  # Search until first '%%' (non-greedy)
                            if match:
                                if book['editor']:
                                    book['editor'] = book['editor'] + ' / ' + match.group(1).strip().strip('.').strip()
                                else:
                                    book['editor'] = match.group(1).strip().strip('.').strip()
                                log.info("book['editor']=%s" % book['editor'])
                                code_c = list(map(lambda x: x.replace('%%e:' + match.group(1), ''), code_c))  ## strip match
                        for code_c_element in code_c:
                            match = re.search("%%e:(.*?)%%", code_c_element)  # Search until first '%%' (non-greedy)
                            if match:
                                if book['editor']:
                                    book['editor'] = book['editor'] + ' / ' + match.group(1).strip().strip('.').strip()
                                else:
                                    book['editor'] = match.group(1).strip().strip('.').strip()
                                log.info("book['editor']=%s" % book['editor'])
                                code_c = list(map(lambda x: x.replace('%%e:' + match.group(1), ''), code_c))  ## strip match
                        for code_c_element in code_c:
                            match = re.search("%%a:(.*?)%%", code_c_element)  # Search until first '%%' (non-greedy)
                            if match:
                                if book['artist']:
                                    book['artist'] = book['artist'] + ' / ' + match.group(1).strip().strip('.').strip()
                                else:
                                    book['artist'] = match.group(1).strip().strip('.').strip()
                                log.info("book['artist']=%s" % book['artist'])
                                code_c = list(map(lambda x: x.replace('%%a:' + match.group(1), ''), code_c))  ## strip match
                        for code_c_element in code_c:
                            match = re.search("%%t:(.*?)%%", code_c_element)  # Search until first '%%' (non-greedy)
                            if match:
                                if book['translator']:
                                    book['translator'] = book['translator'] + ' / ' + match.group(1).strip().strip('.').strip()
                                else:
                                    book['translator'] = match.group(1).strip().strip('.').strip()
                                log.info("book['translator']=%s" % book['translator'])
                                code_c = list(map(lambda x: x.replace('%%t:' + match.group(1), ''), code_c))  ## strip match
                        for code_c_element in code_c:
                            match = re.search("%%t:(.*?)%%", code_c_element)  # Search until first '%%' (non-greedy)
                            if match:
                                if book['translator']:
                                    book['translator'] = book['translator'] + ' / ' + match.group(1).strip().strip('.').strip()
                                else:
                                    book['translator'] = match.group(1).strip().strip('.').strip()
                                log.info("book['translator']=%s" % book['translator'])
                                code_c = list(map(lambda x: x.replace('%%t:' + match.group(1), ''), code_c))  ## strip match
                        for code_c_element in code_c:
                            match = re.search("%%f:(.*?)%%", code_c_element)  # Search until first '%%' (non-greedy)
                            if match:
                                if book['foreword']:
                                    book['foreword'] = book['foreword'] + ' / ' + match.group(1).strip().strip('.').strip()
                                else:
                                    book['foreword'] = match.group(1).strip().strip('.').strip()
                                log.info("book['foreword']=%s" % book['foreword'])
                                code_c = list(map(lambda x: x.replace('%%f:' + match.group(1), ''), code_c))  ## strip match
                        # Is there a remainder?
                        for code_c_element in code_c:
                            code_c = list(map(lambda x: x.replace('%%', ''), code_c))  ## strip delimiters
                        log.info("code_c after stripping=%s" % code_c)
                        # [245.a] code_a=['Deutsches Märchenbuch']
                        # [245.b] code_b=['Mit Illustrationen von Ludwig Richter']
                        # [245.c] code_c=['Ludwig Bechstein ; Illustrator: Ludwig Richter']
                        # ---
                        # 245.a] code_a=['Spannende Geschichten']
                        # [245.c] code_c=['Hrsg. von Günther Bicknese. Ill. von Günter Büsemeyer']
                        # [245.n] code_n=['17']
                        # [245.p] code_p=['Start ins Ungewisse / [Von] Heinz Helfgen']
                        # ---
                        # [245.a] code_a=['Soldatensaga']
                        # [245.c] code_c=['W. E. B. Griffin']
                        # [245.n] code_n=['1']
                        # [245.p] code_p=['Lieutenants']
                        # ---
                        # [245.a] code_a=['\x98Das\x9c Buch der verschollenen Geschichten']
                        # [245.c] code_c=['J.R.R. Tolkien ; Christopher Tolkien ; aus dem Englischen übersetzt von Hans J. Schütz']
                        # [245.n] code_n=['1']
                        if code_c[0] == '':
                            code_c_authors = []
                        else:
                            # [245.c] code_c=['ein Märchen von Marie von Ebner-Eschenbach ; Buchschmuck von Hanns Anker']
                            match = re.search("^.* von (.*)", code_c[0])
                            if match:
                                code_c[0] = match.group(1)
                            # code_c after stripping=['J.R.R. Tolkien ; Christopher Tolkien ; ']
                            code_c_authors_str = code_c[0].lstrip('von ').strip().strip('.').strip()
                            if ' ; ' in code_c_authors_str:
                                code_c_authors = [x.strip() for x in code_c_authors_str.split(' ; ')]
                                log.info("code_c_authors=%s" % code_c_authors)
                                code_c_authors[:] = [item for item in code_c_authors if item != '']
                        log.info("code_c_authors=%s" % code_c_authors)
                        if code_c_authors:
                            book['authors'].extend(code_c_authors)
                            log.info("book['authors']=%s" % book['authors'])

                    # ToDo:

                    # a + b + c:
                    # [245.a] ['Abdahn Effendi']
                    # [245.b] ['Eine Reiseerzählung']
                    # [245.c] ['Karl May']
                    # ---
                    #     <datafield tag="245" ind1="1" ind2="0">
                    #       <subfield code="a">&#152;Der&#156; beiden Quitzows letzte Fahrten</subfield>
                    #       <subfield code="b">historischer Roman</subfield>
                    #       <subfield code="c">von Karl May</subfield>
                    # ---
                    # a + c:
                    # [245.a] ['Torn 27 - Die letzte Kolonie']
                    # [245.c] ['Michael J. Parrish']
                    # ---
                    # [245.a] ['Abdahn Effendi']
                    # [245.b] ['Reiseerzählungen und Texte aus dem Spätwerk, Band 81 der Gesammelten Werke']
                    # [245.c] ['Karl May']

                    # 245.a + 249.a:
                    # [245.a] ['Abdahn Effendi']
                    # [249.a] ['Schamah. 2 Erzählungen / Von Karl May. [Mit e. Vorw. hrsg. von Thomas Ostwald]. Nachdr. aus d. "Bibliothek Saturn" 1909 u. 1911']

                    #     <datafield tag="245" ind1="1" ind2="0">
                    #       <subfield code="a">Auf dem Jakobsweg</subfield>
                    #       <subfield code="b">Tagebuch einer Pilgerreise nach Santiago de Compostela</subfield>
                    #       <subfield code="c">Paulo Coelho. Aus dem Brasilianischen von Maralde Meyer-Minnemann</subfield>

                    #     <datafield tag="245" ind1="1" ind2="0">
                    #       <subfield code="a">Märchen aus Bayern</subfield>
                    #       <subfield code="b">Märchen der Welt</subfield>
                    #       <subfield code="c">Karl Spiegel</subfield>

                    #     <datafield tag="245" ind1="0" ind2="0">
                    #       <subfield code="a">&#152;Die&#156; Horen</subfield>
                    #       <subfield code="n">1.1795, Stück 1-12 = Bd. 1-4</subfield>
                    #       <subfield code="c">hrsg. von Schiller</subfield>

                    #     <datafield tag="245" ind1="1" ind2="0">
                    #       <subfield code="a">Am Kamin und andere unheimliche Geschichten</subfield>
                    #       <subfield code="c">Theodor Storm. Mit Ill. von Roswitha Quadflieg. Ausgew. und mit einem Nachw. von Gottfried Honnefelder</subfield>

                    # a = series, n = series index, p = title and author
                    # <subfield code="a">Spannende Geschichten</subfield>
                    # <subfield code="n">119.</subfield>
                    # <subfield code="p">Schiffbruch zwischen Erde und Mond / [Von] Henry Gamarick. Ill.: G. Büsemeyer [u.a.]</subfield>
                    # <subfield code="c">Hrsg. von Günther Bicknese. Ill. von Günter Büsemeyer</subfield>

                    #   <subfield code="a">Fliegergeschichten</subfield>
                    #   <subfield code="n">Bd. 188.</subfield>
                    #   <subfield code="p">Über der Hölle des Mauna Loa / Otto Behrens</subfield>

                    # <datafield tag="245" ind1="1" ind2="0">
                    #   <subfield code="a">&#152;Die&#156; Odyssee der PN-9</subfield>
                    #   <subfield code="c">Fritz Moeglich. Hrsg.: Peter Supf</subfield>

                    # [245.a] code_a=['\x98Die\x9c Welt der Planeten']
                    # [245.c] code_c=['M. W. Meyer. Neu bearb. von Cuno Hoffmeister']
                    # [245.n] code_n=[]
                    # [245.p] code_p=[]

                    # [245.a] code_a=['Deutsche Dichtung']
                    # [245.c] code_c=['hrsg. und eingeleitet von Stefan George und Karl Wolfskehl']
                    # [245.n] code_n=['1']
                    # [245.p] code_p=['Jean Paul']

                    if code_a and code_b and code_c:
                        # perhabps title - series, subseries and author
                        # [245.a] code_a=['\x98Ein\x9c Glas voll Mord – DuMonts Digitale Kriminal-Bibliothek']
                        # [245.b] code_b=['Inspektor Madoc-Rhys']
                        # [245.c] code_c=['Charlotte MacLeod']
                        # ---
                        # [245.a] code_a=["Appleby's End – DuMonts Digitale Kriminal-Bibliothek"]
                        # [245.b] code_b=['Inspektor-Appleby-Serie']
                        # [245.c] code_c=['Michael Innes']
                        # ---
                        # ToDo? (series and series index in field 830; 245.a = series in first edition):
                        # [245.a] code_a=['Special Force One 01']
                        # [245.b] code_b=['Der erste Einsatz']
                        # [245.c] code_c=['Michael J. Parrish']
                        # Step 2: Identifiying parts
                        for code_a_element in code_a:
                            # ToDo: Move to clean_series
                            # [245.a] code_a=["Appleby's End – DuMonts Digitale Kriminal-Bibliothek"]
                            # [245.b] code_b=['Inspektor-Appleby-Serie']
                            # [245.c] code_c=['Michael Innes']
                            for pattern in ['DuMonts Digitale Kriminal-Bibliothek']:  # main series
                                match = re.search(pattern, code_a_element)
                                if match:
                                    code_a = list(map(lambda x: x.replace(pattern, '').strip(), code_a))
                                    book['series'] = pattern
                                    if code_c:
                                        book['subseries'] = code_b[0]  # ToDo: subseries_index?
                                        code_b[0] = None
                                        book['tags'].append(book['subseries'])
                                    for code_a_element in code_a:
                                        for general_dash in ['-', '–', '—']:  # hyphen, en dash, em dash
                                            general_dash = unicodedata_normalize("NFKC", general_dash)
                                            code_a = list(map(lambda x: x.strip().strip(general_dash.strip()), code_a))
                                    book['series'] = pattern
                                    break  # Search until first match

                            match = re.search("%%e:(.*?)%%", code_c_element)  # Search until first '%%' (non-greedy)
                            if match:
                                book['editor'] = match.group(1).strip().strip('.').strip()
                                log.info("book['editor']=%s" % book['editor'])
                                code_c = list(map(lambda x: x.replace('%%e:' + match.group(1), ''), code_c))  ## strip match

                    # Caching subtitle
                    if code_a and code_b:  #  and not code_c:
                        # [245.a] code_a=['Tödliche Weihnachten – DuMonts Digitale Kriminal-Bibliothek']
                        # [245.b] code_b=['Ein mörderisches Adventspaket']
                        #
                        # [245.a] code_a=['XML und VBA lernen']
                        # [245.b] code_b=['Anfangen, anwenden, verstehen']
                        # [245.c] code_c=['René Martin']
                        for code_a_element in code_a:
                            # ToDo: Move to clean_series
                            for pattern in ['DuMonts Digitale Kriminal-Bibliothek']:  # main series
                                match = re.search(pattern, code_a_element)
                                if match:
                                    code_a = list(map(lambda x: x.replace(pattern, ''), code_a))
                                    book['series'] = pattern
                                    for code_a_element in code_a:
                                        for general_dash in ['-', '–', '—']:  # hyphen, en dash, em dash
                                            general_dash = unicodedata_normalize("NFKC", general_dash)
                                            code_a = list(map(lambda x: x.strip().strip(general_dash.strip()), code_a))
                                    break  # Search until first match
                        if code_b[0]:
                            if code_b[0] not in ['Reiseerzählung', 'Roman', 'Erzählung', 'Kriminalroman']:
                                book['subtitle'] = code_b[0].strip('[]')
                            else:
                                book['tags'].append(code_b[0])

                    # ToDo:
                    # <datafield tag="245" ind1="1" ind2="0">
                    #       <subfield code="a">Durch die Wüste</subfield>
                    #       <subfield code="c">Reiseerzählung von Karl May</subfield>
                    #     </datafield>

                    # a = series, n = series index, p = title and author and perhaps more
                    # [245.a] code_a=['Spannende Geschichten']
                    # [245.c] code_c=['Hrsg. von Günther Bicknese. Ill. von Günter Büsemeyer']
                    # [245.n] code_n=['17']
                    # [245.p] code_p=['Start ins Ungewisse / [Von] Heinz Helfgen']
                    # ---
                    # [245.a] code_a=['Spannende Geschichten']
                    # [245.c] code_c=['Hrsg. von Günther Bicknese. Ill. von Günter Büsemeyer']
                    # [245.n] code_n=['145']
                    # [245.p] code_p=['SOS aus Unbekannt / [Von] Walter G. Brandecker. Textill.: H. Arlart u. G. Büsemeyer']
                    # ---
                    # [245.a] code_a=['Spannende Geschichten']
                    # [245.c] code_c=['Hrsg. von Günther Bicknese. Ill. von Günter Büsemeyer']
                    # [245.n] code_n=['119']
                    # [245.p] code_p=['Schiffbruch zwischen Erde und Mond / [Von] Henry Gamarick. Ill.: G. Büsemeyer [u.a.]']
                    code_p_title = ''
                    code_p_authors = []
                    if code_a and code_n and code_p:  # and not and code_c ?
                        # [245.a] code_a=['Rolf Torring']
                        # [245.n] code_n=['502']
                        # [245.p] code_p=['\x98Die\x9c Teufelsfratze']

                        # ToDo: Code_p is possible repeated: $p - Name of part/section of a work (R)
                        # $p - Name of part/section of a work
                        # Name of a part/section of a work in a title.
                        # In records formulated according to ISBD principles, subfield $p data follows a period (.)
                        # when it is preceded by subfield $a, $b or another subfield $p. Subfield $p follows a comma (,)
                        # when it follows subfield $n.
                        # 245	10$aAdvanced calculus.$pStudent handbook.
                        # 245	10$aInternationale Strassenkarte.$pEurope 1:2.5 Mio. :$bmit Register = International
                        # road map.$pEurope, 1:2.5 mio : with index /$cRV Reise- und Verkehrsverlag.
                        # 245	00$aHistorical statistics.$pSupplement /$c...
                        # 245	00$aDissertation abstracts.$nA,$pThe humanities and social sciences.
                        # 245	00$aDeutsche Bibliographie.$pWöchentliches Verzeichnis.$nReihe B,$pBeilage,
                        # Erscheinungen ausserhalb des Verlagsbuchhandels :$bAmtsblatt der Deutschen Bibliothek.
                        # Subfields $n and $p are repeated only when following a subfield $a, $b, $n, or $p.
                        # If a title recorded in subfield $c includes the name and/or number of a part/section,
                        # those elements are not separately subfield coded.
                        # https://www.loc.gov/marc/bibliographic/bd245.html

                        for code_p_element in code_p:
                            code_p_element_split = code_p_element.split(" / ")
                            code_p_title = code_p_element_split[0].strip().strip('.')
                            book['title'] = code_p_title
                            log.info("book['title']=%s" % book['title'])
                            if len(code_p_element_split) > 1:
                                code_p_element_remainder = code_p_element_split[1].strip().strip('.') + '%%'
                                # Check the second part of code p for authors, artists, etc.
                                for pattern in self.cfg_artist_patterns:
                                    log.info("pattern={0}".format(pattern))
                                    code_p_element_remainder = re.sub(pattern, '%%a:', code_p_element_remainder)  ## Mark authors
                                    log.info("code_p_element_remainder={0}".format(code_p_element_remainder))
                                match = re.search("%%a:(.*?)%%", code_p_element_remainder)  # Search until first '%%' (non-greedy)
                                if match:
                                    if book['artist']:
                                        book['artist'] = book['artist'] + ' / ' + match.group(1).strip().strip('.').strip()
                                    else:
                                        book['artist'] = match.group(1).strip().strip('.').strip()
                                    code_p_element_remainder = code_p_element_remainder.replace('%%a:' + match.group(1), '')  ## strip match
                                    log.info("book['artist']=%s" % book['artist'])

                                # ToDo: Extract language and translator as in code_c
                                for pattern in self.cfg_translator_patterns:
                                    log.info("pattern={0}".format(pattern))
                                    code_p_element_remainder = re.sub(pattern, '%%t:', code_p_element_remainder)  ## Mark authors
                                    log.info("code_p_element_remainder={0}".format(code_p_element_remainder))

                                for pattern in ['\[Von\]', '\[von\]', '[Vv]on']:
                                    log.info("pattern={0}".format(pattern))
                                    code_p_element_remainder = re.sub(pattern, '%%w:', code_p_element_remainder)  ## Mark authors
                                    log.info("code_p_element_remainder={0}".format(code_p_element_remainder))
                                # ToDo: "von Christian Montillon. Nach einer Story von Michael J. Parrish%%"
                                match = re.search("%%w:(.*?)%%", code_p_element_remainder)  # Search until first '%%' (non-greedy)
                                # Cave: Books without authors
                                # [245.a] code_a=['Spannende Geschichten']
                                # [245.c] code_c=['Hrsg. von Günther Bicknese. Ill. von Günter Büsemeyer']
                                # [245.n] code_n=['128']
                                # [245.p] code_p=['Die Goldgräberbank von Sacramento / Ill.: H. Arlat ; G. Büsemeyer']
                                if match:
                                    log.info("match.group(1)={0}".format(match.group(1)))
                                    log.info("code_p_authors={0}".format(code_p_authors))
                                    code_p_authors.append(match.group(1).strip().strip('.').strip())
                                    log.info("code_p_authors=%s" % code_p_authors)

                    if code_p_authors:
                        book['authors'].extend(code_p_authors)
                        log.info("book['authors']=%s" % book['authors'])

                    # Title
                    if code_p:
                        title_parts = [code_p_title]
                    else:
                        title_parts = code_a
                    log.info("title_parts=%s" % title_parts)

                    # Looks like we have a series
                    if code_a and code_n:
                        # set title ("Name of this Book")
                        # if code_p:
                        #     title_parts = [code_p[-1]]

                        # build series name
                        series_parts = [code_a[0]]
                        for i in range(0, min(len(code_p), len(code_n)) - 1):
                            series_parts.append(code_p[i])

                        for i in range(0, min(len(series_parts), len(code_n) - 1)):
                            series_parts[i] += ' ' + code_n[i]

                        book['series'] = ' - '.join(series_parts)
                        log.info("[245] Series: %s" % book['series'])
                        book['series'] = self.clean_series(log, book['series'], book['publisher_name'])

                        # build series index
                        if code_n:
                            book['series_index'] = code_n[-1]
                            log.info("[245] Series_Index: %s" % book['series_index'])

                    # subtitle 1: Field 245, Subfield b
                    # Append subtitle only if set in options
                    if self.cfg_append_subtitle_to_title == True:
                        if book['edition']:
                            book['edition'] = book['edition'].strip('[]')
                            book['title'] = book['title'] + " : " + book['edition']
                        if book['subtitle']:
                            book['title'] = book['title'] + " : " + book['subtitle']
                    else:
                        if self.cfg_append_subtitle_to_title == True:
                            try:
                                log.info("title_parts before adding code b: %s" % title_parts)
                                title_parts.append(
                                    field.xpath("./marc21:subfield[@code='b' and string-length(text())>0]",
                                                namespaces=ns)[0].text.strip())
                                log.info("title_parts after adding code b: %s" % title_parts)
                            except IndexError:
                                pass

                    log.info("title_parts=%s" % title_parts)
                    book['title'] = " : ".join(title_parts)
                    log.info("[245] Title: %s" % book['title'])
                    book['title'] = self.clean_title(log, book['title'])

                # Title_Sort
                if title_parts:
                    title_sort_parts = list(title_parts)

                    try:  # Python2
                        title_sort_regex = re.match('^(.*?)(' + unichr(152) + '.*' + unichr(156) + ')?(.*?)$', title_parts[0])
                    except:  # Python3
                        title_sort_regex = re.match('^(.*?)(' + chr(152) + '.*' + chr(156) + ')?(.*?)$', title_parts[0])
                    sortword = title_sort_regex.group(2)
                    if sortword:
                        title_sort_parts[0] = ''.join(filter(None, [title_sort_regex.group(1).strip(),
                                                                    title_sort_regex.group(3).strip(), ", " + sortword]))

                    book['title_sort'] = " : ".join(title_sort_parts)
                    log.info("[245] Title_Sort: %s" % book['title_sort'])


                ##### Field 100: "Main Entry-Personal Name"  #####
                ##### Field 700: "Added Entry-Personal Name" #####

                # Get Authors ####

                # primary authors
                primary_authors = []
                for i in record.xpath("./marc21:datafield[@tag='100']/marc21:subfield[@code='4' and "
                                      "text()='aut']/../marc21:subfield[@code='a' and "
                                      "string-length(text())>0]", namespaces=ns):
                    name = re.sub(" \[.*\]$", "", i.text.strip())
                    primary_authors.append(name)

                if primary_authors:
                    book['authors'].extend(primary_authors)
                    log.info("[100.a] Primary Authors: %s" % " & ".join(primary_authors))

                # secondary authors
                secondary_authors = []
                for i in record.xpath("./marc21:datafield[@tag='700']/marc21:subfield[@code='4' and "
                                      "text()='aut']/../marc21:subfield[@code='a' and "
                                      "string-length(text())>0]", namespaces=ns):
                    name = re.sub(" \[.*\]$", "", i.text.strip())
                    secondary_authors.append(name)

                # <subfield code="a">Meyer-Minnemann, Maralde</subfield>
                # <subfield code="e">Übersetzer</subfield>
                # <subfield code="4">trl</subfield>
                # <subfield code="2">gnd</subfield>
                for i in record.xpath("./marc21:datafield[@tag='700']/marc21:subfield[@code='4' and "
                                      "text()='trl']/../marc21:subfield[@code='a' and "
                                      "string-length(text())>0]", namespaces=ns):
                    if book['translator']:
                        book['translator'] = book['translator'] + ' / ' + re.sub(" \[.*\]$", "", i.text.strip())
                    else:
                        book['translator'] = re.sub(" \[.*\]$", "", i.text.strip())
                # <subfield code="a">MacLeod, Charlotte</subfield>
                # <subfield code="d">1922-2005</subfield>
                # <subfield code="e">Herausgeber</subfield>
                # <subfield code="4">edt</subfield>
                # <subfield code="2">gnd</subfield>
                for i in record.xpath("./marc21:datafield[@tag='700']/marc21:subfield[@code='4' and "
                                      "text()='edt']/../marc21:subfield[@code='a' and "
                                      "string-length(text())>0]", namespaces=ns):
                    if book['editor']:
                        book['editor'] = book['editor'] + ' / ' + re.sub(" \[.*\]$", "", i.text.strip())
                    else:
                        book['editor'] = re.sub(" \[.*\]$", "", i.text.strip())
                # <subfield code="a">Akademischer Verein Hütte</subfield>
                # <subfield code="e">Herausgebendes Organ</subfield>
                # <subfield code="4">isb</subfield>
                # <subfield code="2">gnd</subfield>
                for i in record.xpath("./marc21:datafield[@tag='700']/marc21:subfield[@code='4' and "
                                      "text()='isb']/../marc21:subfield[@code='a' and "
                                      "string-length(text())>0]", namespaces=ns):
                    if book['editor']:
                        book['editor'] = book['editor'] + ' / ' + re.sub(" \[.*\]$", "", i.text.strip())
                    else:
                        book['editor'] = re.sub(" \[.*\]$", "", i.text.strip())

                if secondary_authors:
                    book['authors'].extend(secondary_authors)
                    log.info("[700.a] Secondary Authors: %s" % " & ".join(secondary_authors))

                ##### Field 245 and code c or p #####
                if not book['authors']:
                    if code_c_authors:
                        book['authors'] = code_c_authors
                    if code_p_authors:
                        book['authors'] = code_p_authors
                book['authors'].extend(secondary_authors)

                # if no "real" author was found use all involved persons as authors
                if not book['authors']:
                    involved_persons = []
                    for i in record.xpath("./marc21:datafield[@tag='700']/marc21:subfield[@code='a' and "
                                          "string-length(text())>0]", namespaces=ns):
                        name = re.sub(" \[.*\]$", "", i.text.strip())
                        involved_persons.append(name)

                    if involved_persons:
                        book['authors'].extend(involved_persons)
                        log.info("[700.a] Involved Persons: %s" % " & ".join(involved_persons))


                ##### Field 249: "Weitere Titel etc. bei Zusammenstellungen"  #####

                #     <datafield tag="249" ind1=" " ind2=" ">
                #       <subfield code="a">Die Nacht von Tarent / H. D. Petersen</subfield>
                #     </datafield>
                for field in record.xpath("./marc21:datafield[@tag='249']", namespaces=ns):
                    log.info("[249] field=%s" % field.text.strip())

                    # Weiterer Titel bei Zusammenstellungen
                    code_a = []
                    for i in field.xpath("./marc21:subfield[@code='a' and string-length(text())>0]", namespaces=ns):
                        code_a.append(i.text.strip())
                        log.info("[249.a] code_a=%s" % code_a)
                    # Titelzusätze zur gesamten Zusammenstellung
                    code_b = []
                    for i in field.xpath("./marc21:subfield[@code='b' and string-length(text())>0]", namespaces=ns):
                        code_b.append(i.text.strip())
                        log.info("[249.b] code_b=%s" % code_b)
                    # Verantwortlichkeitsangabe zum weiteren Titel
                    code_v = []
                    for i in field.xpath("./marc21:subfield[@code='v' and string-length(text())>0]", namespaces=ns):
                        code_v.append(i.text.strip())
                        log.info("[249.v] code_v=%s" % code_v)

                    if code_a:
                        code_a_split = ''.join(code_a).split(" / ")
                        if len(code_a_split) > 1:
                            book['title']  = book['title'] + ' / ' + code_a_split[0]
                            book['authors'].append(code_a_split[1])


                ##### Field 730: "ADDED ENTRY – UNIFORM TITLE"  #####
                # a: Uniform title (Einheitstitel)
                for field in record.xpath("./marc21:datafield[@tag='730']", namespaces=ns):
                    for i in field.xpath("./marc21:subfield[@code='a' and string-length(text())>0]", namespaces=ns):
                        book['tags'].append(i)
                        if book['subseries']:
                            book['subseries'] = book['subseries'] + ' / ' + i
                        else:
                            book['subseries'] = i


                ##### Field 300: "PHYSICAL DESCRIPTION"  #####
                # a: Extent (Umfang)
                # b: Other physical details (A)ndere physische Details)
                # c: Dimensions (Maße)
                # e: Accompanying material (Begleitmaterial)
                for field in record.xpath("./marc21:datafield[@tag='300']", namespaces=ns):
                    code_a = []
                    for i in field.xpath("./marc21:subfield[@code='a' and string-length(text())>0]", namespaces=ns):
                        code_a.append(i.text.strip())
                        log.info("[300.a] code_a=%s" % code_a)
                    code_b = []
                    for i in field.xpath("./marc21:subfield[@code='b' and string-length(text())>0]", namespaces=ns):
                        code_b.append(i.text.strip())
                        log.info("[300.b] code_b=%s" % code_b)
                    code_c = []
                    for i in field.xpath("./marc21:subfield[@code='c' and string-length(text())>0]", namespaces=ns):
                        code_c.append(i.text.strip())
                        log.info("[300.c] code_c=%s" % code_c)
                    code_e = []
                    for i in field.xpath("./marc21:subfield[@code='e' and string-length(text())>0]", namespaces=ns):
                        code_e.append(i.text.strip())
                        log.info("[300.e] code_e=%s" % code_e)
                    if code_a:
                        book['extent'] = ''.join(code_a)
                    if code_b:
                        book['other_physical_details'] = ''.join(code_b)
                    if code_c:
                        book['dimensions'] = ''.join(code_c)
                    if code_e:
                        book['accompanying_material'] = ''.join(code_e)


                ##### Field 856: "Electronic Location and Access" #####
                # Get Comments, either from this book or from one of its other "Physical Forms"
                # Field contains an URL to an HTML file with the comments
                # Example: dnb-idn:1256023949
                for x in [record] + book['alternative_xmls']:
                    try:
                        url = x.xpath("./marc21:datafield[@tag='856']/marc21:subfield[@code='u' and string-length(text())>21]", namespaces=ns)[0].text.strip()
                        if url.startswith("http://deposit.dnb.de/") or url.startswith("https://deposit.dnb.de/"):
                            br = self.browser
                            log.info('[856.u] Trying to download Comments from: %s' % url)
                            try:
                                comments = br.open_novisit(url, timeout=30).read()

                                # Skip service outage information web page
                                if comments.decode('utf-8').find('Zugriff derzeit nicht möglich // Access currently unavailable') != -1:
                                    raise Exception("Access currently unavailable")

                                comments = re.sub(
                                    b'(\s|<br>|<p>|\n)*Angaben aus der Verlagsmeldung(\s|<br>|<p>|\n)*(<h3>.*?</h3>)*(\s|<br>|<p>|\n)*', b'', comments, flags=re.IGNORECASE)
                                book['comments'] = sanitize_comments_html(comments)
                                log.info('[856.u] Got Comments: %s' % book['comments'])
                                break
                            except Exception as e:
                                log.info("[856.u] Could not download Comments from %s: %s" % (url, e))
                    except IndexError:
                        pass


                ##### Field 16: "National Bibliographic Agency Control Number" #####
                # Get Identifier "IDN" (dnb-idn)
                try:
                    book['idn'] = record.xpath("./marc21:datafield[@tag='016']/marc21:subfield[@code='a' "
                                               "and string-length(text())>0]", namespaces=ns)[0].text.strip()
                    log.info("[016.a] Identifier IDN: %s" % book['idn'])
                except IndexError:
                    pass


                ##### Field 24: "Other Standard Identifier" #####
                # Get Identifier "URN"
                for i in record.xpath("./marc21:datafield[@tag='024']/marc21:subfield[@code='2' "
                                      "and text()='urn']/../marc21:subfield[@code='a' and string-length(text())>0]",
                                      namespaces=ns):
                    try:
                        urn = i.text.strip()
                        match = re.search("^urn:(.+)$", urn)
                        book['urn'] = match.group(1)
                        log.info("[024.a] Identifier URN: %s" % book['urn'])
                        break
                    except AttributeError:
                        pass

                # Caveat: "urn:" may be missing:
                #     <datafield tag="024" ind1="7" ind2=" ">
                #       <subfield code="a">1317675975</subfield>
                #       <subfield code="0">http://d-nb.info/gnd/1317675975</subfield>
                #       <subfield code="2">gnd</subfield>
                for i in record.xpath("./marc21:datafield[@tag='024']/marc21:subfield[@code='2' "
                                      "and text()='gnd']/../marc21:subfield[@code='a' and string-length(text())>0]",
                                      namespaces=ns):
                    try:
                        book['gnd'] = i.text.strip()
                        log.info("[024.a] Identifier GND: %s" % book['gnd'])
                        break
                    except AttributeError:
                        pass


                ##### Field 20: "International Standard Book Number" #####
                # Get Identifier "ISBN"
                for i in record.xpath("./marc21:datafield[@tag='020']/marc21:subfield[@code='a' and string-length(text())>0]", namespaces=ns):
                    try:
                        isbn_regex = "(?:ISBN(?:-1[03])?:? )?(?=[-0-9 ]{17}|[-0-9X ]{13}|[0-9X]{10})(?:97[89][- ]?)?[0-9]{1,5}[- ]?(?:[0-9]+[- ]?){2}[0-9X]"
                        match = re.search(isbn_regex, i.text.strip())
                        isbn = match.group()
                        book['isbn'] = isbn.replace('-', '')
                        log.info("[020.a] Identifier ISBN: %s" % book['isbn'])
                        break
                    except AttributeError:
                        pass
                for i in record.xpath("./marc21:datafield[@tag='020']/marc21:subfield[@code='c' "
                                      "and string-length(text())>0]", namespaces=ns):
                    # c: Terms of availability (Bezugsbedingungen) - usually the price
                    if i.text.strip():
                        book['terms_of_availability'] = i.text.strip()
                        break

                ##### Field 82: "Dewey Decimal Classification Number" #####
                # Get Identifier "Sachgruppen (DDC)" (ddc)
                for i in record.xpath("./marc21:datafield[@tag='082']/marc21:subfield[@code='a' "
                                      "and string-length(text())>0]", namespaces=ns):
                    # Caveat:
                    #     <datafield tag="082" ind1="7" ind2="4">
                    #       <subfield code="a">830</subfield>
                    #       <subfield code="a">B</subfield>
                    ddc = i.text.strip()
                    log.info("[082.a] ddc=%s" % ddc)
                    ddc_subject_area = self.ddc_to_text(ddc, log)
                    # log.info("ddc_subject_area=%s" % ddc_subject_area)
                    if ddc_subject_area:
                        book['ddc_subject_area'].append(ddc_subject_area)
                        ddc_subject_area.replace(';',',')
                        if ',' in ddc_subject_area:
                            book['tags'].extend([x.strip() for x in ddc_subject_area.split(',')])
                        else:
                            book['tags'].append(ddc_subject_area)
                    book['ddc'].append(ddc)
                if book['ddc']:
                    log.info("[082.a] Indentifiers DDC: %s" % ",".join(book['ddc']))


                # Field 490: "Series Statement"
                # Get Series and Series_Index
                # In theory book series are in field 830, but sometimes they are in 490, 246, 800 or nowhere
                # So let's look here if we could not extract series/series_index from 830 above properly
                # Subfields:
                # v: Series name and index
                # a: Series name
                for i in record.xpath("./marc21:datafield[@tag='490']/marc21:subfield[@code='v' "
                                      "and string-length(text())>0]/../marc21:subfield[@code='a' "
                                      "and string-length(text())>0]/..", namespaces=ns):

                    if book['series'] and book['series_index'] and book['series_index'] != "0":
                        break

                    series = None
                    series_index = None

                    # "v" is either "Nr. 220" or "This great Seriestitle : Nr. 220"
                    attr_v = i.xpath("./marc21:subfield[@code='v']", namespaces=ns)[0].text.strip()

                    # Assume we have "This great Seriestitle : Nr. 220"
                    # -> Split at " : ", the part without digits is the series, the digits in the other part are the series_index
                    parts = re.split(" : ", attr_v)
                    if len(parts) == 2:
                        if bool(re.search("\d", parts[0])) != bool(re.search("\d", parts[1])):
                            # figure out which part contains the index number
                            if bool(re.search("\d", parts[0])):
                                indexpart = parts[0]
                                textpart = parts[1]
                            else:
                                indexpart = parts[1]
                                textpart = parts[0]

                            match = re.search("(\d+(?:[\.,]\d+)?)", indexpart)
                            if match:
                                series_index = match.group(1)
                                series = textpart.strip()
                                log.info("[490.v] Series: %s" % series)
                                log.info("[490.v] Series_Index: %s" % series_index)

                    else:
                        # Assumption above was wrong. Try to extract at least the series_index
                        #       <subfield code="a">JACK RYAN</subfield>
                        #       <subfield code="v">25</subfield>
                        # "(\d+[,\.\d]+?)" gives as match 1 for the subfield v above: "25",
                        #       <subfield code="a">Abhandlungen</subfield>
                        #       <subfield code="v">Bd. 20, Abth. 1 = [1]</subfield>
                        # "(\d+[,\.\d]+?)" gives as match 1 for the subfield v above: "20,",
                        # => The regex matches integer an float values
                        match = re.search("(\d+(?:[\.,]\d+)?)", attr_v)
                        if match:
                            series_index = match.group(1)
                            log.info("[490.v] Series_Index: %s" % series_index)

                    # Use Series Name from attribute "a" if not already found in attribute "v"
                    if not series:
                        series = i.xpath("./marc21:subfield[@code='a']", namespaces=ns)[0].text.strip()
                        log.info("[490.a] Series: %s" % series)

                    if series:
                        series = self.clean_series(log, series, book['publisher_name'])

                        if series and series_index:
                            book['series'] = series
                            book['series_index'] = series_index


                ##### Field 246: "Varying Form of Title" #####
                # Series and Series_Index
                for i in record.xpath("./marc21:datafield[@tag='246']/marc21:subfield[@code='a' and string-length(text())>0]", namespaces=ns):

                    if book['series'] and book['series_index'] and book['series_index'] != "0":
                        break

                    match = re.search("^(.+?) ; (\d+[,\.\d+]?)$", i.text.strip())
                    if match:
                        series = match.group(1)
                        series_index = match.group(2)
                        log.info("[246.a] Series: %s" % series)
                        log.info("[246.a] Series_Index: %s" % series_index)

                        series = self.clean_series(log, match.group(1), book['publisher_name'])

                        if series and series_index:
                            book['series'] = series
                            book['series_index'] = series_index


                ##### Field 800: "Series Added Entry-Personal Name" #####
                # Series and Series_Index
                for i in record.xpath("./marc21:datafield[@tag='800']/marc21:subfield[@code='v' and string-length(text())>0]/../marc21:subfield[@code='t' and string-length(text())>0]/..", namespaces=ns):

                    if book['series'] and book['series_index'] and book['series_index'] != "0":
                        break

                    # Series Index
                    match = re.search("(\d+[,\.\d+]?)", i.xpath("./marc21:subfield[@code='v']", namespaces=ns)[0].text.strip())
                    if match:
                        series_index = match.group(1)
                        if series_index.endswith(','):
                            series_index = series_index[:-1] + '.'
                        log.info("[490.v] Series_Index: %s" % series_index)

                    # Series
                    series = i.xpath("./marc21:subfield[@code='t']", namespaces=ns)[0].text.strip()
                    log.info("[800.t] Series: %s" % series)

                    series = self.clean_series(log, series, book['publisher_name'])

                    if series and series_index:
                        book['series'] = series
                        book['series_index'] = series_index


                ##### Field 830: "Series Added Entry-Uniform Title" #####
                # Series and Series_Index
                for i in record.xpath("./marc21:datafield[@tag='830']/marc21:subfield[@code='v' and string-length(text())>0]/../marc21:subfield[@code='a' and string-length(text())>0]/..", namespaces=ns):

                    if book['series'] and book['series_index'] and book['series_index'] != "0":
                        break

                    # Series Index
                    #       <subfield code="a">Abhandlungen</subfield>
                    #       <subfield code="v">Bd. 20, Abth. 1 = [1]</subfield>
                    # "(\d+[,\.\d]+?)" gives as match 1 for the subfield v above: "20,",
                    match = re.search("(\d+(?:[\.,]\d+)?)", i.xpath("./marc21:subfield[@code='v']", namespaces=ns)[0].text.strip())
                    if match:
                        series_index = float(match.group(1))
                        log.info("[490.v] Series_Index: %s" % series_index)

                    # Series
                    series = i.xpath("./marc21:subfield[@code='a']", namespaces=ns)[0].text.strip()
                    log.info("[830.a] Series: %s" % series)

                    series = self.clean_series(log, series, book['publisher_name'])

                    if series and series_index:
                        book['series'] = series
                        book['series_index'] = series_index

                # Adjust title, if series and index in title
                if book['series']:
                    log.info("book['series']={0}, book['series_index=']={1}".format(book['series'], book['series_index']))
                    # Re-format series_index (book['series_index'] is of type string)
                    # to remove leading zeros before searching.
                    if book['series_index']:
                        # Check for safe series index
                        # <datafield tag="490" ind1="1" ind2=" ">
                        # <subfield code="a">[Diogenes-Taschenbücher] Diogenes-Taschenbuch</subfield>
                        # <subfield code="v">75,17</subfield>
                        book['series_index'] = book['series_index'].replace(',', '.')
                        # [490.a] Series: K[unst] i[m] D[ruck]
                        book['series_index'] = book['series_index'].replace('[', '').replace(']', '')
                        # match = re.search(book['series'] + ".*" + str(int(book['series_index'])) + ".*:(.*)", book['title'])
                        match = re.search(book['series'] + ".*" + str(float(book['series_index'])) + ".*:(.*)",
                                          book['title'])
                    else:
                        # match = re.search(book['series'] + ".*:(.*)", book['title'])
                        match = re.search(book['series'] + ".*:(.*)", book['title'])
                    if match:
                        book['title'] = match.group(1).strip()
                        log.info("book['title'] after stripping series and index=%s" % book['title'])
                    else:
                        # title, perhaps with subtitle
                        title_split = book['title'].split(": ")
                        book['title'] = title_split[0]
                        if len(title_split) > 1:
                            book['title'] = title_split[0]  # title without subtitle
                            log.info("book['title'] after stripping subtitle=%s" % book['title'])
                            book['subtitle'] = title_split[1]


                ##### Field 689 #####
                # Get GND Subjects
                for i in record.xpath("./marc21:datafield[@tag='689']/marc21:subfield[@code='a' and string-length(text())>0]", namespaces=ns):
                    book['subjects_gnd'].append(i.text.strip())

                for f in range(600, 656):
                    for i in record.xpath("./marc21:datafield[@tag='" + str(f) + "']/marc21:subfield[@code='a' and "
                                                                                 "text()='gnd']/../marc21:subfield[@code='a' and "
                                                                                 "string-length(text())>0]", namespaces=ns):
                        # skip entries starting with "(":
                        if i.text.startswith("("):
                            continue
                        # <datafield tag="653" ind1=" " ind2=" ">
                        #   <subfield code="a">Emil; Klassiker; Krimi; Kästner</subfield>
                        if ';' in i.text:
                            subjects = [x.strip() for x in i.text.split(';')]
                            book['subjects_gnd'].extend(subjects)
                        else:
                            book['subjects_gnd'].append(i.text)

                if book['subjects_gnd']:
                    log.info("[689.a] GND Subjects: %s" % " / ".join(book['subjects_gnd']))


                ##### Fields 600-655 #####
                # Get non-GND Subjects
                # Field 648: Chronological term (Zeitschlagwort)
                for f in range(600, 656):
                    for i in record.xpath("./marc21:datafield[@tag='" + str(f) + "']/marc21:subfield[@code='a' and "
                                                                                 "string-length(text())>0]", namespaces=ns):
                        # skip entries starting with "(":
                        if i.text.startswith("("):  # "BISAC Subject Heading)FIC000000"
                            continue
                        # skip one-character subjects:
                        if len(i.text) < 2:
                            continue

                        # Preserve entries with commas as one term: "<subfield code="a">Dame, König, As, Spion</subfield"
                        # log.info("i.text=%s" % i.text)
                        # book['subjects_non_gnd'].extend(re.split(',|;', self.remove_sorting_characters(i.text)))
                        book['subjects_non_gnd'].append(unicodedata_normalize("NFKC", i.text))

                if book['subjects_non_gnd']:
                    log.info("[600.a-655.a] Non-GND Subjects: %s" % " / ".join(book['subjects_non_gnd']))


                ##### Field 250: "Edition Statement" #####
                # Get Edition
                try:
                    book['edition'] = record.xpath("./marc21:datafield[@tag='250']/marc21:subfield[@code='a' "
                                                   "and string-length(text())>0]", namespaces=ns)[0].text.strip().strip('[])')
                    log.info("[250.a] Edition: %s" % book['edition'])
                except IndexError:
                    pass


                ##### Field 41: "Language Code" #####
                # Get Languages (unfortunately in ISO-639-2/B ("ger" for German), while Calibre uses ISO-639-1 ("de"))
                for i in record.xpath("./marc21:datafield[@tag='041']/marc21:subfield[@code='a' and string-length(text())>0]", namespaces=ns):
                    book['languages'].append(
                        lang_as_iso639_1(
                            self.iso639_2b_as_iso639_3(i.text.strip())
                        )
                    )

                try:
                    if book['languages']:
                        log.info("[041.a] Languages: %s" % ",".join(book['languages']))
                except TypeError:
                    pass


                ##### SERIES GUESSER #####
                # DNB's metadata often lacks proper series/series_index data
                ##### If configured: Try to retrieve Series, Series Index and "real" Title from the fetched Title #####
                if self.cfg_guess_series is True and not book['series'] or not book['series_index'] or book['series_index'] == "0":
                    guessed_series = None
                    guessed_series_index = None
                    guessed_title = None

                    parts = re.split(
                        "[:]", self.remove_sorting_characters(book['title']))

                    if len(parts) == 2:
                        # make sure only one part of the two parts contains digits
                        if bool(re.search("\d", parts[0])) != bool(re.search("\d", parts[1])):

                            # call the part with the digits "indexpart" as it contains the series_index, the one without digits "textpart"
                            if bool(re.search("\d", parts[0])):
                                indexpart = parts[0]
                                textpart = parts[1]
                            else:
                                indexpart = parts[1]
                                textpart = parts[0]

                            # remove odd characters from start and end of the textpart
                            match = re.match(
                                "^[\s\-–—:]*(.+?)[\s\-–—:]*$", textpart)
                            if match:
                                textpart = match.group(1)

                            # if indexparts looks like "Name of the series - Episode 2": extract series and series_index
                            match = re.match(
                                "^\s*(\S\D*?[a-zA-Z]\D*?)\W[\(\/\.,\s\-–—:]*(?:#|Reihe|Nr\.|Heft|Volume|Vol\.?|Episode|Bd\.|Sammelband|[B|b]and|Part|Kapitel|[Tt]eil|Folge)[,\-–—:\s#\(]*(\d+[\.,]?\d*)[\)\s\-–—:]*$", indexpart)
                            if match:
                                guessed_series_index = match.group(2)
                                guessed_series = match.group(1)

                                # sometimes books with multiple volumes are detected as series without series name -> Add the volume to the title if no series was found
                                if not guessed_series:
                                    guessed_series = textpart
                                    guessed_title = textpart + " : Band " + guessed_series_index
                                else:
                                    guessed_title = textpart

                                log.info("[Series Guesser] 2P1 matched: Title: %s, Series: %s[%s]" % (guessed_title, guessed_series, guessed_series_index))

                            else:
                                # if indexpart looks like "Episode 2 Name of the series": extract series and series_index
                                match = re.match(
                                    "^\s*(?:#|Reihe|Nr\.|Heft|Volume|Vol\.?Episode|Bd\.|Sammelband|[B|b]and|Part|Kapitel|[Tt]eil|Folge)[,\-–—:\s#\(]*(\d+[\.,]?\d*)[\)\s\-–—:]*(\S\D*?[a-zA-Z]\D*?)[\/\.,\-–—\s]*$", indexpart)
                                if match:
                                    guessed_series_index = match.group(1)
                                    guessed_series = match.group(2)

                                    # sometimes books with multiple volumes are detected as series without series name -> Add the volume to the title if no series was found
                                    if not guessed_series:
                                        guessed_series = textpart
                                        guessed_title = textpart + " : Band " + guessed_series_index
                                    else:
                                        guessed_title = textpart

                                    log.info("[Series Guesser] 2P2 matched: Title: %s, Series: %s[%s]" % (guessed_title, guessed_series, guessed_series_index))

                                else:
                                    # if indexpart looks like "Band 2": extract series_index
                                    match = re.match(
                                        "^[\s\(]*(?:#|Reihe|Nr\.|Heft|Volume|Vol\.?Episode|Bd\.|Sammelband|[B|b]and|Part|Kapitel|[Tt]eil|Folge)[,\-–—:\s#\(]*(\d+[\.,]?\d*)[\)\s\-–—:]*[\/\.,\-–—\s]*$", indexpart)
                                    if match:
                                        guessed_series_index = match.group(1)

                                        # if textpart looks like "Name of the Series - Book Title": extract series and title
                                        match = re.match(
                                            "^\s*(\w+.+?)\s?[\.;\-–:]+\s(\w+.+)\s*$", textpart)
                                        if match:
                                            guessed_series = match.group(1)
                                            guessed_title = match.group(2)

                                            log.info("[Series Guesser] 2P3 matched: Title: %s, Series: %s[%s]" % (guessed_title, guessed_series, guessed_series_index))

                    elif len(parts) == 1:
                        # if title looks like: "Name of the series - Title (Episode 2)"
                        match = re.match(
                            "^\s*(\S.+?) \- (\S.+?) [\(\/\.,\s\-–:](?:#|Reihe|Nr\.|Heft|Volume|Vol\.?Episode|Bd\.|Sammelband|[B|b]and|Part|Kapitel|[Tt]eil|Folge)[,\-–—:\s#\(]*(\d+[\.,]?\d*)[\)\s\-–—:]*$", parts[0])
                        if match:
                            guessed_series_index = match.group(3)
                            guessed_series = match.group(1)
                            guessed_title = match.group(2)

                            log.info("[Series Guesser] 1P1 matched: Title: %s, Series: %s[%s]" % (guessed_title, guessed_series, guessed_series_index))

                        else:
                            # if title looks like "Name of the series - Episode 2"
                            match = re.match(
                                "^\s*(\S.+?)[\(\/\.,\s\-–—:]*(?:#|Reihe|Nr\.|Heft|Volume|Vol\.?Episode|Bd\.|Sammelband|[B|b]and|Part|Kapitel|[Tt]eil|Folge)[,\-–:\s#\(]*(\d+[\.,]?\d*)[\)\s\-–—:]*$", parts[0])
                            if match:
                                guessed_series_index = match.group(2)
                                guessed_series = match.group(1)
                                guessed_title = guessed_series + " : Band " + guessed_series_index

                                log.info("[Series Guesser] 1P2 matched: Title: %s, Series: %s[%s]" % (guessed_title, guessed_series, guessed_series_index))

                    # store results
                    if guessed_series and guessed_series_index and guessed_title:
                        book['title'] = self.clean_title(log, guessed_title)
                        book['series'] = guessed_series
                        book['series_index'] = guessed_series_index


                ##### Filter exact searches #####
                if idn and book['idn'] and idn != book['idn']:
                    log.info("Extracted IDN does not match book's IDN, skipping record")
                    continue

                ##### Figure out working URL to cover #####
                # Cover URL is basically fixed and takes ISBN as an argument
                # So get all ISBNs we have for this book...
                cover_isbns = [ book['isbn'] ]
                # loop through all alternative "physical forms"
                for altxml in book['alternative_xmls']:
                    for identifier in altxml.xpath("./marc21:datafield[@tag='020']/marc21:subfield[@code='a' and string-length(text())>0]", namespaces=ns):
                        try:
                            isbn_regex = "(?:ISBN(?:-1[03])?:? )?(?=[-0-9 ]{17}|[-0-9X ]{13}|[0-9X]{10})(?:97[89][- ]?)?[0-9]{1,5}[- ]?(?:[0-9]+[- ]?){2}[0-9X]"
                            match = re.search(isbn_regex, identifier.text.strip())
                            isbn = match.group()
                            isbn = isbn.replace('-', '')
                            log.info("[020.a ALTERNATE] Identifier ISBN: %s" % isbn)
                            cover_isbns.append(isbn)
                            self.cache_isbn_to_identifier(isbn, book['idn'])
                            break
                        except AttributeError:
                            pass

                # ...and check for each ISBN if the server has a cover
                for i in cover_isbns:
                    url = self.COVERURL % i
                    request = Request(url)
                    request.get_method = lambda : 'HEAD'
                    try:
                        urlopen(request)
                        self.cache_identifier_to_cover_url(book['idn'], url)
                        break
                    except HTTPError:
                        continue


                ##### Put it all together #####

                # Remove duplicate authors
                # log.info("book['authors']=%s" % book['authors'])
                # First, swap names like 'Doe, John':
                book['authors'] = [' '.join(author.split(',')[::-1]).strip() for author in book['authors']]
                book['authors'] = list(dict.fromkeys(book['authors']))
                if book['translator']:
                    # Remove double translators: Ebba-Margareta von Freymann / Freymann, Ebba-Margareta von / Freymann, Thelma von
                    translators = book['translator'].split(' / ')
                    # Sswap names like 'Doe, John':
                    translators = [' '.join(translator.split(',')[::-1]).strip() for translator in translators]
                    log.info("translators=%s" % translators)
                    translators = list(dict.fromkeys(translators))
                    book['translator'] = ' / '.join(translators)

                if book['comments']:
                    book['comments'] = book['comments'] + '<p>'  # Because of 'html_sanitize()' above
                    html_comments = True
                    line_break = '<br />'
                else:
                    book['comments'] = ''
                    html_comments = False
                    line_break = '\n'

                # Put other data in comment field
                if self.cfg_fetch_all == True:
                    if self.cfg_prefer_results_with_isbn == False and book['isbn']:
                        book['comments'] = book['comments'] + line_break + _('ISBN:\t') + book['isbn']
                    if book['subtitle']:
                        book['comments'] = book['comments'] + line_break +_('Subtitle:\t') + book['subtitle']
                    if book['subseries']:
                        book['comments'] = book['comments'] + line_break + _('Subseries:\t') + book['subseries']
                        if book['subseries_index']:
                            book['comments'] = book['comments'] + ' [' + book['subseries_index'] + ']'
                    if book['editor']:
                        # Avoid such cases (from different fields): "Herausgeber: Wolfram von Soden / Soden, Wolfram von"
                        book_editors = book['editor'].split(' / ')
                        if len(book_editors) > 1:
                            book_editors[1] = book_editors[1].replace('\x98', ' ').replace('\x9c', ' ').replace('  ', ' ').strip()
                            book_editors[1] = ' '.join(book_editors[1].split(', ')[::-1]).strip()
                            if book_editors[0] == book_editors[1]:
                                book['editor'] = book_editors[0]
                        book['comments'] = book['comments'] + line_break + _('Editor:\t') + book['editor']
                    if book['foreword']:
                        book['comments'] = book['comments'] + line_break + _('Foreword by:\t') + book['foreword']
                    if book['artist']:
                        book['comments'] = book['comments'] + line_break + _('Artist:\t') + book['artist']
                    if book['original_language']:
                        book['comments'] = book['comments'] + line_break + _('Original language:\t') + book['original_language']
                    if book['original_pubdate']:
                        book['comments'] = book['comments'] + line_break + _('Original pubdate:\t') + book['original_pubdate']
                    if book['original_version_note']:
                        book['comments'] = book['comments'] + line_break + _('Original version note:\t') + book['original_version_note']
                    if book['translator']:
                        book['comments'] = book['comments'] + line_break + _('Translator:\t') + book['translator']
                    if book['edition']:
                        book['comments'] = book['comments'] + line_break + _('Edition:\t') + book['edition']
                    if book['mediatype']:
                        book['comments'] = book['comments'] + line_break + _('Media type:\t') + book['mediatype']
                    if book['extent']:
                        book['comments'] = book['comments'] + line_break + _('Extent:\t') + book['extent']
                    if book['other_physical_details']:
                        book['comments'] = book['comments'] + line_break + _('Other physical details:\t') + book['other_physical_details']
                    if book['dimensions']:
                        book['comments'] = book['comments'] + line_break + _('Dimensions:\t') + book['dimensions']
                    if book['accompanying_material']:
                        book['comments'] = book['comments'] + line_break + _('Accompanying material:\t') + book['accompanying_material']
                    if book['terms_of_availability']:
                        book['comments'] = (book['comments'] + line_break + _('Terms of availability:\t') + book['terms_of_availability'])
                    if book['ddc_subject_area']:
                        book['comments'] = (book['comments'] + line_break + _('DDC subject area:\t') + ', '.join(book['ddc_subject_area']))
                    if book['subjects_gnd']:
                        book['comments'] = book['comments'] + line_break + _('GND subjects:\t') + ' / '.join(book['subjects_gnd'])
                    if book['subjects_non_gnd']:
                        book['comments'] = book['comments'] + line_break + _('Non-GND subjects:\t') + ' / '.join(book['subjects_non_gnd'])
                    if marc21_fields and self.cfg_show_marc21_field_numbers == True:
                        book['comments'] = book['comments'] + line_break + '---' + line_break + _('MARC21 fields:\t') + ', '.join(marc21_fields)
                    if html_comments:
                        book['comments'] = book['comments'] + '</p>'

                # Indicate path to source
                if book['idn']:
                    book['record_uri'] = 'https://d-nb.info/' + book['idn']
                elif book['gnd']:
                    book['record_uri'] = 'https://d-nb.info/gnd/' + book['gnd']
                if html_comments:
                    book['comments'] = book['comments'] + '<p>' + _('Source:\t') + book['record_uri'] + '</p>'
                else:
                    book['comments'] = book['comments'] + line_break + _('Source:\t') + book[
                        'record_uri']
                log.info("book= %s" % book)

                if self.cfg_append_edition_to_title == True and book['edition']:
                    book['title'] = book['title'] + " : " + book['edition']

                # Avoiding Calibre's merge behavior for identical titles and authors.
                # (This behavior suppresses other editions of a title.)
                if len(results) > 1:
                    book['title'] = book['title'] + " (" + book['idn'] + ")"

                if book['authors']:
                    authors = list(map(lambda i: self.remove_sorting_characters(i), book['authors']))
                else:
                    authors = []

                if authors:
                    mi = Metadata(
                        self.remove_sorting_characters(book['title']),
                        list(map(lambda i: re.sub("^(.+), (.+)$", r"\2 \1", i), authors))
                    )
                else:
                    mi = Metadata(
                        self.remove_sorting_characters(book['title']),
                        authors
                    )

                # mi.author_sort = " & ".join(authors)  # Let Calibre itself doing the sort

                mi.title_sort = self.remove_sorting_characters(book['title_sort'])

                if book['languages']:
                    mi.languages = book['languages']
                    mi.language = book['languages'][0]

                mi.pubdate = book['pubdate']
                mi.publisher = " : ".join(filter(
                    None, [book['publisher_location'], self.remove_sorting_characters(book['publisher_name'])]))

                if book['series']:
                    mi.series = self.remove_sorting_characters(book['series'].replace(',', '.'))
                    mi.series_index = book['series_index'] or "0"

                mi.comments = book['comments']

                mi.has_cover = self.cached_identifier_to_cover_url(book['idn']) is not None

                # mi.isbn = book['isbn']  # see https://www.mobileread.com/forums/showthread.php?t=336308
                log.info("self.prefer_results_with_isbn=%s" % self.prefer_results_with_isbn)
                if self.prefer_results_with_isbn == True:
                    mi.isbn = book['isbn']
                    mi.set_identifier('isbn', book['isbn'])
                mi.set_identifier('urn', book['urn'])
                mi.set_identifier('dnb-idn', book['idn'])
                mi.set_identifier('ddc', ",".join(book['ddc']))

                # cfg_subjects:
                # 0: use only subjects_gnd
                if self.cfg_fetch_subjects == 0:
                    mi.tags = self.uniq(book['subjects_gnd'])
                # 1: use only subjects_gnd if found, else subjects_non_gnd
                elif self.cfg_fetch_subjects == 1:
                    if book['subjects_gnd']:
                        mi.tags = self.uniq(book['subjects_gnd'])
                    else:
                        mi.tags = self.uniq(book['subjects_non_gnd'])
                # 2: subjects_gnd and subjects_non_gnd
                elif self.cfg_fetch_subjects == 2:
                    mi.tags = self.uniq(book['subjects_gnd'] + book['subjects_non_gnd'])
                # 3: use only subjects_non_gnd if found, else subjects_gnd
                elif self.cfg_fetch_subjects == 3:
                    if book['subjects_non_gnd']:
                        mi.tags = self.uniq(book['subjects_non_gnd'])
                    else:
                        mi.tags = self.uniq(book['subjects_gnd'])
                # 4: use only subjects_non_gnd
                elif self.cfg_fetch_subjects == 4:
                    mi.tags = self.uniq(book['subjects_non_gnd'])
                # 5: use no subjects at all
                elif self.cfg_fetch_subjects == 5:
                    mi.tags = []
                if book['tags']:
                    mi.tags.extend(book['tags'])

                # put current result's metdata into result queue
                log.info("Final formatted result: \n%s\n-----" % mi)
                result_queue.put(mi)
                query_success = True

            # Stop on first successful query, if option is set (default)
            if query_success and self.cfg_stop_after_first_hit == True:
                break


    # Download Cover image - gets called directly from Calibre
    def download_cover(self, log, result_queue, abort, title=None, authors=None, identifiers={}, timeout=30, get_best_cover=False):
        cached_url = self.get_cached_cover_url(identifiers)
        if cached_url is None:
            log.info('No cached cover found, running identify')
            rq = Queue()
            self.identify(log, rq, abort, title=title,
                          authors=authors, identifiers=identifiers)
            if abort.is_set():
                return

            results = []
            while True:
                try:
                    results.append(rq.get_nowait())
                except Empty:
                    break
            results.sort(key=self.identify_results_keygen(
                title=title, authors=authors, identifiers=identifiers))
            for mi in results:
                cached_url = self.get_cached_cover_url(mi.identifiers)
                if cached_url is not None:
                    break

        if not cached_url:
            log.info('No cover found')
            return None

        if abort.is_set():
            return

        br = self.browser
        log('Downloading cover from:', cached_url)
        try:
            cdata = br.open_novisit(cached_url, timeout=timeout).read()
            result_queue.put((self, cdata))
        except Exception as e:
            log.info("Could not download Cover, ERROR %s" % e)


########################################
    def create_query_variations(self, log, idn=None, isbn=None, authors=[], title=None):
        queries = []

        if idn:
            # if IDN is given only search for the IDN and skip all the other stuff
            queries.append('num=' + idn)
        elif isbn:
            # if ISBN is given only search for the ISBN and skip all the other stuff
            queries.append('num=' + isbn)
        else:

            # create some variations of given authors
            authors_v = []
            if len(authors) > 0:
                # simply use all authors
                for a in authors:
                    authors_v.append(authors)

                # use all authors, one by one
                if len(authors) > 1:
                    for a in authors:
                        authors_v.append([a])

            # create some variations of given title
            title_v = []
            if title:

                # simply use given title
                title_v.append([ title ])

                # remove some punctation characters
                title_v.append([ ' '.join(self.get_title_tokens(
                    title, strip_joiners=False, strip_subtitle=False))] )

                # remove some punctation characters, joiners ("and", "und", "&", ...), leading zeros,  and single non-word characters
                title_v.append([x.lstrip('0') for x in self.strip_german_joiners(self.get_title_tokens(
                    title, strip_joiners=True, strip_subtitle=False)) if (len(x)>1 or x.isnumeric())])

                # remove subtitle (everything after " : ")
                title_v.append([ ' '.join(self.get_title_tokens(
                    title, strip_joiners=False, strip_subtitle=True))] )

                # remove subtitle (everything after " : "), joiners ("and", "und", "&", ...), leading zeros, and single non-word characters
                title_v.append([x.lstrip('0') for x in self.strip_german_joiners(self.get_title_tokens(
                    title, strip_joiners=True, strip_subtitle=True)) if (len(x)>1 or x.isnumeric())])

            ## create queries
            # Titelsuche
            # tit
            # Sämtliche Titelfelder werden mit tit durchsucht, er liegt hinter dem Suchschlüssel
            # Titel in der Erweiterten Suche. Titelfelder sind zum Beispiel Haupttitel, Titelzusätze, Serientitel und
            # Titel von Werken (Normdaten). Beispiel: tit=Joseph Brüder
            # tst
            # Es wird der vollständige Titel gesucht, das heißt der Titel muss exakt bekannt sein und der führende
            # Artikel eingegeben werden. Beispiel: tst="Der Atlas für den Weltreisenden". Rechtstrunkierung ist möglich,
            # Beispiel: tst="Der Vogelhändler*". Der Index tst liegt hinter dem Suchschlüssel Vollständiger Titel

            # title and author given:
            if authors_v and title_v:

                # try with title and all authors
                queries.append('tst="%s" AND %s' % (
                    title,
                    " AND ".join(list(map(lambda x: 'per="%s"' % x, authors))),
                ))

                # try with cartiesian product of all authors and title variations created above
                for a in authors_v:
                    for t in title_v:
                        queries.append(
                            " AND ".join(
                                list(map(lambda x: 'tit="%s"' % x.lstrip('0'), t)) +
                                list(map(lambda x: 'per="%s"' % x, a))
                         ))

                # try with first author as title and title (without subtitle) as author
                queries.append('per="%s" AND tit="%s"' % (
                    ' '.join(x.lstrip('0') for x in self.get_title_tokens(title, strip_joiners=True, strip_subtitle=True)),
                    ' '.join(self.get_author_tokens(authors, only_first_author=True))
                ))

                # try with first author and title (without subtitle) in any index
                queries.append(
                    ' AND '.join(list(map(lambda x: '"%s"' % x, [
                        " ".join(x.lstrip('0') for x in self.get_title_tokens(title, strip_joiners=True, strip_subtitle=True)),
                        " ".join(self.get_author_tokens(authors, only_first_author=True))
                    ])))
                )

                # try with first author and splitted title words (without subtitle) in any index
                queries.append(
                    ' AND '.join(list(map(lambda x: '"%s"' % x.lstrip('0'),
                                          list(x.lstrip('0') for x in self.strip_german_joiners(self.get_title_tokens(title, strip_joiners=True, strip_subtitle=True)))
                                          + list(self.get_author_tokens(authors, only_first_author=True))
                                          )))
                )

            # authors given but no title
            elif authors_v and not title_v:
                # try with all authors as authors
                for a in authors_v:
                    queries.append(" AND ".join(list(map(lambda x: 'per="%s"' % x, a))))

                # try with first author as author
                queries.append('per="' + ' '.join(self.get_author_tokens(authors, only_first_author=True)) + '"')

                # try with first author as title
                queries.append('tit="' + ' '.join(x.lstrip('0') for x in self.get_author_tokens(authors, only_first_author=True)) + '"')

            # title given but no author
            elif not authors_v and title_v:
                # try with title as title
                for t in title_v:
                    queries.append(
                        " AND ".join(list(map(lambda x: 'tit="%s"' % x.lstrip('0'), t)))
                    )
                # try with title as author
                queries.append('per="' + ' '.join(self.get_title_tokens(title, strip_joiners=True, strip_subtitle=True)) + '"')

                # try with title (without subtitle) in any index
                queries.append(
                    ' AND '.join(list(map(lambda x: '"%s"' % x, [
                        " ".join(x.lstrip('0') for x in self.get_title_tokens(title, strip_joiners=True, strip_subtitle=True))
                    ])))
                )

        # remove duplicate queries (while keeping the order)
        uniqueQueries = []
        for i in queries:
            if i not in uniqueQueries:
                uniqueQueries.append(i)

        if isbn:
            uniqueQueries = [ i + ' AND num=' + isbn for i in uniqueQueries ]

        # do not search in films, music, microfiches or audiobooks
        uniqueQueries = [ i + ' NOT (mat=film OR mat=music OR mat=microfiches OR cod=tt)' for i in uniqueQueries ]

        return uniqueQueries


    # remove sorting word markers
    def remove_sorting_characters(self, text):
        if text:
            return ''.join([c for c in text if ord(c) != 152 and ord(c) != 156])
        else:
            return None


    # clean up title
    def clean_title(self, log, title):
        log.info("clean_title(), title=%s" % title)
        if title:
            # remove name of translator from title
            match = re.search(
                '^(.+) [/:] [Aa]us dem .+? von(\s\w+)+$', self.remove_sorting_characters(title))
            if match:
                title = match.group(1)
                book['translator'] = title = match.group(2)
                log.info("[Title Cleaning] Removed translator, title is now: %s" % title)
                return title
            # For clarity reason, no use of non-capturing groups for alternatives
            # <subfield code="c">von Gérard de Villiers. [Übers.: Jürgen Hofmann]</subfield>
            match = re.search(
                '^(.+) [/:.] \[Übers\.:(\s\w+)+\]$', self.remove_sorting_characters(title))
            if match:
                title = match.group(1)
                book['translator'] = title = match.group(2)
                log.info("[Title Cleaning] Removed translator, title is now: %s" % title)
                return title
            return title


    # clean up series
    def clean_series(self, log, series, publisher_name):
        if series:
            # series must at least contain a single character or digit
            match = re.search('[\w\d]', series)
            if not match:
                return None

            # remove sorting word markers
            series = ''.join(
                [c for c in series if ord(c) != 152 and ord(c) != 156])

            # do not accept publisher name as series
            if publisher_name:
                if publisher_name == series:
                    log.info("[Series Cleaning] Series %s is equal to publisher, ignoring" % series)
                    return None

                # Leads to "bad character" error, if regex meta character in pattern ("Diogenes-Taschenbuch")
                regex_meta_chars = ['[', ']', '([)', ')', '-', '.', '*', '?']
                for regex_meta_char in regex_meta_chars:
                    series.replace(regex_meta_char, '\\' + regex_meta_char)
                log.info("regex_safe series=%s" % series)
                return series

                # # [490.a] Series: [Diogenes-Taschenbücher] Diogenes-Taschenbuch
                # match = re.search(
                #     '\[(.*)\]', series)
                # if match:
                #     series = match.group(1)
                #     return series

                # Optional:
                # Skip series info if it starts with the first word of the publisher's name (which must be at least 4 characters long)
                # But only, if publishers name is not qualified (publisher: DuMont, series: DuMonts Bibliothek...). So
                # let the pattern ending with space.
                match = re.search(
                    '^(\w\w\w\w+) ', self.remove_sorting_characters(publisher_name))
                if match:  # ToDo: and prefer series not start with publisher name -- there may be reasons to add such series names
                    pubcompany = match.group(1)
                    if re.search('^\W*' + pubcompany, series, flags=re.IGNORECASE):
                        if self.cfg_skip_series_starting_with_publishers_name == True:
                            log.info("[Series Cleaning] Series %s starts with publisher, ignoring" % series)
                            return None

            # do not accept some other unwanted series names
            # TODO: Make user configurable
            for i in self.cfg_unwanted_series_names:
                # TODO: Has issues with Umlauts in regex (or series string?)
                # Perhaps we should do it with unicodedata_normalize("NFKC", string)
                if re.search(unicodedata_normalize("NFKC", i), series, flags=re.IGNORECASE):
                    log.info("[Series Cleaning] Series %s contains unwanted string %s, ignoring" % (series, i))
                    return None
        return series


    # remove duplicates from list
    def uniq(self, listWithDuplicates):
        uniqueList = []
        if len(listWithDuplicates) > 0:
            for i in listWithDuplicates:
                if i not in uniqueList:
                    uniqueList.append(i)
        return uniqueList

        # Other possible approach:
        # uniqueList = list(dict.fromkeys(listWithDuplicates))


    def execute_query(self, log, query, timeout=30):
        # SRU does not work with "+" or "?" characters in query, so we simply remove them
        query =  re.sub('[\+\?]', '', query)

        log.info('Query String: %s' % query)

        queryUrl = self.QUERYURL % (self.MAXIMUMRECORDS, quote(query.encode('utf-8')))
        log.info('Query URL: %s' % queryUrl)

        xmlData = None
        try:
            data = self.browser.open_novisit(queryUrl, timeout=timeout).read()

            # "data" is of type "bytes", decode it to an utf-8 string, normalize the UTF-8 encoding (from decomposed to composed), and convert it back to bytes
            data = normalize(data.decode('utf-8')).encode('utf-8')
            #log.info('Got some data : %s' % data)

            xmlData = etree.XML(data)
            #log.info(etree.tostring(xmlData,pretty_print=True))

            numOfRecords = xmlData.xpath("./zs:numberOfRecords", namespaces={"zs": "http://www.loc.gov/zing/srw/"})[0].text.strip()
            log.info('Got records: %s' % numOfRecords)

            if int(numOfRecords) == 0:
                return None

            return xmlData.xpath("./zs:records/zs:record/zs:recordData/marc21:record", namespaces={'marc21': 'http://www.loc.gov/MARC21/slim', "zs": "http://www.loc.gov/zing/srw/"})
        except:
            try:
                diag = ": ".join([
                    xmlData.find('diagnostics/diag:diagnostic/diag:details', namespaces={
                        None: 'http://www.loc.gov/zing/srw/', 'diag': 'http://www.loc.gov/zing/srw/diagnostic/'}).text,
                    xmlData.find('diagnostics/diag:diagnostic/diag:message', namespaces={
                        None: 'http://www.loc.gov/zing/srw/', 'diag': 'http://www.loc.gov/zing/srw/diagnostic/'}).text
                ])
                log.error('ERROR: %s' % diag)
                return None
            except:
                log.error('ERROR: Got invalid response:')
                log.error(data)
                return None


    # Build Cover URL
    def get_cached_cover_url(self, identifiers):
        url = None
        idn = identifiers.get('dnb-idn', None)
        if idn is None:
            isbn = identifiers.get('isbn', None)
            if isbn is not None:
                idn = self.cached_isbn_to_identifier(isbn)
        if idn is not None:
            url = self.cached_identifier_to_cover_url(idn)
        return url


    # Convert ISO 639-2/B to ISO 639-3
    def iso639_2b_as_iso639_3(self, lang):
        # Most codes in ISO 639-2/B are the same as in ISO 639-3. This are the exceptions:
        mapping = {
            'alb': 'sqi',
            'arm': 'hye',
            'baq': 'eus',
            'bur': 'mya',
            'chi': 'zho',
            'cze': 'ces',
            'dut': 'nld',
            'fre': 'fra',
            'geo': 'kat',
            'ger': 'deu',
            'gre': 'ell',
            'ice': 'isl',
            'mac': 'mkd',
            'may': 'msa',
            'mao': 'mri',
            'per': 'fas',
            'rum': 'ron',
            'slo': 'slk',
            'tib': 'bod',
            'wel': 'cym',
        }
        try:
            return mapping[lang.lower()]
        except KeyError:
            return lang

    # Convert ddc to text for tagging
    def ddc_to_text(self, ddc, log):
        # Caveat:
        #     <datafield tag="082" ind1="7" ind2="4">
        #       <subfield code="a">830</subfield>
        #       <subfield code="a">B</subfield>
        if not ddc.isnumeric():
            return None
        ddc = str(ddc)
        QUERY_BASE_URL = 'https://deweysearchde.pansoft.de/webdeweysearch/executeSearch.html'
        query_url = QUERY_BASE_URL + '?query=' + ddc + '&catalogs=DNB'
        # https://deweysearchde.pansoft.de/webdeweysearch/executeSearch.html?query=390&catalogs=DNB
        try:
            response = self.browser.open_novisit(query_url, timeout=30)
            raw = response.read()
            data = fromstring(clean_ascii_chars(raw))
            ddc_subject_area = data.xpath('//*[@id="scheduleResult"]/tbody/tr[td[1]=' + ddc
                                          + ']/td[2]/div[1]/span')[-1].text_content().strip()
            return ddc_subject_area
        except Exception as e:
            log.info('Exception in ddc_to_text: %s' % e)
            return None


    # Remove German joiners from list of words
    # By default, Calibre's function "get_title_tokens(...,strip_joiners=True,...)" only removes "a", "and", "the", "&"
    def strip_german_joiners(self, wordlist):
        tokens = []
        for word in wordlist:
            if word.lower() not in ( 'ein', 'eine', 'einer', 'der', 'die', 'das', 'und', 'oder'):
                tokens.append(word)
        return tokens


########################################
if __name__ == '__main__':  # tests
    # To run these test use:
    # calibre-debug -e __init__.py
    from calibre.ebooks.metadata.sources.test import (
        test_identify_plugin, title_test, authors_test, series_test)

    test_identify_plugin(DNB_DE.name, [
        (
            {'identifiers': {'isbn': '9783404285266'}}, 
            [
                title_test('der goblin-held', exact=True),
                authors_test(['jim c. hines']),
                series_test('Die Goblin-Saga / Jim C. Hines', '4'),
            ], 
        ), 
        (
            {'identifiers': {'dnb-idn': '1136409025'}},
            [
                title_test('Sehnsucht des Herzens', exact=True),
                authors_test(['Lucas, Joanne St.']),
                series_test('Die Goblin-Saga / Jim C. Hines', '4'),
            ]
        ),
    ])
