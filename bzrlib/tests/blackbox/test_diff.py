# Copyright (C) 2006-2012 Canonical Ltd
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


"""Black-box tests for bzr diff."""

import os
import re

from bzrlib import (
    tests,
    workingtree,
    )
from bzrlib.diff import (
    DiffTree,
    format_registry as diff_format_registry,
    )
from bzrlib.tests import (
    features,
    )


def subst_dates(string):
    """Replace date strings with constant values."""
    return re.sub(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [-\+]\d{4}',
                  'YYYY-MM-DD HH:MM:SS +ZZZZ', string)


class DiffBase(tests.TestCaseWithTransport):
    """Base class with common setup method"""

    def make_example_branch(self):
        tree = self.make_branch_and_tree('.')
        self.build_tree_contents([
            ('hello', 'foo\n'),
            ('goodbye', 'baz\n')])
        tree.add(['hello'])
        tree.commit('setup')
        tree.add(['goodbye'])
        tree.commit('setup')
        return tree


class TestDiff(DiffBase):

    def test_diff(self):
        tree = self.make_example_branch()
        self.build_tree_contents([('hello', 'hello world!')])
        tree.commit(message='fixing hello')
        output = self.run_bzr('diff -r 2..3', retcode=1)[0]
        self.assert_('\n+hello world!' in output)
        output = self.run_bzr('diff -c 3', retcode=1)[0]
        self.assert_('\n+hello world!' in output)
        output = self.run_bzr('diff -r last:3..last:1', retcode=1)[0]
        self.assert_('\n+baz' in output)
        output = self.run_bzr('diff -c last:2', retcode=1)[0]
        self.assert_('\n+baz' in output)
        self.build_tree(['moo'])
        tree.add('moo')
        os.unlink('moo')
        self.run_bzr('diff')

    def test_diff_prefix(self):
        """diff --prefix appends to filenames in output"""
        self.make_example_branch()
        self.build_tree_contents([('hello', 'hello world!\n')])
        out, err = self.run_bzr('diff --prefix old/:new/', retcode=1)
        self.assertEquals(err, '')
        self.assertEqualDiff(subst_dates(out), '''\
=== modified file 'hello'
--- old/hello\tYYYY-MM-DD HH:MM:SS +ZZZZ
+++ new/hello\tYYYY-MM-DD HH:MM:SS +ZZZZ
@@ -1,1 +1,1 @@
-foo
+hello world!

''')

    def test_diff_illegal_prefix_value(self):
        # There was an error in error reporting for this option
        out, err = self.run_bzr('diff --prefix old/', retcode=3)
        self.assertContainsRe(err,
            '--prefix expects two values separated by a colon')

    def test_diff_p1(self):
        """diff -p1 produces lkml-style diffs"""
        self.make_example_branch()
        self.build_tree_contents([('hello', 'hello world!\n')])
        out, err = self.run_bzr('diff -p1', retcode=1)
        self.assertEquals(err, '')
        self.assertEqualDiff(subst_dates(out), '''\
=== modified file 'hello'
--- old/hello\tYYYY-MM-DD HH:MM:SS +ZZZZ
+++ new/hello\tYYYY-MM-DD HH:MM:SS +ZZZZ
@@ -1,1 +1,1 @@
-foo
+hello world!

''')

    def test_diff_p0(self):
        """diff -p0 produces diffs with no prefix"""
        self.make_example_branch()
        self.build_tree_contents([('hello', 'hello world!\n')])
        out, err = self.run_bzr('diff -p0', retcode=1)
        self.assertEquals(err, '')
        self.assertEqualDiff(subst_dates(out), '''\
=== modified file 'hello'
--- hello\tYYYY-MM-DD HH:MM:SS +ZZZZ
+++ hello\tYYYY-MM-DD HH:MM:SS +ZZZZ
@@ -1,1 +1,1 @@
-foo
+hello world!

''')

    def test_diff_nonexistent(self):
        # Get an error from a file that does not exist at all
        # (Malone #3619)
        self.make_example_branch()
        out, err = self.run_bzr('diff does-not-exist', retcode=3,
            error_regexes=('not versioned.*does-not-exist',))

    def test_diff_illegal_revision_specifiers(self):
        out, err = self.run_bzr('diff -r 1..23..123', retcode=3,
            error_regexes=('one or two revision specifiers',))

    def test_diff_using_and_format(self):
        out, err = self.run_bzr('diff --format=default --using=mydi', retcode=3,
            error_regexes=('are mutually exclusive',))

    def test_diff_nonexistent_revision(self):
        out, err = self.run_bzr('diff -r 123', retcode=3,
            error_regexes=("Requested revision: '123' does not "
                "exist in branch:",))

    def test_diff_nonexistent_dotted_revision(self):
        out, err = self.run_bzr('diff -r 1.1', retcode=3)
        self.assertContainsRe(err,
            "Requested revision: '1.1' does not exist in branch:")

    def test_diff_nonexistent_dotted_revision_change(self):
        out, err = self.run_bzr('diff -c 1.1', retcode=3)
        self.assertContainsRe(err,
            "Requested revision: '1.1' does not exist in branch:")

    def test_diff_unversioned(self):
        # Get an error when diffing a non-versioned file.
        # (Malone #3619)
        self.make_example_branch()
        self.build_tree(['unversioned-file'])
        out, err = self.run_bzr('diff unversioned-file', retcode=3)
        self.assertContainsRe(err, 'not versioned.*unversioned-file')

    # TODO: What should diff say for a file deleted in working tree?

    def example_branches(self):
        branch1_tree = self.make_branch_and_tree('branch1')
        self.build_tree(['branch1/file'], line_endings='binary')
        self.build_tree(['branch1/file2'], line_endings='binary')
        branch1_tree.add('file')
        branch1_tree.add('file2')
        branch1_tree.commit(message='add file and file2')
        branch2_tree = branch1_tree.bzrdir.sprout('branch2').open_workingtree()
        self.build_tree_contents([('branch2/file', 'new content\n')])
        branch2_tree.commit(message='update file')
        return branch1_tree, branch2_tree

    def check_b2_vs_b1(self, cmd):
        # Compare branch2 vs branch1 using cmd and check the result
        out, err = self.run_bzr(cmd, retcode=1)
        self.assertEquals('', err)
        self.assertEquals("=== modified file 'file'\n"
                          "--- file\tYYYY-MM-DD HH:MM:SS +ZZZZ\n"
                          "+++ file\tYYYY-MM-DD HH:MM:SS +ZZZZ\n"
                          "@@ -1,1 +1,1 @@\n"
                          "-new content\n"
                          "+contents of branch1/file\n"
                          "\n", subst_dates(out))

    def check_b1_vs_b2(self, cmd):
        # Compare branch1 vs branch2 using cmd and check the result
        out, err = self.run_bzr(cmd, retcode=1)
        self.assertEquals('', err)
        self.assertEqualDiff("=== modified file 'file'\n"
                              "--- file\tYYYY-MM-DD HH:MM:SS +ZZZZ\n"
                              "+++ file\tYYYY-MM-DD HH:MM:SS +ZZZZ\n"
                              "@@ -1,1 +1,1 @@\n"
                              "-contents of branch1/file\n"
                              "+new content\n"
                              "\n", subst_dates(out))

    def check_no_diffs(self, cmd):
        # Check that running cmd returns an empty diff
        out, err = self.run_bzr(cmd, retcode=0)
        self.assertEquals('', err)
        self.assertEquals('', out)

    def test_diff_branches(self):
        self.example_branches()
        # should open branch1 and diff against branch2,
        self.check_b2_vs_b1('diff -r branch:branch2 branch1')
        # Compare two working trees using various syntax forms
        self.check_b2_vs_b1('diff --old branch2 --new branch1')
        self.check_b2_vs_b1('diff --old branch2 branch1')
        self.check_b2_vs_b1('diff branch2 --new branch1')
        # Test with a selected file that was changed
        self.check_b2_vs_b1('diff --old branch2 --new branch1 file')
        self.check_b2_vs_b1('diff --old branch2 branch1/file')
        self.check_b2_vs_b1('diff branch2/file --new branch1')
        # Test with a selected file that was not changed
        self.check_no_diffs('diff --old branch2 --new branch1 file2')
        self.check_no_diffs('diff --old branch2 branch1/file2')
        self.check_no_diffs('diff branch2/file2 --new branch1')

    def test_diff_branches_no_working_trees(self):
        branch1_tree, branch2_tree = self.example_branches()
        # Compare a working tree to a branch without a WT
        dir1 = branch1_tree.bzrdir
        dir1.destroy_workingtree()
        self.assertFalse(dir1.has_workingtree())
        self.check_b2_vs_b1('diff --old branch2 --new branch1')
        self.check_b2_vs_b1('diff --old branch2 branch1')
        self.check_b2_vs_b1('diff branch2 --new branch1')
        # Compare a branch without a WT to one with a WT
        self.check_b1_vs_b2('diff --old branch1 --new branch2')
        self.check_b1_vs_b2('diff --old branch1 branch2')
        self.check_b1_vs_b2('diff branch1 --new branch2')
        # Compare a branch with a WT against another without a WT
        dir2 = branch2_tree.bzrdir
        dir2.destroy_workingtree()
        self.assertFalse(dir2.has_workingtree())
        self.check_b1_vs_b2('diff --old branch1 --new branch2')
        self.check_b1_vs_b2('diff --old branch1 branch2')
        self.check_b1_vs_b2('diff branch1 --new branch2')

    def test_diff_revno_branches(self):
        self.example_branches()
        branch2_tree = workingtree.WorkingTree.open_containing('branch2')[0]
        self.build_tree_contents([('branch2/file', 'even newer content')])
        branch2_tree.commit(message='update file once more')

        out, err = self.run_bzr('diff -r revno:1:branch2..revno:1:branch1',
                                )
        self.assertEquals('', err)
        self.assertEquals('', out)
        out, err = self.run_bzr('diff -r revno:2:branch2..revno:1:branch1',
                                retcode=1)
        self.assertEquals('', err)
        self.assertEqualDiff("=== modified file 'file'\n"
                              "--- file\tYYYY-MM-DD HH:MM:SS +ZZZZ\n"
                              "+++ file\tYYYY-MM-DD HH:MM:SS +ZZZZ\n"
                              "@@ -1,1 +1,1 @@\n"
                              "-new content\n"
                              "+contents of branch1/file\n"
                              "\n", subst_dates(out))

    def example_branch2(self):
        branch1_tree = self.make_branch_and_tree('branch1')
        self.build_tree_contents([('branch1/file1', 'original line\n')])
        branch1_tree.add('file1')
        branch1_tree.commit(message='first commit')
        self.build_tree_contents([('branch1/file1', 'repo line\n')])
        branch1_tree.commit(message='second commit')
        return branch1_tree

    def test_diff_to_working_tree(self):
        self.example_branch2()
        self.build_tree_contents([('branch1/file1', 'new line')])
        output = self.run_bzr('diff -r 1.. branch1', retcode=1)
        self.assertContainsRe(output[0], '\n\\-original line\n\\+new line\n')

    def test_diff_to_working_tree_in_subdir(self):
        self.example_branch2()
        self.build_tree_contents([('branch1/file1', 'new line')])
        os.mkdir('branch1/dir1')
        output = self.run_bzr('diff -r 1..', retcode=1,
                              working_dir='branch1/dir1')
        self.assertContainsRe(output[0], '\n\\-original line\n\\+new line\n')

    def test_diff_across_rename(self):
        """The working tree path should always be considered for diffing"""
        tree = self.make_example_branch()
        self.run_bzr('diff -r 0..1 hello', retcode=1)
        tree.rename_one('hello', 'hello1')
        self.run_bzr('diff hello1', retcode=1)
        self.run_bzr('diff -r 0..1 hello1', retcode=1)

    def test_diff_to_branch_no_working_tree(self):
        branch1_tree = self.example_branch2()
        dir1 = branch1_tree.bzrdir
        dir1.destroy_workingtree()
        self.assertFalse(dir1.has_workingtree())
        output = self.run_bzr('diff -r 1.. branch1', retcode=1)
        self.assertContainsRe(output[0], '\n\\-original line\n\\+repo line\n')

    def test_custom_format(self):
        class BooDiffTree(DiffTree):

            def show_diff(self, specific_files, extra_trees=None):
                self.to_file.write("BOO!\n")
                return super(BooDiffTree, self).show_diff(specific_files,
                    extra_trees)

        diff_format_registry.register("boo", BooDiffTree, "Scary diff format")
        self.addCleanup(diff_format_registry.remove, "boo")
        self.make_example_branch()
        self.build_tree_contents([('hello', 'hello world!\n')])
        output = self.run_bzr('diff --format=boo', retcode=1)
        self.assertTrue("BOO!" in output[0])
        output = self.run_bzr('diff -Fboo', retcode=1)
        self.assertTrue("BOO!" in output[0])


