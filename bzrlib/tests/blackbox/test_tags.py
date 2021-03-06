# Copyright (C) 2007-2011 Canonical Ltd
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

"""Tests for commands related to tags"""

from bzrlib import (
    errors,
    tag,
    transform,
    )
from bzrlib.branch import (
    Branch,
    )
from bzrlib.tests import (
    script,
    TestCaseWithTransport,
    )
from bzrlib.tests.matchers import ContainsNoVfsCalls
from bzrlib.workingtree import WorkingTree


class TestTagging(TestCaseWithTransport):

    def test_tag_command_help(self):
        out, err = self.run_bzr('help tag')
        self.assertContainsRe(out, 'Create, remove or modify a tag')

    def test_cannot_tag_range(self):
        out, err = self.run_bzr('tag -r1..10 name', retcode=3)
        self.assertContainsRe(err,
            "Tags can only be placed on a single revision")

    def test_no_tag_name(self):
        out, err = self.run_bzr('tag -d branch', retcode=3)
        self.assertContainsRe(err, 'Please specify a tag name.')

    def test_automatic_tag_name(self):
        def get_tag_name(branch, revid):
            return "mytag"
        Branch.hooks.install_named_hook('automatic_tag_name',
            get_tag_name, 'get tag name')
        out, err = self.run_bzr('tag -d branch')
        self.assertContainsRe(err, 'Created tag mytag.')

    def test_tag_current_rev(self):
        t = self.make_branch_and_tree('branch')
        t.commit(allow_pointless=True, message='initial commit',
            rev_id='first-revid')
        # make a tag through the command line
        out, err = self.run_bzr('tag -d branch NEWTAG')
        self.assertContainsRe(err, 'Created tag NEWTAG.')
        # tag should be observable through the api
        self.assertEquals(t.branch.tags.get_tag_dict(),
                dict(NEWTAG='first-revid'))
        # can also create tags using -r
        self.run_bzr('tag -d branch tag2 -r1')
        self.assertEquals(t.branch.tags.lookup_tag('tag2'), 'first-revid')
        # regression test: make sure a unicode revision from the user
        # gets turned into a str object properly. The use of a unicode
        # object for the revid is intentional.
        self.run_bzr(['tag', '-d', 'branch', 'tag3', u'-rrevid:first-revid'])
        self.assertEquals(t.branch.tags.lookup_tag('tag3'), 'first-revid')
        # can also delete an existing tag
        out, err = self.run_bzr('tag --delete -d branch tag2')
        # cannot replace an existing tag normally
        out, err = self.run_bzr('tag -d branch NEWTAG -r0', retcode=3)
        self.assertContainsRe(err, 'Tag NEWTAG already exists\\.')
        # ... but can if you use --force
        out, err = self.run_bzr('tag -d branch NEWTAG --force -r0')
        self.assertEquals("Updated tag NEWTAG.\n", err)

    def test_tag_same_revision(self):
        t = self.make_branch_and_tree('branch')
        t.commit(allow_pointless=True, message='initial commit',
            rev_id='first-revid')
        t.commit(allow_pointless=True, message='second commit',
            rev_id='second-revid')
        out, err = self.run_bzr('tag -rrevid:first-revid -d branch NEWTAG')
        out, err = self.run_bzr('tag -rrevid:first-revid -d branch NEWTAG')
        self.assertContainsRe(err,
            'Tag NEWTAG already exists for that revision\\.')
        out, err = self.run_bzr('tag -rrevid:second-revid -d branch NEWTAG',
            retcode=3)
        self.assertContainsRe(err, 'Tag NEWTAG already exists\\.')

    def test_tag_delete_requires_name(self):
        out, err = self.run_bzr('tag -d branch', retcode=3)
        self.assertContainsRe(err, 'Please specify a tag name\\.')

    def test_branch_push_pull_merge_copies_tags(self):
        t = self.make_branch_and_tree('branch1')
        t.commit(allow_pointless=True, message='initial commit',
            rev_id='first-revid')
        b1 = t.branch
        b1.tags.set_tag('tag1', 'first-revid')
        # branching copies the tag across
        self.run_bzr('branch branch1 branch2')
        b2 = Branch.open('branch2')
        self.assertEquals(b2.tags.lookup_tag('tag1'), 'first-revid')
        # make a new tag and pull it
        b1.tags.set_tag('tag2', 'twa')
        self.run_bzr('pull -d branch2 branch1')
        self.assertEquals(b2.tags.lookup_tag('tag2'), 'twa')
        # make a new tag and push it
        b1.tags.set_tag('tag3', 'san')
        self.run_bzr('push -d branch1 branch2')
        self.assertEquals(b2.tags.lookup_tag('tag3'), 'san')
        # make a new tag and merge it
        t.commit(allow_pointless=True, message='second commit',
            rev_id='second-revid')
        t2 = WorkingTree.open('branch2')
        t2.commit(allow_pointless=True, message='commit in second')
        b1.tags.set_tag('tag4', 'second-revid')
        self.run_bzr('merge -d branch2 branch1')
        self.assertEquals(b2.tags.lookup_tag('tag4'), 'second-revid')
        # pushing to a new location copies the tag across
        self.run_bzr('push -d branch1 branch3')
        b3 = Branch.open('branch3')
        self.assertEquals(b3.tags.lookup_tag('tag1'), 'first-revid')

    def make_master_and_checkout(self):
        builder = self.make_branch_builder('master')
        builder.build_commit(message='Initial commit.', rev_id='rev-1')
        master = builder.get_branch()
        child = master.create_checkout(self.get_url('child'))
        return master, child

    def make_fork(self, branch):
        fork = branch.create_clone_on_transport(self.get_transport('fork'))
        self.addCleanup(fork.lock_write().unlock)
        with transform.TransformPreview(fork.basis_tree()) as tt:
            tt.commit(fork, message='Commit in fork.', revision_id='fork-0')
        with transform.TransformPreview(fork.basis_tree()) as tt:
            tt.commit(fork, message='Commit in fork.', revision_id='fork-1')
        return fork

    def test_merge_without_commit_does_not_propagate_tags_to_master(self):
        """'bzr merge' alone does not propagate tags to a master branch.

        (If the user runs 'bzr commit', then that is when the tags from the
        merge are propagated.)
        """
        master, child = self.make_master_and_checkout()
        fork = self.make_fork(master)
        fork.tags.set_tag('new-tag', fork.last_revision())
        self.run_bzr(['merge', '../fork'], working_dir='child')
        self.assertEqual({}, master.tags.get_tag_dict())

    def test_commit_in_heavyweight_checkout_copies_tags_to_master(self):
        master, child = self.make_master_and_checkout()
        fork = self.make_fork(master)
        fork.tags.set_tag('new-tag', fork.last_revision())
        fork.tags.set_tag('non-ancestry-tag', 'fork-0')
        fork.tags.set_tag('absent-tag', 'absent-rev')
        script.run_script(self, """
            $ cd child
            $ bzr merge ../fork
            $ bzr commit -m "Merge fork."
            2>Committing to: .../master/
            2>Committed revision 2.
            """, null_output_matches_anything=True)
        # Merge copied the tag to child and commit propagated it to master
        expected_tag_dict = {
            'new-tag': fork.last_revision(),
            'non-ancestry-tag': 'fork-0',
            'absent-tag': 'absent-rev',
            }
        self.assertEqual(expected_tag_dict, child.branch.tags.get_tag_dict())
        self.assertEqual(expected_tag_dict, master.tags.get_tag_dict())
        # Revisions not in ancestry but named in tags are present
        child.branch.repository.get_revision('fork-0')
        master.repository.get_revision('fork-0')

    def test_commit_in_heavyweight_checkout_reports_tag_conflict(self):
        master, child = self.make_master_and_checkout()
        fork = self.make_fork(master)
        fork.tags.set_tag('new-tag', fork.last_revision())
        master_r1 = master.last_revision()
        master.tags.set_tag('new-tag', master_r1)
        script.run_script(self, """
            $ cd child
            $ bzr merge ../fork
            $ bzr commit -m "Merge fork."
            2>Committing to: .../master/
            2>Conflicting tags in bound branch:
            2>    new-tag
            2>Committed revision 2.
            """, null_output_matches_anything=True)
        # Merge copied the tag to child.  master's conflicting tag is unchanged.
        self.assertEqual(
            {'new-tag': fork.last_revision()}, child.branch.tags.get_tag_dict())
        self.assertEqual(
            {'new-tag': master_r1}, master.tags.get_tag_dict())

    def test_list_tags(self):
        tree1 = self.make_branch_and_tree('branch1')
        tree1.commit(allow_pointless=True, message='revision 1',
                rev_id='revid-1', timestamp=10)
        tree1.commit(allow_pointless=True, message='revision 2',
                rev_id='revid-2', timestamp=15)

        b1 = tree1.branch
        # note how the tag for revid-1 sorts after the one for revid-2
        b1.tags.set_tag(u'tag1\u30d0', 'revid-2')
        b1.tags.set_tag(u'tag10\u30d0', 'missing') # not present in repository
        b1.tags.set_tag(u'tag2\u30d0', 'revid-1')

        # natural order
        out, err = self.run_bzr('tags -d branch1',
                                encoding='utf-8')
        self.assertEquals(err, '')
        self.assertContainsRe(out, (u'^tag1\u30d0  *2\ntag2\u30d0  *1\n' +
            u'tag10\u30d0 *\\?\n').encode('utf-8'))

        # lexicographical order
        out, err = self.run_bzr('tags --sort=alpha -d branch1',
                                encoding='utf-8')
        self.assertEquals(err, '')
        self.assertContainsRe(out, (u'^tag10\u30d0  *\\?\ntag1\u30d0  *2\n' +
            u'tag2\u30d0 *1\n').encode('utf-8'))

        out, err = self.run_bzr('tags --sort=alpha --show-ids -d branch1',
                                encoding='utf-8')
        self.assertEquals(err, '')
        self.assertContainsRe(out, (u'^tag10\u30d0  *missing\n' +
            u'tag1\u30d0  *revid-2\ntag2\u30d0 *revid-1\n').encode('utf-8'))

        # chronological order
        out, err = self.run_bzr('tags --sort=time -d branch1',
                encoding='utf-8')
        self.assertEquals(err, '')
        self.assertContainsRe(out, (u'^tag2\u30d0  *1\ntag1\u30d0  *2\n' +
            u'tag10\u30d0 *\\?\n').encode('utf-8'))

        out, err = self.run_bzr('tags --sort=time --show-ids -d branch1',
                encoding='utf-8')
        self.assertEquals(err, '')
        self.assertContainsRe(out, (u'^tag2\u30d0  *revid-1\n' +
            u'tag1\u30d0  *revid-2\ntag10\u30d0 *missing\n').encode('utf-8'))

        # now test dotted revnos
        tree2 = tree1.bzrdir.sprout('branch2').open_workingtree()
        tree1.commit(allow_pointless=True, message='revision 3 in branch1',
                rev_id='revid-3a')
        tree2.commit(allow_pointless=True, message='revision 3 in branch2',
                rev_id='revid-3b')

        b2 = tree2.branch
        b2.tags.set_tag('tagD', 'revid-3b')
        self.run_bzr('merge -d branch1 branch2')
        tree1.commit('merge', rev_id='revid-4')

        out, err = self.run_bzr('tags -d branch1', encoding='utf-8')
        self.assertEquals(err, '')
        self.assertContainsRe(out, r'tagD  *2\.1\.1\n')
        out, err = self.run_bzr('tags -d branch2', encoding='utf-8')
        self.assertEquals(err, '')
        self.assertContainsRe(out, r'tagD  *3\n')

    def test_list_tags_dotted_revnos_unsupported(self):
        tree = self.make_branch_and_tree('branch')
        rev1 = tree.commit("rev1")
        tree.branch.tags.set_tag("mytag", rev1)
        def revision_id_to_dotted_revno(self, revid):
            raise errors.UnsupportedOperation(revision_id_to_dotted_revno, self)
        self.overrideAttr(Branch, "revision_id_to_dotted_revno",
            revision_id_to_dotted_revno)
        out, err = self.run_bzr('tags -d branch', encoding='utf-8')
        self.assertEquals(out, 'mytag                ?\n')

    def test_list_tags_revision_filtering(self):
        tree1 = self.make_branch_and_tree('.')
        tree1.commit(allow_pointless=True, message='revision 1',
                rev_id='revid-1')
        tree1.commit(allow_pointless=True, message='revision 2',
                rev_id='revid-2')
        tree1.commit(allow_pointless=True, message='revision 3',
                rev_id='revid-3')
        tree1.commit(allow_pointless=True, message='revision 4',
                rev_id='revid-4')
        b1 = tree1.branch
        b1.tags.set_tag(u'tag 1', 'revid-1')
        b1.tags.set_tag(u'tag 2', 'revid-2')
        b1.tags.set_tag(u'tag 3', 'revid-3')
        b1.tags.set_tag(u'tag 4', 'revid-4')
        self._check_tag_filter('', (1, 2, 3, 4))
        self._check_tag_filter('-r ..', (1, 2, 3, 4))
        self._check_tag_filter('-r ..2', (1, 2))
        self._check_tag_filter('-r 2..', (2, 3, 4))
        self._check_tag_filter('-r 2..3', (2, 3))
        self._check_tag_filter('-r 3..2', ())
        self.run_bzr_error(args="tags -r 123",
            error_regexes=["bzr: ERROR: Requested revision: '123' "
                "does not exist in branch:"])
        self.run_bzr_error(args="tags -r ..123",
            error_regexes=["bzr: ERROR: Requested revision: '123' "
                "does not exist in branch:"])
        self.run_bzr_error(args="tags -r 123.123",
            error_regexes=["bzr: ERROR: Requested revision: '123.123' "
                "does not exist in branch:"])

    def test_sort_tags_custom(self):
        def sort_by_dots(branch, tags):
            def sort_key((tag, revid)):
                return tag.count(".")
            tags.sort(key=sort_key)

        # Register a custom sort method
        tag.tag_sort_methods.register("dots", sort_by_dots, "Sort by dots.")
        self.addCleanup(tag.tag_sort_methods.remove, "dots")

        tree1 = self.make_branch_and_tree('branch1')
        tree1.commit(allow_pointless=True, message='revision 1',
                rev_id='revid-1', timestamp=10)
        tree1.commit(allow_pointless=True, message='revision 2',
                rev_id='revid-2', timestamp=15)

        b1 = tree1.branch

        b1.tags.set_tag(u'tag..', 'revid-2')
        b1.tags.set_tag(u'tag....', 'missing') # not present in repository
        b1.tags.set_tag(u'tag.', 'revid-1')
        b1.tags.set_tag(u'tag...', 'revid-1')
        b1.tags.set_tag(u'tag....', 'revid-1')

        # sorted by number of dots
        out, err = self.run_bzr('tags --sort=dots -d branch1')
        self.assertEquals(err, '')
        self.assertEquals([
            'tag.                 1',
            'tag..                2',
            'tag...               1',
            'tag....              1'],
            out.splitlines())

    def _check_tag_filter(self, argstr, expected_revnos):
        #upper bound of laziness
        out, err = self.run_bzr('tags ' + argstr)
        self.assertEquals(err, '')
        self.assertContainsRe(out, "^" + ''.join(["tag %s +%s\n" % (
            revno, revno) for revno in expected_revnos]) + "$")

    def test_conflicting_tags(self):
        # setup two empty branches with different tags
        t1 = self.make_branch_and_tree('one')
        t2 = self.make_branch_and_tree('two')
        b1 = t1.branch
        b2 = t2.branch
        tagname = u'\u30d0zaar'
        b1.tags.set_tag(tagname, 'revid1')
        b2.tags.set_tag(tagname, 'revid2')
        # push should give a warning about the tags
        out, err = self.run_bzr('push -d one two', encoding='utf-8')
        self.assertContainsRe(out,
                'Conflicting tags:\n.*' + tagname.encode('utf-8'))
        # pull should give a warning about the tags
        out, err = self.run_bzr('pull -d one two', encoding='utf-8',
            retcode=1)
        self.assertContainsRe(out,
                'Conflicting tags:\n.*' + tagname.encode('utf-8'))
        # merge should give a warning about the tags -- not implemented yet
        ## out, err = self.run_bzr('merge -d one two', encoding='utf-8')
        ## self.assertContainsRe(out,
        ##         'Conflicting tags:\n.*' + tagname.encode('utf-8'))

    def test_tag_quiet(self):
        t1 = self.make_branch_and_tree('')
        out, err = self.run_bzr('tag --quiet test1')
        self.assertEqual('', out)
        self.assertEqual('', err)

    def test_tag_delete_quiet(self):
        t1 = self.make_branch_and_tree('')
        self.run_bzr('tag test1')
        out, err = self.run_bzr('tag --delete --quiet test1')
        self.assertEqual('', out)
        self.assertEqual('', err)

    def test_tags_with_mainline_ghosts(self):
        tree = self.make_branch_and_tree('tree1')
        tree.set_parent_ids(["spooky"], allow_leftmost_as_ghost=True)
        tree.add('')
        tree.commit('msg1', rev_id='rev1')
        tree.commit('msg2', rev_id='rev2')
        tree.branch.tags.set_tag('unknown', 'out-of-mainline')
        tree.branch.tags.set_tag('ghost', 'spooky')
        tree.branch.tags.set_tag('tag1', 'rev1')
        tree.branch.tags.set_tag('tag2', 'rev2')

        out, err = self.run_bzr('tags -d tree1', encoding='utf-8')
        self.assertEqual(out,
            'ghost                ?\n'
            'tag1                 1\n'
            'tag2                 2\n'
            'unknown              ?\n')
        self.assertEqual('', err)


