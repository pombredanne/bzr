# Copyright (C) 2006 Canonical Ltd
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

"""Tests for remote bzrdir/branch/repo/etc

These are proxy objects which act on remote objects by sending messages
through a smart client.  The proxies are to be created when attempting to open
the object given a transport that supports smartserver rpc operations. 
"""

from bzrlib import bzrdir, remote, tests
from bzrlib.transport import remote as remote_transport
from bzrlib import smart
from bzrlib.smart import server
from bzrlib.bzrdir import BzrDir, BzrDirFormat
from bzrlib.remote import RemoteBzrDir, RemoteBzrDirFormat
from bzrlib.branch import Branch


class BasicRemoteObjectTests(tests.TestCaseInTempDir):

    def setUp(self):
        tests.TestCaseInTempDir.setUp(self)
        self.server = server.SmartTCPServer_for_testing()
        self.server.setUp()
        self.addCleanup(self.server.tearDown)
        self.transport = remote_transport.SmartTCPTransport(self.server.get_url())
        self.client = self.transport.get_smart_client()
        # make a branch that can be opened over the smart transport
        self.local_wt = BzrDir.create_standalone_workingtree('.')

    def test_create_remote_bzrdir(self):
        b = remote.RemoteBzrDir(self.transport)
        self.assertIsInstance(b, BzrDir)

    def test_open_remote_branch(self):
        # open a standalone branch in the working directory
        b = remote.RemoteBzrDir(self.transport)
        branch = b.open_branch()

    def test_remote_repository(self):
        b = BzrDir.open_from_transport(self.transport)
        repo = b.open_repository()
        revid = u'\xc823123123'
        self.assertFalse(repo.has_revision(revid))
        self.local_wt.commit(message='test commit', rev_id=revid)
        self.assertTrue(repo.has_revision(revid))

    def test_remote_branch_revision_history(self):
        b = BzrDir.open_from_transport(self.transport).open_branch()
        self.assertEqual([], b.revision_history())
        r1 = self.local_wt.commit('1st commit')
        r2 = self.local_wt.commit('1st commit', rev_id=u'\xc8')
        self.assertEqual([r1, r2], b.revision_history())

    def test_find_correct_format(self):
        """Should open a RemoteBzrDir over a RemoteTransport"""
        fmt = BzrDirFormat.find_format(self.transport)
        self.assertTrue(RemoteBzrDirFormat in BzrDirFormat._control_formats)
        self.assertIsInstance(fmt, remote.RemoteBzrDirFormat)

    def test_open_detected_smart_format(self):
        fmt = BzrDirFormat.find_format(self.transport)
        d = fmt.open(self.transport)
        self.assertIsInstance(d, BzrDir)
