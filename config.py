#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import (unicode_literals, division,
                        absolute_import, print_function)
from calibre.utils.config import JSONConfig
from calibre.gui2.metadata.config import ConfigWidget as DefaultConfigWidget

__license__ = 'GPL v3'
__copyright__ = '2017, Bernhard Geier <geierb@geierb.de>'
__docformat__ = 'restructuredtext en'


from PyQt5.Qt import QLabel, QGridLayout, QGroupBox, QCheckBox, QButtonGroup, QRadioButton, QPlainTextEdit

STORE_NAME = 'Options'

KEY_GUESS_SERIES = 'guessSeries'
KEY_APPEND_EDITION_TO_TITLE = 'appendEditionToTitle'
KEY_FETCH_SUBJECTS = 'subjects'
KEY_FETCH_ALL = 'fetchAll'
KEY_APPEND_SUBTITLE_TO_TITLE = 'appendSubtitleToTitle'
KEY_STOP_AFTER_FIRST_HIT = 'stopAfterFirstHit'
KEY_PREFER_RESULTS_WITH_ISBN = 'preferResultsWithIsbn'
KEY_CAN_GET_MULTIPLE_COVERS = 'canGetMultipleCovers'
KEY_EDITOR_PATTERNS = 'editorPatterns'
KEY_ARTIST_PATTERNS = 'artistPatterns'
KEY_TRANSLATOR_PATTERNS = 'translatorPatterns'
KEY_FOREWORD_PATTERNS = 'forewordPatterns'
KEY_SHOW_MARC21_FIELD_NUMBERS = 'showMarc21FieldNumbers'

DEFAULT_STORE_VALUES = {
    KEY_GUESS_SERIES: True,
    KEY_APPEND_EDITION_TO_TITLE: False,
    # 0:only gnd   1:prefer gnd 2:both   3:prefer non-gnd   4:only non-gnd   5:none
    KEY_FETCH_SUBJECTS: 2,
    KEY_FETCH_ALL: False,
    KEY_APPEND_SUBTITLE_TO_TITLE: True,
    KEY_STOP_AFTER_FIRST_HIT: True,
    KEY_PREFER_RESULTS_WITH_ISBN: True,
    KEY_CAN_GET_MULTIPLE_COVERS: False,
    KEY_EDITOR_PATTERNS: ['Neu hrsg. von ', 'hrsg. und eingeleitet von ', 'Hrsg. u. eingel. von ',
                          '[Hh]rsg. von ', 'Hrsg.:', 'Ausgew. und mit einem Nachw. von ', 'Ausgew. u. bearb. von ',
                          'hrsg. u. mit e. Einl. vers. von ', 'Erg. u. teilweise neu gestaltet von '],
    KEY_ARTIST_PATTERNS: ['Illustrator: ', '[Ii]llustriert von ', 'Ill. von ', 'Textill.:', 'u. Bild. von ',
                          'm. Bild. von ', 'mit Bildern von ', 'Die Bilder sind v. ', '(Mit .* Bildern .* von) (.*)%%'],
    # , '(\. [Aa]us (?:dem|d\.) (.*) von) (.*)%%' -- This pattern will be used in regex search for the original language
    KEY_TRANSLATOR_PATTERNS: ['neu übers. u. mit Anm. vers. von ', 'übers. und mit Anm. versehen von ',
                              'übers. u. mit Anm. vers. von ', 'übers. u. mit Anm. versehen von ', 'nach dem Text von ',
                              'ins .* übers.* von ', 'Übersetzt von ', 'Dt. Übers.:', 'Übers.:'],
    KEY_FOREWORD_PATTERNS: ['M. e. Vorw. von ', 'Vorwort von ', 'Vorw. von ', 'M. e. Geleitwort von '],
    KEY_SHOW_MARC21_FIELD_NUMBERS: False,
}

# This is where all preferences for this plugin will be stored
plugin_prefs = JSONConfig('plugins/DNB_DE')
# Set defaults
plugin_prefs.defaults[STORE_NAME] = DEFAULT_STORE_VALUES


