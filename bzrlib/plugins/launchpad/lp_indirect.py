# Copyright (C) 2007, 2008 Canonical Ltd
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


"""Transport indirection that uses Launchpad as a directory lookup.

When the transport is opened, it immediately redirects to a url
on Launchpad, which can then either serve the branch itself or redirect
again.
"""

from urlparse import urlsplit, urlunsplit
import xmlrpclib

from bzrlib import (
    debug,
    errors,
    trace,
    urlutils,
    )
from bzrlib.transport import (
    get_transport,
    register_urlparse_netloc_protocol,
    Transport,
    )

from bzrlib.plugins.launchpad.lp_registration import (
    LaunchpadService, ResolveLaunchpadPathRequest)
from bzrlib.plugins.launchpad.account import get_lp_login


# As bzrlib.transport.remote may not be loaded yet, make sure bzr+ssh
# is counted as a netloc protocol.
register_urlparse_netloc_protocol('bzr+ssh')
register_urlparse_netloc_protocol('lp')


class LaunchpadDirectory(object):

    def _requires_launchpad_login(self, scheme, netloc, path, query,
                                  fragment):
        """Does the URL require a Launchpad login in order to be reached?

        The URL is specified by its parsed components, as returned from
        urlsplit.
        """
        return (scheme in ('bzr+ssh', 'sftp')
                and (netloc.endswith('launchpad.net')
                     or netloc.endswith('launchpad.dev')))

    def look_up(self, name, url):
        return self._resolve(url)

    def _resolve(self, abspath,
                 _request_factory=ResolveLaunchpadPathRequest,
                 _lp_login=None):
        """Resolve the base URL for this transport."""
        result = urlsplit(abspath)
        # Perform an XMLRPC request to resolve the path
        lp_instance = result.netloc
        if lp_instance == '':
            lp_instance = None
        elif lp_instance not in LaunchpadService.LAUNCHPAD_INSTANCE:
            raise errors.InvalidURL(path=abspath)
        resolve = _request_factory(result.path.strip('/'))
        service = LaunchpadService(lp_instance=lp_instance)
        try:
            result = resolve.submit(service)
        except xmlrpclib.Fault, fault:
            raise errors.InvalidURL(
                path=abspath, extra=fault.faultString)

        if 'launchpad' in debug.debug_flags:
            trace.mutter("resolve_lp_path(%r) == %r", path, result)

        if _lp_login is None:
            _lp_login = get_lp_login()
        for url in result['urls']:
            scheme, netloc, path, query, fragment = urlsplit(url)
            if self._requires_launchpad_login(scheme, netloc, path, query,
                                              fragment):
                # Only accept launchpad.net bzr+ssh URLs if we know
                # the user's Launchpad login:
                if _lp_login is None:
                    continue
                url = urlunsplit((scheme, '%s@%s' % (_lp_login, netloc),
                                  path, query, fragment))
                break
            else:
                # Use the URL if we can create a transport for it.
                try:
                    get_transport(url)
                except (errors.PathError, errors.TransportError):
                    pass
                else:
                    break
        else:
            raise errors.InvalidURL(path=abspath,
                                    extra='no supported schemes')
        return url


def get_test_permutations():
    # Since this transport doesn't do anything once opened, it's not subjected
    # to the usual transport tests.
    return []
