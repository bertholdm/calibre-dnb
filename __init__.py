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
                    'authors': [],
                    'author_sort': None,
                    'edition': None,
                    'editor': None,
                    'artist': None,
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
                            location_parts.append(i.text.strip())
                        if location_parts:
                            book['publisher_location'] = ' '.join(location_parts).strip('[]')

                    if not book['publisher_name']:
                        try:
                            book['publisher_name'] = field.xpath("./marc21:subfield[@code='b' and string-length(text())>0]", namespaces=ns)[0].text.strip()
                            log.info("[264.b] Publisher: %s" % book['publisher_name'])
                        except IndexError:
                            pass

                    if not book['pubdate']:
                        try:
                            pubdate = field.xpath("./marc21:subfield[@code='c' and string-length(text())>=4]", namespaces=ns)[0].text.strip()
                            match = re.search("(\d{4})", pubdate)
                            year = match.group(1)
                            book['pubdate'] = datetime.datetime(int(year), 1, 1, 12, 30, 0)
                            log.info("[264.c] Publication Year: %s" % book['pubdate'])
                        except (IndexError, AttributeError):
                            pass

                # ToDo: At least fetch original pubdate
                ##### Field 534 - "Original Version Note" #####
                # or:
                # <controlfield tag="008">240627r20241795xx u||p|o ||| 0||||1ger  </controlfield
                # gives in DBB catalog GUI:
                # Zeitliche Einordnung	Erscheinungsdatum: 2024 Original: 1795
                # see: https://www.loc.gov/marc/archive/2000/concise/ecbd008s.html


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

                    # Remainder of title (Zusatz zum Titel)
                    if code_c:

                        # Step 1: Mark parts by uniforming identifiers
                        for code_c_element in code_c:
                            code_c = [s + '%%' for s in code_c]  # Mark end of code c entry
                            for delimiter in ['Hrsg. von ', 'hrsg. von ', '. Hrsg.: ', 'Ausgew. und mit einem Nachw. von ']:
                                code_c = list(map(lambda x: x.replace(delimiter, '%%e:'), code_c))  ## Mark editor
                            for delimiter in ['Illustrator: ', 'Illustriert von ', 'illustriert von ', 'Ill. von ']:
                                code_c = list(map(lambda x: x.replace(delimiter, '%%a:'), code_c))  ## Mark artist
                            for delimiter in ['[Übers.:', 'Übers.:', 'Übersetzt von']:
                                # log.info("[delimiter=%s" % delimiter)
                                # log.info("[delimiter=%s" % ":".join("{:02x}".format(ord(c)) for c in delimiter))
                                delimiter = unicodedata_normalize("NFKC", delimiter)
                                # log.info("[delimiter, normalized=%s" % ":".join("{:02x}".format(ord(c)) for c in delimiter))
                                # log.info("[code_c[0]=%s" % code_c[0])
                                # log.info("[code_c[0]=%s" % ":".join("{:02x}".format(ord(c)) for c in code_c[0]))
                                code_c = list(map(lambda x: x.replace(delimiter, '%%t:'), code_c))  ## Mark translator
                        log.info("[245.c] code_c after uniforming identifiers=%s" % code_c)

                        # Step 2: Identifiying parts by divide and conquer
                        for code_c_element in code_c:
                            match = re.search("%%e:(.*?)%%", code_c_element)  # Search until first '%%' (non-greedy)
                            if match:
                                book['editor'] = match.group(1).strip().strip('.').strip()
                                log.info("book['editor']=%s" % book['editor'])
                                code_c = list(map(lambda x: x.replace('%%e:' + match.group(1), ''), code_c))  ## strip match
                        for code_c_element in code_c:
                            match = re.search("%%a:(.*?)%%", code_c_element)  # Search until first '%%' (non-greedy)
                            if match:
                                book['artist'] = match.group(1).strip().strip('.').strip()
                                log.info("book['artist']=%s" % book['artist'])
                                code_c = list(map(lambda x: x.replace('%%a:' + match.group(1), ''), code_c))  ## strip match
                        for code_c_element in code_c:
                            match = re.search("%%t:(.*?)%%", code_c_element)  # Search until first '%%' (non-greedy)
                            if match:
                                book['translator'] = match.group(1).strip().strip('.').strip()
                                log.info("book['translator']=%s" % book['translator'])
                                code_c = list(map(lambda x: x.replace('%%t:' + match.group(1), ''), code_c))  ## strip match
                        # Is there a remainder?
                        for code_c_element in code_c:
                            code_c = list(map(lambda x: x.replace('%%', ''), code_c))  ## strip delimiters
                            code_c = list(map(lambda x: x.replace(' ', ''), code_c))  ## strip delimiters
                        log.info("code_c after stripping=%s" % code_c)



                    # ToDo:
                    # 245.a] code_a=['Spannende Geschichten']
                    # [245.c] code_c=['Hrsg. von Günther Bicknese. Ill. von Günter Büsemeyer']
                    # [245.n] code_n=['17']
                    # [245.p] code_p=['Start ins Ungewisse / [Von] Heinz Helfgen']

                    #     <datafield tag="245" ind1="1" ind2="0">
                    #       <subfield code="a">Auf dem Jakobsweg</subfield>
                    #       <subfield code="b">Tagebuch einer Pilgerreise nach Santiago de Compostela</subfield>
                    #       <subfield code="c">Paulo Coelho. Aus dem Brasilianischen von Maralde Meyer-Minnemann</subfield>

                    # ToDo: 245.c] code_c=['Hrsg. von Günther Bicknese. Ill. von Günter Büsemeyer']

                    #     <datafield tag="245" ind1="1" ind2="0">
                    #       <subfield code="a">Märchen aus Bayern</subfield>
                    #       <subfield code="b">Märchen der Welt</subfield>
                    #       <subfield code="c">Karl Spiegel</subfield>

                    # [245.a] code_a=['Deutsches Märchenbuch']
                    # [245.b] code_b=['Mit Illustrationen von Ludwig Richter']
                    # [245.c] code_c=['Ludwig Bechstein ; Illustrator: Ludwig Richter']
                    # [245.n] code_n=[]
                    # [245.p] code_p=[]

                    #     <datafield tag="245" ind1="0" ind2="0">
                    #       <subfield code="a">&#152;Die&#156; Horen</subfield>
                    #       <subfield code="n">1.1795, Stück 1-12 = Bd. 1-4</subfield>
                    #       <subfield code="c">hrsg. von Schiller</subfield>

                    #     <datafield tag="245" ind1="1" ind2="0">
                    #       <subfield code="a">Am Kamin und andere unheimliche Geschichten</subfield>
                    #       <subfield code="c">Theodor Storm. Mit Ill. von Roswitha Quadflieg. Ausgew. und mit einem Nachw. von Gottfried Honnefelder</subfield>



                    # <datafield tag="245" ind1="0" ind2="0">
                    #   <subfield code="a">Fliegergeschichten</subfield>
                    #   <subfield code="n">Bd. 188.</subfield>
                    #   <subfield code="p">Über der Hölle des Mauna Loa / Otto Behrens</subfield>

                    # <datafield tag="245" ind1="1" ind2="0">
                    #   <subfield code="a">&#152;Die&#156; Odyssee der PN-9</subfield>
                    #   <subfield code="c">Fritz Moeglich. Hrsg.: Peter Supf</subfield>


                    # Caching subtitle
                    if code_a and code_b and code_c:
                        book['subtitle'] = code_b[0]

                    # c = author and perhaps editor, artist etc.
                    code_c_authors = None
                    if code_a and code_c:

                        # ToDo: consider regex

                        # 245.c ['Hrsg. von Günther Bicknese. Ill. von Günter Büsemeyer']
                        for delimiter in ['Hrsg. von ', 'hrsg. von ', '. Hrsg.: ']:
                            code_c_split = code_c[0].split(delimiter)
                            code_c_authors = code_c_split[0]
                            if len(code_c_split) > 1:
                                book['editor'] = code_c_split[1].strip()
                                code_c = [code_c[0].replace(delimiter + book['editor'], '')]
                                log.info("code_c=%s" % code_c)
                                break
                        # [245.c] ['Ludwig Bechstein ; Illustrator: Ludwig Richter']
                        for delimiter in ['Illustrator: ', 'Illustriert von ', 'illustriert von ', 'Ill. von ']:
                            code_c_split = code_c[0].split(delimiter)
                            code_c_authors = code_c_split[0]
                            code_c_authors = code_c_authors.strip(';').strip()
                            if len(code_c_split) > 1:
                                book['artist'] = code_c_split[1].strip()
                                code_c = [code_c[0].replace(book['artist'], '')]
                                log.info("code_c=%s" % code_c)
                                break
                        # <subfield code="c">von Gérard de Villiers. [Übers.: Jürgen Hofmann]</subfield>
                        # [delimiter=5b:55:308:62:65:72:73:2e:3a
                        # [code_c[0]=von Gérard de Villiers. [Übers.: Jürgen Hofmann]
                        # [code_c[0]=76:6f:6e:20:47:e9:72:61:72:64:20:64:65:20:56:69:6c:6c:69:65:72:73:2e:20:5b:dc:62:65:72:73:2e:3a:20:4a:fc:72:67:65:6e:20:48:6f:66:6d:61:6e:6e:5d
                        # Ü in delimiter is x'55', Ü in decoded xml (code_c) is x'dc' => compare fails. So normalize
                        # the delimiter before compare.
                        for delimiter in ['[Übers.:', 'Übers.:', 'Übersetzt von']:
                            # log.info("[delimiter=%s" % delimiter)
                            # log.info("[delimiter=%s" % ":".join("{:02x}".format(ord(c)) for c in delimiter))
                            delimiter = unicodedata_normalize("NFKC", delimiter)
                            # log.info("[delimiter, normalized=%s" % ":".join("{:02x}".format(ord(c)) for c in delimiter))
                            # log.info("[code_c[0]=%s" % code_c[0])
                            # log.info("[code_c[0]=%s" % ":".join("{:02x}".format(ord(c)) for c in code_c[0]))
                            code_c_split = code_c[0].split(delimiter)
                            code_c_authors = code_c_split[0]
                            code_c_authors = code_c_authors.strip(';').strip()
                            if len(code_c_split) > 1:
                                book['translator'] = code_c_split[1].strip(']').strip()
                                code_c = [code_c[0].replace(book['translator'], '')]
                                log.info("code_c=%s" % code_c)
                                break

                    # a = series, n = series index, p = title and author
                    # <subfield code="a">Spannende Geschichten</subfield>
                    # <subfield code="n">119.</subfield>
                    # <subfield code="p">Schiffbruch zwischen Erde und Mond / [Von] Henry Gamarick. Ill.: G. Büsemeyer [u.a.]</subfield>
                    # <subfield code="c">Hrsg. von Günther Bicknese. Ill. von Günter Büsemeyer</subfield>
                    if code_a and code_n and code_p and code_c:
                        pass

                    # a = series, n = series index, p = title and author
                    code_p_authors = None
                    if code_a and code_n and code_p:
                        code_p_authors_split = code_p[0].split(" / ")
                        code_p = [code_p_authors_split[0]]
                        if len(code_p_authors_split) > 1:
                            code_p_authors = code_p_authors_split[1].strip()
                            code_p_authors.removeprefix('[Von]').strip()
                            book['authors'].extend([code_p_authors])

                    # Title
                    if code_p:
                        title_parts = code_p
                    else:
                        title_parts = code_a

                    # Looks like we have a series
                    if code_a and code_n:
                        # set title ("Name of this Book")
                        if code_p:
                            title_parts = [code_p[-1]]

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

                #       <subfield code="a">Meyer-Minnemann, Maralde</subfield>
                #       <subfield code="e">Übersetzer</subfield>
                #       <subfield code="4">trl</subfield>
                #       <subfield code="2">gnd</subfield>
                for i in record.xpath("./marc21:datafield[@tag='700']/marc21:subfield[@code='4' and "
                                      "text()='trl']/../marc21:subfield[@code='a' and "
                                      "string-length(text())>0]", namespaces=ns):
                    book['translator'] = re.sub(" \[.*\]$", "", i.text.strip())

                if secondary_authors:
                    book['authors'].extend(secondary_authors)
                    log.info("[700.a] Secondary Authors: %s" % " & ".join(secondary_authors))

                ##### Field 245 and code c or p #####
                if not book['authors']:
                    if code_c_authors:
                        book['authors'] = [code_c_authors]
                    if code_p_authors:
                        book['authors'] = [code_p_authors]

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


                ##### Field 300: "PHYSICAL DESCRIPTION"  #####
                # a: Extent (Umfang)
                # b: Other physical details (A)ndere physische Details)
                # c: Dimensions (Maße)
                # e: Accompanying material (Begleitmaterial)
                for field in record.xpath("./marc21:datafield[@tag='300']", namespaces=ns):
                    log.info("[300] field=%s" % field.text.strip())
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

                            match = re.search("(\d+[,\.\d+]?)", indexpart)
                            if match:
                                series_index = match.group(1)
                                series = textpart.strip()
                                log.info("[490.v] Series: %s" % series)
                                log.info("[490.v] Series_Index: %s" % series_index)

                    else:
                        # Assumption above was wrong. Try to extract at least the series_index
                        match = re.search("(\d+[,\.\d+]?)", attr_v)
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
                        log.info("[246.a] Series_Index: %s" % book['series_index'])

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
                        log.info("[800.v] Series_Index: %s" % series_index)

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
                    match = re.search("(\d+[,\.\d+]?)", i.xpath("./marc21:subfield[@code='v']", namespaces=ns)[0].text.strip())
                    if match:
                        series_index = match.group(1)
                        log.info("[830.v] Series_Index: %s" % series_index)

                    # Series
                    series = i.xpath("./marc21:subfield[@code='a']", namespaces=ns)[0].text.strip()
                    log.info("[830.a] Series: %s" % series)

                    series = self.clean_series(log, series, book['publisher_name'])

                    if series and series_index:
                        book['series'] = series
                        book['series_index'] = series_index

                # Adjust title, if series and index in title
                if book['series']:
                    # Re-format series_index (book['series_index'] is of type string)
                    # to remove leading zeros before searching.
                    match = re.search(book['series'] + ".*" + str(int(book['series_index'])) + ".*:(.*)", book['title'])
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
                        if i.text.startswith("("):
                            continue
                        # skip one-character subjects:
                        if len(i.text) < 2:
                            continue

                        book['subjects_non_gnd'].extend(re.split(',|;', self.remove_sorting_characters(i.text)))

                if book['subjects_non_gnd']:
                    log.info("[600.a-655.a] Non-GND Subjects: %s" % " / ".join(book['subjects_non_gnd']))


                ##### Field 250: "Edition Statement" #####
                # Get Edition
                try:
                    book['edition'] = record.xpath("./marc21:datafield[@tag='250']/marc21:subfield[@code='a' "
                                                   "and string-length(text())>0]", namespaces=ns)[0].text.strip()
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

                if not book['comments']:
                    book['comments'] = ''

                # Put other data in comment field
                if self.cfg_fetch_all == True:
                    if self.cfg_prefer_results_with_isbn == False and book['isbn']:
                        book['comments'] = book['comments'] + _('\nISBN:\t') + book['isbn']
                    if book['subtitle']:
                        book['comments'] = book['comments'] + _('\nSubtitle:\t') + book['subtitle']
                    if book['editor']:
                        book['comments'] = book['comments'] + _('\nEditor:\t') + book['editor']
                    if book['artist']:
                        book['comments'] = book['comments'] + _('\nArtist:\t') + book['artist']
                    if book['translator']:
                        book['comments'] = book['comments'] + _('\nTranslator:\t') + book['translator']
                    if book['edition']:
                        book['comments'] = book['comments'] + _('\nEdition:\t') + book['edition']
                    if book['mediatype']:
                        book['comments'] = book['comments'] + _('\nMedia type:\t') + book['mediatype']
                    if book['extent']:
                        book['comments'] = book['comments'] + _('\nExtent:\t') + book['extent']
                    if book['other_physical_details']:
                        book['comments'] = book['comments'] + _('\nOther physical details:\t') + book['other_physical_details']
                    if book['dimensions']:
                        book['comments'] = book['comments'] + _('\nDimensions:\t') + book['dimensions']
                    if book['accompanying_material']:
                        book['comments'] = book['comments'] + _('\nAccompanying material:\t') + book['accompanying_material']
                    if book['terms_of_availability']:
                        book['comments'] = (book['comments'] + _('\nTerms of availability:\t') + book['terms_of_availability'])
                    if book['ddc_subject_area']:
                        book['comments'] = (book['comments'] + _('\nDDC subject area:\t') + ', '.join(book['ddc_subject_area']))
                    if book['subjects_gnd']:
                        book['comments'] = book['comments'] + _(
                            '\nGND subjects:\t') + ' / '.join(book['subjects_gnd'])
                    if book['subjects_non_gnd']:
                        book['comments'] = book['comments'] + _(
                            '\nNon-GND subjects:\t') + ' / '.join(book['subjects_non_gnd'])
                    if marc21_fields:
                        book['comments'] = book['comments'] + _('\n---\nMARC21 fields:\t') + ', '.join(marc21_fields)

                # Indicate path to source
                if book['idn']:
                    book['record_uri'] = 'https://d-nb.info/' + book['idn']
                elif book['gnd']:
                    book['record_uri'] = 'https://d-nb.info/gnd/' + book['gnd']
                book['comments'] = book['comments'] + _('\nSource:\t') + book['record_uri']
                log.info("book= %s" % book)

                if self.cfg_append_edition_to_title == True and book['edition']:
                    book['title'] = book['title'] + " : " + book['edition']

                # Avoiding Calibre's merge behavior for identical titles and authors.
                # (This behavior suppresses other editions of a title.)
                if len(results) > 1:
                    book['title'] = book['title'] + " (" + book['idn'] + ")"

                authors = list(map(lambda i: self.remove_sorting_characters(i), book['authors']))

                mi = Metadata(
                    self.remove_sorting_characters(book['title']),
                    list(map(lambda i: re.sub("^(.+), (.+)$", r"\2 \1", i), authors))
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

                # Skip series info if it starts with the first word of the publisher's name (which must be at least 4 characters long)
                match = re.search(
                    '^(\w\w\w\w+)', self.remove_sorting_characters(publisher_name))
                if match:
                    pubcompany = match.group(1)
                    if re.search('^\W*' + pubcompany, series, flags=re.IGNORECASE):
                        log.info("[Series Cleaning] Series %s starts with publisher, ignoring" % series)
                        return None

            # do not accept some other unwanted series names
            # TODO: Has issues with Umlauts in regex (or series string?)
            # TODO: Make user configurable
            for i in [
                '^Roman$', '^Science-fiction$',
                '^\[Ariadne\]$', '^Ariadne$', '^atb$', '^BvT$', '^Bastei L', '^bb$', '^Beck Paperback', '^Beck\-.*berater', '^Beck\'sche Reihe', '^Bibliothek Suhrkamp$', '^BLT$',
                '^DLV-Taschenbuch$', '^Edition Suhrkamp$', '^Edition Lingen Stiftung$', '^Edition C', '^Edition Metzgenstein$', '^ETB$', '^dtv', '^Ein Goldmann',
                '^Oettinger-Taschenbuch$', '^Haymon-Taschenbuch$', '^Mira Taschenbuch$', '^Suhrkamp-Taschenbuch$', '^Bastei-L', '^Hey$', '^btb$', '^bt-Kinder', '^Ravensburger',
                '^Sammlung Luchterhand$', '^blanvalet$', '^KiWi$', '^Piper$', '^C.H. Beck', '^Rororo', '^Goldmann$', '^Moewig$', '^Fischer Klassik$', '^hey! shorties$', '^Ullstein',
                '^Unionsverlag', '^Ariadne-Krimi', '^C.-Bertelsmann', '^Phantastische Bibliothek$', '^Beck Paperback$', '^Beck\'sche Reihe$', '^Knaur', '^Volk-und-Welt',
                '^Allgemeine', '^Premium', '^Horror-Bibliothek$']:
                if re.search(i, series, flags=re.IGNORECASE):
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