class TestSmartServerCat(TestCaseWithTransport):

    def test_set_tag(self):
        self.setup_smart_server_with_call_log()
        t = self.make_branch_and_tree('branch')
        self.build_tree_contents([('branch/foo', 'thecontents')])
        t.add("foo")
        t.commit("message")
        self.reset_smart_call_log()
        out, err = self.run_bzr(['tag', "-d", self.get_url('branch'), "tagname"])
        # This figure represent the amount of work to perform this use case. It
        # is entirely ok to reduce this number if a test fails due to rpc_count
        # being too low. If rpc_count increases, more network roundtrips have
        # become necessary for this use case. Please do not adjust this number
        # upwards without agreement from bzr's network support maintainers.
        self.assertLength(9, self.hpss_calls)
        self.assertLength(1, self.hpss_connections)
        self.assertThat(self.hpss_calls, ContainsNoVfsCalls)

    def test_show_tags(self):
        self.setup_smart_server_with_call_log()
        t = self.make_branch_and_tree('branch')
        self.build_tree_contents([('branch/foo', 'thecontents')])
        t.add("foo")
        t.commit("message")
        t.branch.tags.set_tag("sometag", "rev1")
        t.branch.tags.set_tag("sometag", "rev2")
        self.reset_smart_call_log()
        out, err = self.run_bzr(['tags', "-d", self.get_url('branch')])
        # This figure represent the amount of work to perform this use case. It
        # is entirely ok to reduce this number if a test fails due to rpc_count
        # being too low. If rpc_count increases, more network roundtrips have
        # become necessary for this use case. Please do not adjust this number
        # upwards without agreement from bzr's network support maintainers.
        self.assertLength(6, self.hpss_calls)
        self.assertLength(1, self.hpss_connections)
        self.assertThat(self.hpss_calls, ContainsNoVfsCalls)
