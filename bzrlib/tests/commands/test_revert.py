# Copyright (C) 2007 Canonical Ltd
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

import os
from bzrlib import (
    branch,
    builtins,
    errors,
    )
from bzrlib.tests import transport_util


class TestRevert(
    transport_util.TestCaseWithConnectionHookedTransport):

    def setUp(self):
        super(TestRevert, self).setUp()
        self.local_wt = self.make_branch_and_tree('local')

    def test_revert_tree_write_lock_and_branch_read_lock(self):

        self.start_logging_connections()

        os.chdir('local')

        revert = builtins.cmd_revert()
        num_before = len(self._lock_actions)
        revert.run()
        num_after = len(self._lock_actions)

        # only expect the working tree to be locked and released, so 2
        # additional entries.
        self.assertEquals(num_before+2, num_after)

#    def test_commit_both_modified(self):
#        self.master_wt.commit('empty commit on master')
#        self.start_logging_connections()
#
#        commit = builtins.cmd_commit()
#        # commit do not provide a directory parameter, we have to change dir
#        # manually
#        os.chdir('local')
#        # cmd_commit translates BoundBranchOutOfDate into BzrCommandError
#        self.assertRaises(errors.BzrCommandError, commit.run,
#                          message=u'empty commit', unchanged=True)
#        self.assertEquals(1, len(self.connections))
#
#    def test_commit_local(self):
#        """Commits with --local should not connect to the master!"""
#        self.start_logging_connections()
#
#        commit = builtins.cmd_commit()
#        # commit do not provide a directory parameter, we have to change dir
#        # manually
#        os.chdir('local')
#        commit.run(message=u'empty commit', unchanged=True, local=True)
#
#        #it shouldn't open any connections
#        self.assertEquals(0, len(self.connections))
