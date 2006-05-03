# Copyright (C) 2005, 2006 Canonical Ltd
# Authors:  Robert Collins <robert.collins@canonical.com>
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
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

from cStringIO import StringIO
import os

import bzrlib
from bzrlib.branch import Branch
import bzrlib.bzrdir as bzrdir
from bzrlib.bzrdir import BzrDir
from bzrlib.conflicts import *
import bzrlib.errors as errors
from bzrlib.errors import NotBranchError, NotVersionedError
from bzrlib.lockdir import LockDir
from bzrlib.osutils import pathjoin, getcwd, has_symlinks
from bzrlib.tests import TestCaseWithTransport, TestSkipped
from bzrlib.trace import mutter
from bzrlib.transport import get_transport
import bzrlib.workingtree as workingtree
from bzrlib.workingtree import (TreeEntry, TreeDirectory, TreeFile, TreeLink,
                                WorkingTree)

class TestTreeDirectory(TestCaseWithTransport):

    def test_kind_character(self):
        self.assertEqual(TreeDirectory().kind_character(), '/')


class TestTreeEntry(TestCaseWithTransport):

    def test_kind_character(self):
        self.assertEqual(TreeEntry().kind_character(), '???')


class TestTreeFile(TestCaseWithTransport):

    def test_kind_character(self):
        self.assertEqual(TreeFile().kind_character(), '')


class TestTreeLink(TestCaseWithTransport):

    def test_kind_character(self):
        self.assertEqual(TreeLink().kind_character(), '')


class TestDefaultFormat(TestCaseWithTransport):

    def test_get_set_default_format(self):
        old_format = workingtree.WorkingTreeFormat.get_default_format()
        # default is 3
        self.assertTrue(isinstance(old_format, workingtree.WorkingTreeFormat3))
        workingtree.WorkingTreeFormat.set_default_format(SampleTreeFormat())
        try:
            # the default branch format is used by the meta dir format
            # which is not the default bzrdir format at this point
            dir = bzrdir.BzrDirMetaFormat1().initialize('.')
            dir.create_repository()
            dir.create_branch()
            result = dir.create_workingtree()
            self.assertEqual(result, 'A tree')
        finally:
            workingtree.WorkingTreeFormat.set_default_format(old_format)
        self.assertEqual(old_format, workingtree.WorkingTreeFormat.get_default_format())


class SampleTreeFormat(workingtree.WorkingTreeFormat):
    """A sample format

    this format is initializable, unsupported to aid in testing the 
    open and open_downlevel routines.
    """

    def get_format_string(self):
        """See WorkingTreeFormat.get_format_string()."""
        return "Sample tree format."

    def initialize(self, a_bzrdir, revision_id=None):
        """Sample branches cannot be created."""
        t = a_bzrdir.get_workingtree_transport(self)
        t.put('format', StringIO(self.get_format_string()))
        return 'A tree'

    def is_supported(self):
        return False

    def open(self, transport, _found=False):
        return "opened tree."


class TestWorkingTreeFormat(TestCaseWithTransport):
    """Tests for the WorkingTreeFormat facility."""

    def test_find_format(self):
        # is the right format object found for a working tree?
        # create a branch with a few known format objects.
        self.build_tree(["foo/", "bar/"])
        def check_format(format, url):
            dir = format._matchingbzrdir.initialize(url)
            dir.create_repository()
            dir.create_branch()
            format.initialize(dir)
            t = get_transport(url)
            found_format = workingtree.WorkingTreeFormat.find_format(dir)
            self.failUnless(isinstance(found_format, format.__class__))
        check_format(workingtree.WorkingTreeFormat3(), "bar")
        
    def test_find_format_no_tree(self):
        dir = bzrdir.BzrDirMetaFormat1().initialize('.')
        self.assertRaises(errors.NoWorkingTree,
                          workingtree.WorkingTreeFormat.find_format,
                          dir)

    def test_find_format_unknown_format(self):
        dir = bzrdir.BzrDirMetaFormat1().initialize('.')
        dir.create_repository()
        dir.create_branch()
        SampleTreeFormat().initialize(dir)
        self.assertRaises(errors.UnknownFormatError,
                          workingtree.WorkingTreeFormat.find_format,
                          dir)

    def test_register_unregister_format(self):
        format = SampleTreeFormat()
        # make a control dir
        dir = bzrdir.BzrDirMetaFormat1().initialize('.')
        dir.create_repository()
        dir.create_branch()
        # make a branch
        format.initialize(dir)
        # register a format for it.
        workingtree.WorkingTreeFormat.register_format(format)
        # which branch.Open will refuse (not supported)
        self.assertRaises(errors.UnsupportedFormatError, workingtree.WorkingTree.open, '.')
        # but open_downlevel will work
        self.assertEqual(format.open(dir), workingtree.WorkingTree.open_downlevel('.'))
        # unregister the format
        workingtree.WorkingTreeFormat.unregister_format(format)