class ConfigWidget(DefaultConfigWidget):
    def __init__(self, plugin):
        DefaultConfigWidget.__init__(self, plugin)
        c = plugin_prefs[STORE_NAME]

        other_group_box = QGroupBox('Other options', self)
        self.l.addWidget(other_group_box, self.l.rowCount(), 0, 1, 2)
        other_group_box_layout = QGridLayout()
        other_group_box.setLayout(other_group_box_layout)

        # Guess Series
        guess_series_label = QLabel(
            'Guess Series and Series Index from Title:', self)
        guess_series_label.setToolTip('DNB only rarely provides data about a book\'s series.\n'
                                      'This plugin can try to extract series and series_index from the book title.\n')
        other_group_box_layout.addWidget(guess_series_label, 0, 0, 1, 1)

        self.guess_series_checkbox = QCheckBox(self)
        self.guess_series_checkbox.setChecked(
            c.get(KEY_GUESS_SERIES, DEFAULT_STORE_VALUES[KEY_GUESS_SERIES]))
        other_group_box_layout.addWidget(
            self.guess_series_checkbox, 0, 1, 1, 1)

        # Append Edition to Title
        append_edition_to_title_label = QLabel(
            'Append Edition to Title:', self)
        append_edition_to_title_label.setToolTip('For some books DNB has information about the edition.\n'
                                                 'This plugin can fetch this information and append it to the book\'s title,\n'
                                                 'e.g. "Mord am Tegernsee : Ein Bayern-Krimi : 2. Aufl.".\n'
                                                 'Of course this only works reliable if you search for a book with a known unique identifier such as dnb-idn or ISBN.')
        other_group_box_layout.addWidget(
            append_edition_to_title_label, 1, 0, 1, 1)

        self.append_edition_to_title_checkbox = QCheckBox(self)
        self.append_edition_to_title_checkbox.setChecked(c.get(
            KEY_APPEND_EDITION_TO_TITLE, DEFAULT_STORE_VALUES[KEY_APPEND_EDITION_TO_TITLE]))
        other_group_box_layout.addWidget(
            self.append_edition_to_title_checkbox, 1, 1, 1, 1)

        # Fetch Subjects
        fetch_subjects_label = QLabel('Fetch Subjects:', self)
        fetch_subjects_label.setToolTip('DNB provides several types of subjects:\n'
                                        ' - Standardized subjects according to the GND\n'
                                        ' - Subjects delivered by the publisher\n'
                                        'You can choose which ones to fetch.')
        other_group_box_layout.addWidget(fetch_subjects_label, 2, 0, 1, 1)

        self.fetch_subjects_radios_group = QButtonGroup(other_group_box)
        titles = ['only GND subjects', 'GND subjects if available, otherwise non-GND subjects', 'GND and non-GND subjects',
                  'non-GND subjects if available, otherwise GND subjects', 'only non-GND subjects', 'none']
        self.fetch_subjects_radios = [
            QRadioButton(title) for title in titles]
        for i, radio in enumerate(self.fetch_subjects_radios):
            if i == c.get(KEY_FETCH_SUBJECTS, DEFAULT_STORE_VALUES[KEY_FETCH_SUBJECTS]):
                radio.setChecked(True)
            self.fetch_subjects_radios_group.addButton(radio, i)
            other_group_box_layout.addWidget(radio, 2 + i, 1, 1, 1)

        # Fetch all data
        fetchAll_label = QLabel(
            _('Fetch all data in book record:'), self)
        fetchAll_label.setToolTip(_('Additional, non-Calibre book data will be stored in comments.\n'))
        other_group_box_layout.addWidget(fetchAll_label, 8, 0, 1, 1)

        self.fetchAll_checkbox = QCheckBox(self)
        self.fetchAll_checkbox.setChecked(
            c.get(KEY_FETCH_ALL, DEFAULT_STORE_VALUES[KEY_FETCH_ALL]))
        other_group_box_layout.addWidget(
            self.fetchAll_checkbox, 8, 1, 1, 1)

        # Append subtitle to title
        appendSubtitleToTitle_label = QLabel(
            _('Append subtitle to title:'), self)
        appendSubtitleToTitle_label.setToolTip(_('Subtitle is appended to the main title, otherwise it will be stored in comments.\n'))
        other_group_box_layout.addWidget(appendSubtitleToTitle_label, 9, 0, 1, 1)

        self.appendSubtitleToTitle_checkbox = QCheckBox(self)
        self.appendSubtitleToTitle_checkbox.setChecked(
            c.get(KEY_APPEND_SUBTITLE_TO_TITLE, DEFAULT_STORE_VALUES[KEY_APPEND_SUBTITLE_TO_TITLE]))
        other_group_box_layout.addWidget(
            self.appendSubtitleToTitle_checkbox, 9, 1, 1, 1)

        # Stop after first hit?
        stopAfterFirstHit_label = QLabel(
            _('Stop after first hit:'), self)
        stopAfterFirstHit_label.setToolTip(_('Stop search after first book record is found.\n'))
        other_group_box_layout.addWidget(stopAfterFirstHit_label, 10, 0, 1, 1)

        self.stopAfterFirstHit_checkbox = QCheckBox(self)
        self.stopAfterFirstHit_checkbox.setChecked(
            c.get(KEY_STOP_AFTER_FIRST_HIT, DEFAULT_STORE_VALUES[KEY_STOP_AFTER_FIRST_HIT]))
        other_group_box_layout.addWidget(
            self.stopAfterFirstHit_checkbox, 10, 1, 1, 1)

        # Prefer results with ISBN?
        preferResultsWithIsbn_label = QLabel(
            _('Prefer results with ISBN:'), self)
        preferResultsWithIsbn_label.setToolTip(_('If set to True, and this source returns multiple results for a '
                                                       'query, some of which have ISBNs and some of which do not, the '
                                                       'results without ISBNs will be ignored. '
                                                       '(see https://manual.calibre-ebook.com/de/plugins.html)\n'))
        other_group_box_layout.addWidget(preferResultsWithIsbn_label, 11, 0, 1, 1)

        self.preferResultsWithIsbn_checkbox = QCheckBox(self)
        self.preferResultsWithIsbn_checkbox.setChecked(
            c.get(KEY_PREFER_RESULTS_WITH_ISBN, DEFAULT_STORE_VALUES[KEY_PREFER_RESULTS_WITH_ISBN]))
        other_group_box_layout.addWidget(
            self.preferResultsWithIsbn_checkbox, 11, 1, 1, 1)

        # Can get multiple covers?
        canGetMultipleCovers_label = QLabel(
            _('Can get multiple covers:'), self)
        canGetMultipleCovers_label.setToolTip(_('If True, the plugin can return multiple covers for a given query.'))
        other_group_box_layout.addWidget(canGetMultipleCovers_label, 12, 0, 1, 1)

        self.canGetMultipleCovers_checkbox = QCheckBox(self)
        self.canGetMultipleCovers_checkbox.setChecked(
            c.get(KEY_CAN_GET_MULTIPLE_COVERS, DEFAULT_STORE_VALUES[KEY_CAN_GET_MULTIPLE_COVERS]))
        other_group_box_layout.addWidget(
            self.canGetMultipleCovers_checkbox, 12, 1, 1, 1)

        # Patterns for editor detection
        editorPatterns_label = QLabel(
            _('Patterns to detect editors:'), self)
        editorPatterns_label.setToolTip(_('RegEx pattern to detect editors, without the editor\'s name itself. '
                                          'One pattern per line in descending check order.'))
        other_group_box_layout.addWidget(editorPatterns_label, 13, 0, 1, 1)

        self.editorPatterns_textarea = QPlainTextEdit(self)
        self.editorPatterns_textarea.setPlainText(
            '\n'.join(c.get(KEY_EDITOR_PATTERNS, DEFAULT_STORE_VALUES[KEY_EDITOR_PATTERNS])))
        other_group_box_layout.addWidget(
            self.editorPatterns_textarea, 13, 1, 1, 1)

        # Patterns for artist detection
        artistPatterns_label = QLabel(
            _('Patterns to detect artists:'), self)
        artistPatterns_label.setToolTip(_('RegEx pattern to detect artists, without the artist\'s name itself. '
                                          'One pattern per line in ascending check order.'))
        other_group_box_layout.addWidget(artistPatterns_label, 14, 0, 1, 1)

        self.artistPatterns_textarea = QPlainTextEdit(self)
        self.artistPatterns_textarea.setPlainText(
            '\n'.join(c.get(KEY_ARTIST_PATTERNS, DEFAULT_STORE_VALUES[KEY_ARTIST_PATTERNS])))
        other_group_box_layout.addWidget(
            self.artistPatterns_textarea, 14, 1, 1, 1)

        # Patterns for translator detection
        translatorPatterns_label = QLabel(
            _('Patterns to detect translators:'), self)
        translatorPatterns_label.setToolTip(_('RegEx pattern to detect translators, without the translator\'s name itself. '
                                          'One pattern per line in ascending check order.'))
        other_group_box_layout.addWidget(translatorPatterns_label, 15, 0, 1, 1)

        self.translatorPatterns_textarea = QPlainTextEdit(self)
        self.translatorPatterns_textarea.setPlainText(
            '\n'.join(c.get(KEY_TRANSLATOR_PATTERNS, DEFAULT_STORE_VALUES[KEY_TRANSLATOR_PATTERNS])))
        other_group_box_layout.addWidget(
            self.translatorPatterns_textarea, 15, 1, 1, 1)

        # Patterns for foreword (preface) detection
        forewordPatterns_label = QLabel(
            _('Patterns to detect foreword writer:'), self)
        forewordPatterns_label.setToolTip(_('RegEx pattern to detect foreword writers, without the foreword writer\'s name itself. '
                                          'One pattern per line in ascending check order.'))
        other_group_box_layout.addWidget(forewordPatterns_label, 16, 0, 1, 1)

        self.forewordPatterns_textarea = QPlainTextEdit(self)
        self.forewordPatterns_textarea.setPlainText(
            '\n'.join(c.get(KEY_FOREWORD_PATTERNS, DEFAULT_STORE_VALUES[KEY_FOREWORD_PATTERNS])))
        other_group_box_layout.addWidget(
            self.forewordPatterns_textarea, 16, 1, 1, 1)

        # Show MARC21 field numbers?
        showMarc21FieldNumbers_label = QLabel(
            _('Show MARC21 field numbers:'), self)
        showMarc21FieldNumbers_label.setToolTip(_('Show MARC21 field numbers in comments for reference purposes.\n'))
        other_group_box_layout.addWidget(showMarc21FieldNumbers_label, 17, 0, 1, 1)

        self.showMarc21FieldNumbers_checkbox = QCheckBox(self)
        self.showMarc21FieldNumbers_checkbox.setChecked(
            c.get(KEY_SHOW_MARC21_FIELD_NUMBERS, DEFAULT_STORE_VALUES[KEY_SHOW_MARC21_FIELD_NUMBERS]))
        other_group_box_layout.addWidget(
            self.showMarc21FieldNumbers_checkbox, 17, 1, 1, 1)


    def commit(self):
        DefaultConfigWidget.commit(self)
        new_prefs = {}
        new_prefs[KEY_GUESS_SERIES] = self.guess_series_checkbox.isChecked()
        new_prefs[KEY_APPEND_EDITION_TO_TITLE] = self.append_edition_to_title_checkbox.isChecked()
        new_prefs[KEY_FETCH_SUBJECTS] = self.fetch_subjects_radios_group.checkedId()
        new_prefs[KEY_FETCH_ALL] = self.fetchAll_checkbox.isChecked()
        new_prefs[KEY_APPEND_SUBTITLE_TO_TITLE] = self.appendSubtitleToTitle_checkbox.isChecked()
        new_prefs[KEY_STOP_AFTER_FIRST_HIT] = self.stopAfterFirstHit_checkbox.isChecked()
        new_prefs[KEY_PREFER_RESULTS_WITH_ISBN] = self.preferResultsWithIsbn_checkbox.isChecked()
        new_prefs[KEY_CAN_GET_MULTIPLE_COVERS] = self.canGetMultipleCovers_checkbox.isChecked()
        new_prefs[KEY_EDITOR_PATTERNS] = self.editorPatterns_textarea.toPlainText().split("\n")
        new_prefs[KEY_ARTIST_PATTERNS] = self.artistPatterns_textarea.toPlainText().split("\n")
        new_prefs[KEY_TRANSLATOR_PATTERNS] = self.translatorPatterns_textarea.toPlainText().split("\n")
        new_prefs[KEY_FOREWORD_PATTERNS] = self.forewordPatterns_textarea.toPlainText().split("\n")
        new_prefs[KEY_SHOW_MARC21_FIELD_NUMBERS] = self.showMarc21FieldNumbers_checkbox.isChecked()

        plugin_prefs[STORE_NAME] = new_prefs
