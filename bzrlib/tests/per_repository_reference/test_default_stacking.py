# Copyright (C) 2009 Canonical Ltd
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


from bzrlib.smart import server
from bzrlib.tests.per_repository import TestCaseWithRepository


class TestDefaultStackingPolicy(TestCaseWithRepository):

    # XXX: this helper probably belongs on TestCaseWithTransport
    def make_smart_server(self, path):
        smart_server = server.SmartTCPServer_for_testing()
        smart_server.setUp(self.get_server())
        return smart_server.get_url() + path

    def test_sprout_to_smart_server_stacking_policy_handling(self):
        """Obey policy where possible, ignore otherwise."""
        stack_on = self.make_branch('stack-on')
        parent_bzrdir = self.make_bzrdir('.', format='default')
        parent_bzrdir.get_config().set_default_stack_on('stack-on')
        source = self.make_branch('source')
        url = self.make_smart_server('target')
        target = source.bzrdir.sprout(url).open_branch()
        self.assertEqual('../stack-on', target.get_stacked_on_url())
        self.assertEqual(
            source._format.network_name(), target._format.network_name())
