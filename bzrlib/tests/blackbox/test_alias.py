# Copyright (C) 2008, 2009, 2010 Canonical Ltd
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#

"""Tests of the 'bzr alias' command."""

from bzrlib import (
    config,
    tests,
    )
from bzrlib.tests import (
    features,
    )


class TestAlias(tests.TestCaseWithTransport):

    def test_list_alias_with_none(self):
        """Calling alias with no parameters lists existing aliases."""
        out, err = self.run_bzr('alias')
        self.assertEquals('', out)

    def test_list_unknown_alias(self):
        out, err = self.run_bzr('alias commit')
        self.assertEquals('bzr alias: commit: not found\n', out)

    def test_add_alias_outputs_nothing(self):
        out, err = self.run_bzr('alias commit="commit --strict"')
        self.assertEquals('', out)

    def test_add_alias_visible(self):
        """Adding an alias makes it ..."""
        self.run_bzr('alias commit="commit --strict"')
        out, err = self.run_bzr('alias commit')
        self.assertEquals('bzr alias commit="commit --strict"\n', out)

    def test_unicode_alias(self):
        """Unicode aliases should work (Bug #529930)"""
        # XXX: strictly speaking, lack of unicode filenames doesn't imply that
        # unicode command lines aren't available.
        self.requireFeature(features.UnicodeFilenameFeature)
        file_name = u'foo\xb6'

        tree = self.make_branch_and_tree('.')
        self.build_tree([file_name])
        tree.add(file_name)
        tree.commit('added')

        config.GlobalConfig.from_string(
            u'[ALIASES]\nust=st %s\n' % (file_name,), save=True)

        out, err = self.run_bzr('ust')
        self.assertEquals(err, '')
        self.assertEquals(out, '')

    def test_alias_listing_alphabetical(self):
        self.run_bzr('alias commit="commit --strict"')
        self.run_bzr('alias ll="log --short"')
        self.run_bzr('alias add="add -q"')

        out, err = self.run_bzr('alias')
        self.assertEquals(
            'bzr alias add="add -q"\n'
            'bzr alias commit="commit --strict"\n'
            'bzr alias ll="log --short"\n',
            out)

    def test_remove_unknown_alias(self):
        out, err = self.run_bzr('alias --remove fooix', retcode=3)
        self.assertEquals('bzr: ERROR: The alias "fooix" does not exist.\n',
                          err)

    def test_remove_known_alias(self):
        self.run_bzr('alias commit="commit --strict"')
        out, err = self.run_bzr('alias commit')
        self.assertEquals('bzr alias commit="commit --strict"\n', out)
        # No output when removing an existing alias.
        out, err = self.run_bzr('alias --remove commit')
        self.assertEquals('', out)
        # Now its not.
        out, err = self.run_bzr('alias commit')
        self.assertEquals("bzr alias: commit: not found\n", out)
