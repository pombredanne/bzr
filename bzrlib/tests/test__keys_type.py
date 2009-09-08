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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

"""Tests for the Keys type."""

from bzrlib import (
    # _keys_py,
    errors,
    tests,
    )


def load_tests(standard_tests, module, loader):
    """Parameterize tests for all versions of groupcompress."""
    scenarios = [
    #    ('python', {'module': _keys_py}),
    ]
    suite = loader.suiteClass()
    if CompiledKeysType.available():
        from bzrlib import _keys_type_c
        scenarios.append(('C', {'module': _keys_type_c}))
    else:
        # the compiled module isn't available, so we add a failing test
        class FailWithoutFeature(tests.TestCase):
            def test_fail(self):
                self.requireFeature(CompiledKeysType)
        suite.addTest(loader.loadTestsFromTestCase(FailWithoutFeature))
    result = tests.multiply_tests(standard_tests, scenarios, suite)
    return result


class _CompiledKeysType(tests.Feature):

    def _probe(self):
        try:
            import bzrlib._keys_type_c
        except ImportError:
            return False
        return True

    def feature_name(self):
        return 'bzrlib._keys_type_c'

CompiledKeysType = _CompiledKeysType()


class TestKeysType(tests.TestCase):

    def test_create(self):
        t = self.module.Keys(1, 'foo', 'bar')

    def test_create_bad_args(self):
        self.assertRaises(TypeError, self.module.Keys)
        self.assertRaises(TypeError, self.module.Keys, 'foo')
        self.assertRaises(ValueError, self.module.Keys, 0)
        self.assertRaises(ValueError, self.module.Keys, -1)
        self.assertRaises(ValueError, self.module.Keys, -200)
        self.assertRaises(ValueError, self.module.Keys, 2, 'foo')
        self.assertRaises(ValueError, self.module.Keys, 257)
        lots_of_args = ['a']*300
        # too many args
        self.assertRaises(ValueError, self.module.Keys, 1, *lots_of_args)
        self.assertRaises(TypeError, self.module.Keys, 1, 'foo', 10)