class TestCheckoutDiff(TestDiff):

    def make_example_branch(self):
        tree = super(TestCheckoutDiff, self).make_example_branch()
        tree = tree.branch.create_checkout('checkout')
        os.chdir('checkout')
        return tree

    def example_branch2(self):
        tree = super(TestCheckoutDiff, self).example_branch2()
        os.mkdir('checkouts')
        tree = tree.branch.create_checkout('checkouts/branch1')
        os.chdir('checkouts')
        return tree

    def example_branches(self):
        branch1_tree, branch2_tree = super(TestCheckoutDiff,
                                           self).example_branches()
        os.mkdir('checkouts')
        branch1_tree = branch1_tree.branch.create_checkout('checkouts/branch1')
        branch2_tree = branch2_tree.branch.create_checkout('checkouts/branch2')
        os.chdir('checkouts')
        return branch1_tree, branch2_tree


class TestDiffLabels(DiffBase):

    def test_diff_label_removed(self):
        tree = super(TestDiffLabels, self).make_example_branch()
        tree.remove('hello', keep_files=False)
        diff = self.run_bzr('diff', retcode=1)
        self.assertTrue("=== removed file 'hello'" in diff[0])

    def test_diff_label_added(self):
        tree = super(TestDiffLabels, self).make_example_branch()
        self.build_tree_contents([('barbar', 'barbar')])
        tree.add('barbar')
        diff = self.run_bzr('diff', retcode=1)
        self.assertTrue("=== added file 'barbar'" in diff[0])

    def test_diff_label_modified(self):
        super(TestDiffLabels, self).make_example_branch()
        self.build_tree_contents([('hello', 'barbar')])
        diff = self.run_bzr('diff', retcode=1)
        self.assertTrue("=== modified file 'hello'" in diff[0])

    def test_diff_label_renamed(self):
        tree = super(TestDiffLabels, self).make_example_branch()
        tree.rename_one('hello', 'gruezi')
        diff = self.run_bzr('diff', retcode=1)
        self.assertTrue("=== renamed file 'hello' => 'gruezi'" in diff[0])