class TestWorkingTreeFormat3(TestCaseWithTransport):
    """Tests specific to WorkingTreeFormat3."""

    def test_disk_layout(self):
        control = bzrdir.BzrDirMetaFormat1().initialize(self.get_url())
        control.create_repository()
        control.create_branch()
        tree = workingtree.WorkingTreeFormat3().initialize(control)
        # we want:
        # format 'Bazaar-NG Working Tree format 3'
        # inventory = blank inventory
        # pending-merges = ''
        # stat-cache = ??
        # no inventory.basis yet
        t = control.get_workingtree_transport(None)
        self.assertEqualDiff('Bazaar-NG Working Tree format 3',
                             t.get('format').read())
        self.assertEqualDiff('<inventory format="5">\n'
                             '</inventory>\n',
                             t.get('inventory').read())
        self.assertEqualDiff('### bzr hashcache v5\n',
                             t.get('stat-cache').read())
        self.assertFalse(t.has('inventory.basis'))
        # no last-revision file means 'None' or 'NULLREVISION'
        self.assertFalse(t.has('last-revision'))
        # TODO RBC 20060210 do a commit, check the inventory.basis is created 
        # correctly and last-revision file becomes present.

    def test_uses_lockdir(self):
        """WorkingTreeFormat3 uses its own LockDir:
            
            - lock is a directory
            - when the WorkingTree is locked, LockDir can see that
        """
        t = self.get_transport()
        url = self.get_url()
        dir = bzrdir.BzrDirMetaFormat1().initialize(url)
        repo = dir.create_repository()
        branch = dir.create_branch()
        try:
            tree = workingtree.WorkingTreeFormat3().initialize(dir)
        except errors.NotLocalUrl:
            raise TestSkipped('Not a local URL')
        self.assertIsDirectory('.bzr', t)
        self.assertIsDirectory('.bzr/checkout', t)
        self.assertIsDirectory('.bzr/checkout/lock', t)
        our_lock = LockDir(t, '.bzr/checkout/lock')
        self.assertEquals(our_lock.peek(), None)
        tree.lock_write()
        self.assertTrue(our_lock.peek())
        tree.unlock()
        self.assertEquals(our_lock.peek(), None)


class TestFormat2WorkingTree(TestCaseWithTransport):
    """Tests that are specific to format 2 trees."""

    def create_format2_tree(self, url):
        return self.make_branch_and_tree(
            url, format=bzrlib.bzrdir.BzrDirFormat6())

    def test_conflicts(self):
        # test backwards compatability
        tree = self.create_format2_tree('.')
        self.assertRaises(errors.UnsupportedOperation, tree.set_conflicts,
                          None)
        file('lala.BASE', 'wb').write('labase')
        expected = ContentsConflict('lala')
        self.assertEqual(list(tree.conflicts()), [expected])
        file('lala', 'wb').write('la')
        tree.add('lala', 'lala-id')
        expected = ContentsConflict('lala', file_id='lala-id')
        self.assertEqual(list(tree.conflicts()), [expected])
        file('lala.THIS', 'wb').write('lathis')
        file('lala.OTHER', 'wb').write('laother')
        # When "text conflict"s happen, stem, THIS and OTHER are text
        expected = TextConflict('lala', file_id='lala-id')
        self.assertEqual(list(tree.conflicts()), [expected])
        os.unlink('lala.OTHER')
        os.mkdir('lala.OTHER')
        expected = ContentsConflict('lala', file_id='lala-id')
        self.assertEqual(list(tree.conflicts()), [expected])