class TestExternalDiff(DiffBase):

    def test_external_diff(self):
        """Test that we can spawn an external diff process"""
        self.disable_missing_extensions_warning()
        # We have to use run_bzr_subprocess, because we need to
        # test writing directly to stdout, (there was a bug in
        # subprocess.py that we had to workaround).
        # However, if 'diff' may not be available
        self.make_example_branch()
        out, err = self.run_bzr_subprocess(
            'diff -Oprogress_bar=none -r 1 --diff-options -ub',
            universal_newlines=True,
            retcode=None)
        if 'Diff is not installed on this machine' in err:
            raise tests.TestSkipped("No external 'diff' is available")
        self.assertEqual('', err)
        # We have to skip the stuff in the middle, because it depends
        # on time.time()
        self.assertStartsWith(out, "=== added file 'goodbye'\n"
                                   "--- goodbye\t1970-01-01 00:00:00 +0000\n"
                                   "+++ goodbye\t")
        self.assertEndsWith(out, "\n@@ -0,0 +1 @@\n"
                                 "+baz\n\n")

    def test_external_diff_options_and_using(self):
        """Test that the options are passed correctly to an external diff process"""
        self.requireFeature(features.diff_feature)
        self.make_example_branch()
        self.build_tree_contents([('hello', 'Foo\n')])
        out, err = self.run_bzr('diff --diff-options -i --using diff',
                                    retcode=1)
        self.assertEquals("=== modified file 'hello'\n", out)
        self.assertEquals('', err)


class TestDiffOutput(DiffBase):

    def test_diff_output(self):
        # check that output doesn't mangle line-endings
        self.make_example_branch()
        self.build_tree_contents([('hello', 'hello world!\n')])
        output = self.run_bzr_subprocess('diff', retcode=1)[0]
        self.assert_('\n+hello world!\n' in output)
