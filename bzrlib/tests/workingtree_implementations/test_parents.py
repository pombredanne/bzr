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

"""Tests of the parent related functions of WorkingTrees."""

from errno import EEXIST
import os

from bzrlib import (
    errors,
    osutils,
    revision as _mod_revision,
    symbol_versioning,
    )
from bzrlib.inventory import (
    Inventory,
    InventoryFile,
    InventoryDirectory,
    InventoryLink,
    )
from bzrlib.revision import Revision
from bzrlib.tests import SymlinkFeature, TestNotApplicable
from bzrlib.tests.workingtree_implementations import TestCaseWithWorkingTree
from bzrlib.uncommit import uncommit


class TestParents(TestCaseWithWorkingTree):

    def assertConsistentParents(self, expected, tree):
        """Check that the parents found are as expected.

        This test helper also checks that they are consistent with
        the pre-get_parent_ids() api - which is now deprecated.
        """
        self.assertEqual(expected, tree.get_parent_ids())
        if expected == []:
            self.assertEqual(_mod_revision.NULL_REVISION,
                             _mod_revision.ensure_null(tree.last_revision()))
        else:
            self.assertEqual(expected[0], tree.last_revision())


class TestGetParents(TestParents):

    def test_get_parents(self):
        t = self.make_branch_and_tree('.')
        self.assertEqual([], t.get_parent_ids())


class TestSetParents(TestParents):

    def test_set_no_parents(self):
        t = self.make_branch_and_tree('.')
        t.set_parent_trees([])
        self.assertEqual([], t.get_parent_ids())
        # now give it a real parent, and then set it to no parents again.
        t.commit('first post')
        t.set_parent_trees([])
        self.assertConsistentParents([], t)

    def test_set_null_parent(self):
        t = self.make_branch_and_tree('.')
        self.assertRaises(errors.ReservedId, t.set_parent_ids, ['null:'],
                          allow_leftmost_as_ghost=True)
        self.assertRaises(errors.ReservedId, t.set_parent_trees,
                          [('null:', None)], allow_leftmost_as_ghost=True)

    def test_set_one_ghost_parent_rejects(self):
        t = self.make_branch_and_tree('.')
        self.assertRaises(errors.GhostRevisionUnusableHere,
            t.set_parent_trees, [('missing-revision-id', None)])

    def test_set_one_ghost_parent_force(self):
        t = self.make_branch_and_tree('.')
        t.set_parent_trees([('missing-revision-id', None)],
            allow_leftmost_as_ghost=True)
        self.assertConsistentParents(['missing-revision-id'], t)

    def test_set_two_parents_one_ghost(self):
        t = self.make_branch_and_tree('.')
        revision_in_repo = t.commit('first post')
        # remove the tree's history
        uncommit(t.branch, tree=t)
        rev_tree = t.branch.repository.revision_tree(revision_in_repo)
        t.set_parent_trees([(revision_in_repo, rev_tree),
            ('another-missing', None)])
        self.assertConsistentParents([revision_in_repo, 'another-missing'], t)

    def test_set_three_parents(self):
        t = self.make_branch_and_tree('.')
        first_revision = t.commit('first post')
        uncommit(t.branch, tree=t)
        second_revision = t.commit('second post')
        uncommit(t.branch, tree=t)
        third_revision = t.commit('third post')
        uncommit(t.branch, tree=t)
        rev_tree1 = t.branch.repository.revision_tree(first_revision)
        rev_tree2 = t.branch.repository.revision_tree(second_revision)
        rev_tree3 = t.branch.repository.revision_tree(third_revision)
        t.set_parent_trees([(first_revision, rev_tree1),
            (second_revision, rev_tree2),
            (third_revision, rev_tree3)])
        self.assertConsistentParents(
            [first_revision, second_revision, third_revision], t)

    def test_set_no_parents_ids(self):
        t = self.make_branch_and_tree('.')
        t.set_parent_ids([])
        self.assertEqual([], t.get_parent_ids())
        # now give it a real parent, and then set it to no parents again.
        t.commit('first post')
        t.set_parent_ids([])
        self.assertConsistentParents([], t)

    def test_set_one_ghost_parent_ids_rejects(self):
        t = self.make_branch_and_tree('.')
        self.assertRaises(errors.GhostRevisionUnusableHere,
            t.set_parent_ids, ['missing-revision-id'])

    def test_set_one_ghost_parent_ids_force(self):
        t = self.make_branch_and_tree('.')
        t.set_parent_ids(['missing-revision-id'],
            allow_leftmost_as_ghost=True)
        self.assertConsistentParents(['missing-revision-id'], t)

    def test_set_two_parents_one_ghost_ids(self):
        t = self.make_branch_and_tree('.')
        revision_in_repo = t.commit('first post')
        # remove the tree's history
        uncommit(t.branch, tree=t)
        rev_tree = t.branch.repository.revision_tree(revision_in_repo)
        t.set_parent_ids([revision_in_repo, 'another-missing'])
        self.assertConsistentParents([revision_in_repo, 'another-missing'], t)

    def test_set_three_parents_ids(self):
        t = self.make_branch_and_tree('.')
        first_revision = t.commit('first post')
        uncommit(t.branch, tree=t)
        second_revision = t.commit('second post')
        uncommit(t.branch, tree=t)
        third_revision = t.commit('third post')
        uncommit(t.branch, tree=t)
        rev_tree1 = t.branch.repository.revision_tree(first_revision)
        rev_tree2 = t.branch.repository.revision_tree(second_revision)
        rev_tree3 = t.branch.repository.revision_tree(third_revision)
        t.set_parent_ids([first_revision, second_revision, third_revision])
        self.assertConsistentParents(
            [first_revision, second_revision, third_revision], t)

    def test_set_duplicate_parent_ids(self):
        t = self.make_branch_and_tree('.')
        rev1 = t.commit('first post')
        uncommit(t.branch, tree=t)
        rev2 = t.commit('second post')
        uncommit(t.branch, tree=t)
        rev3 = t.commit('third post')
        uncommit(t.branch, tree=t)
        t.set_parent_ids([rev1, rev2, rev2, rev3])
        # We strip the duplicate, but preserve the ordering
        self.assertConsistentParents([rev1, rev2, rev3], t)

    def test_set_duplicate_parent_trees(self):
        t = self.make_branch_and_tree('.')
        rev1 = t.commit('first post')
        uncommit(t.branch, tree=t)
        rev2 = t.commit('second post')
        uncommit(t.branch, tree=t)
        rev3 = t.commit('third post')
        uncommit(t.branch, tree=t)
        rev_tree1 = t.branch.repository.revision_tree(rev1)
        rev_tree2 = t.branch.repository.revision_tree(rev2)
        rev_tree3 = t.branch.repository.revision_tree(rev3)
        t.set_parent_trees([(rev1, rev_tree1), (rev2, rev_tree2),
                            (rev2, rev_tree2), (rev3, rev_tree3)])
        # We strip the duplicate, but preserve the ordering
        self.assertConsistentParents([rev1, rev2, rev3], t)

    def test_set_parent_ids_in_ancestry(self):
        t = self.make_branch_and_tree('.')
        rev1 = t.commit('first post')
        rev2 = t.commit('second post')
        rev3 = t.commit('third post')
        # Reset the tree, back to rev1
        t.set_parent_ids([rev1])
        t.branch.set_last_revision_info(1, rev1)
        self.assertConsistentParents([rev1], t)
        t.set_parent_ids([rev1, rev2, rev3])
        # rev2 is in the ancestry of rev3, so it will be filtered out
        self.assertConsistentParents([rev1, rev3], t)
        # Order should be preserved, and the first revision should always be
        # kept
        t.set_parent_ids([rev2, rev3, rev1])
        self.assertConsistentParents([rev2, rev3], t)

    def test_set_parent_trees_in_ancestry(self):
        t = self.make_branch_and_tree('.')
        rev1 = t.commit('first post')
        rev2 = t.commit('second post')
        rev3 = t.commit('third post')
        # Reset the tree, back to rev1
        t.set_parent_ids([rev1])
        t.branch.set_last_revision_info(1, rev1)
        self.assertConsistentParents([rev1], t)
        rev_tree1 = t.branch.repository.revision_tree(rev1)
        rev_tree2 = t.branch.repository.revision_tree(rev2)
        rev_tree3 = t.branch.repository.revision_tree(rev3)
        t.set_parent_trees([(rev1, rev_tree1), (rev2, rev_tree2),
                            (rev3, rev_tree3)])
        # rev2 is in the ancestry of rev3, so it will be filtered out
        self.assertConsistentParents([rev1, rev3], t)
        # Order should be preserved, and the first revision should always be
        # kept
        t.set_parent_trees([(rev2, rev_tree2), (rev1, rev_tree1),
                            (rev3, rev_tree3)])
        self.assertConsistentParents([rev2, rev3], t)


class TestAddParent(TestParents):

    def test_add_first_parent_id(self):
        """Test adding the first parent id"""
        tree = self.make_branch_and_tree('.')
        first_revision = tree.commit('first post')
        uncommit(tree.branch, tree=tree)
        tree.add_parent_tree_id(first_revision)
        self.assertConsistentParents([first_revision], tree)
        
    def test_add_first_parent_id_ghost_rejects(self):
        """Test adding the first parent id - as a ghost"""
        tree = self.make_branch_and_tree('.')
        self.assertRaises(errors.GhostRevisionUnusableHere,
            tree.add_parent_tree_id, 'first-revision')
        
    def test_add_first_parent_id_ghost_force(self):
        """Test adding the first parent id - as a ghost"""
        tree = self.make_branch_and_tree('.')
        tree.add_parent_tree_id('first-revision', allow_leftmost_as_ghost=True)
        self.assertConsistentParents(['first-revision'], tree)

    def test_add_second_parent_id_with_ghost_first(self):
        """Test adding the second parent when the first is a ghost."""
        tree = self.make_branch_and_tree('.')
        tree.add_parent_tree_id('first-revision', allow_leftmost_as_ghost=True)
        tree.add_parent_tree_id('second')
        self.assertConsistentParents(['first-revision', 'second'], tree)
        
    def test_add_second_parent_id(self):
        """Test adding the second parent id"""
        tree = self.make_branch_and_tree('.')
        first_revision = tree.commit('first post')
        uncommit(tree.branch, tree=tree)
        second_revision = tree.commit('second post')
        tree.add_parent_tree_id(first_revision)
        self.assertConsistentParents([second_revision, first_revision], tree)
        
    def test_add_second_parent_id_ghost(self):
        """Test adding the second parent id - as a ghost"""
        tree = self.make_branch_and_tree('.')
        first_revision = tree.commit('first post')
        tree.add_parent_tree_id('second')
        self.assertConsistentParents([first_revision, 'second'], tree)
        
    def test_add_first_parent_tree(self):
        """Test adding the first parent id"""
        tree = self.make_branch_and_tree('.')
        first_revision = tree.commit('first post')
        uncommit(tree.branch, tree=tree)
        tree.add_parent_tree((first_revision,
            tree.branch.repository.revision_tree(first_revision)))
        self.assertConsistentParents([first_revision], tree)
        
    def test_add_first_parent_tree_ghost_rejects(self):
        """Test adding the first parent id - as a ghost"""
        tree = self.make_branch_and_tree('.')
        self.assertRaises(errors.GhostRevisionUnusableHere,
            tree.add_parent_tree, ('first-revision', None))
        
    def test_add_first_parent_tree_ghost_force(self):
        """Test adding the first parent id - as a ghost"""
        tree = self.make_branch_and_tree('.')
        tree.add_parent_tree(('first-revision', None),
            allow_leftmost_as_ghost=True)
        self.assertConsistentParents(['first-revision'], tree)
        
    def test_add_second_parent_tree(self):
        """Test adding the second parent id"""
        tree = self.make_branch_and_tree('.')
        first_revision = tree.commit('first post')
        uncommit(tree.branch, tree=tree)
        second_revision = tree.commit('second post')
        tree.add_parent_tree((first_revision,
            tree.branch.repository.revision_tree(first_revision)))
        self.assertConsistentParents([second_revision, first_revision], tree)
        
    def test_add_second_parent_tree_ghost(self):
        """Test adding the second parent id - as a ghost"""
        tree = self.make_branch_and_tree('.')
        first_revision = tree.commit('first post')
        tree.add_parent_tree(('second', None))
        self.assertConsistentParents([first_revision, 'second'], tree)


class UpdateToOneParentViaDeltaTests(TestCaseWithWorkingTree):
    """Tests for the update_basis_by_delta call.
    
    This is intuitively defined as 'apply an inventory delta to the basis and
    discard other parents', but for trees that have an inventory that is not
    managed as a tree-by-id, the implementation requires roughly duplicated
    tests with those for apply_inventory_delta on the main tree.
    """

    def assertDeltaApplicationResultsInExpectedBasis(self, tree, revid, delta,
        expected_inventory):
        tree.lock_write()
        try:
            tree.update_basis_by_delta(revid, delta)
        finally:
            tree.unlock()
        # check the last revision was adjusted to rev_id
        self.assertEqual(revid, tree.last_revision())
        # check the parents are what we expect
        self.assertEqual([revid], tree.get_parent_ids())
        # check that the basis tree has the inventory we expect from applying
        # the delta.
        result_basis = tree.basis_tree()
        result_basis.lock_read()
        self.addCleanup(result_basis.unlock)
        self.assertEqual(expected_inventory, result_basis.inventory)

    def make_inv_delta(self, old, new):
        """Make an inventory delta from two inventories."""
        old_ids = set(old._byid.iterkeys())
        new_ids = set(new._byid.iterkeys())
        adds = new_ids - old_ids
        deletes = old_ids - new_ids
        common = old_ids.intersection(new_ids)
        delta = []
        for file_id in deletes:
            delta.append((old.id2path(file_id), None, file_id, None))
        for file_id in adds:
            delta.append((None, new.id2path(file_id), file_id, new[file_id]))
        for file_id in common:
            if old[file_id] != new[file_id]:
                delta.append((old.id2path(file_id), new.id2path(file_id),
                    file_id, new[file_id]))
        return delta

    def fake_up_revision(self, tree, revid, shape):
        tree.lock_write()
        try:
            tree.branch.repository.start_write_group()
            try:
                if shape.root.revision is None:
                    shape.root.revision = revid
                sha1 = tree.branch.repository.add_inventory(revid, shape, [])
                rev = Revision(timestamp=0,
                               timezone=None,
                               committer="Foo Bar <foo@example.com>",
                               message="Message",
                               inventory_sha1=sha1,
                               revision_id=revid)
                tree.branch.repository.add_revision(revid, rev)
            except:
                tree.branch.repository.abort_write_group()
                raise
            else:
                tree.branch.repository.commit_write_group()
        finally:
            tree.unlock()

    def add_entry(self, inv, rev_id, entry):
        entry.revision = rev_id
        inv.add(entry)

    def add_dir(self, inv, rev_id, file_id, parent_id, name):
        new_dir = InventoryDirectory(file_id, name, parent_id)
        self.add_entry(inv, rev_id, new_dir)

    def add_file(self, inv, rev_id, file_id, parent_id, name, sha, size):
        new_file = InventoryFile(file_id, name, parent_id)
        new_file.text_sha1 = sha
        new_file.text_size = size
        self.add_entry(inv, rev_id, new_file)

    def add_link(self, inv, rev_id, file_id, parent_id, name, target):
        new_link = InventoryLink(file_id, name, parent_id)
        new_link.symlink_target = target
        self.add_entry(inv, rev_id, new_link)

    def add_new_root(self, new_shape, old_revid, new_revid):
        if self.bzrdir_format.repository_format.rich_root_data:
            self.add_dir(new_shape, old_revid, 'root-id', None, '')
        else:
            self.add_dir(new_shape, new_revid, 'root-id', None, '')

    def assertTransitionFromBasisToShape(self, basis_shape, basis_revid,
        new_shape, new_revid, extra_parent=None):
        # set the inventory revision ids.
        basis_shape.revision_id = basis_revid
        new_shape.revision_id = new_revid
        delta = self.make_inv_delta(basis_shape, new_shape)
        tree = self.make_branch_and_tree('tree')
        # the shapes need to be in the tree's repository to be able to set them
        # as a parent, but the file content is not needed.
        if basis_revid is not None:
            self.fake_up_revision(tree, basis_revid, basis_shape)
            parents = [basis_revid]
            if extra_parent is not None:
                parents.append(extra_parent)
            tree.set_parent_ids(parents)
        self.fake_up_revision(tree, new_revid, new_shape)
        # give tree an inventory of new_shape
        tree._write_inventory(new_shape)
        self.assertDeltaApplicationResultsInExpectedBasis(tree, new_revid,
            delta, new_shape)
        # The tree should be internally consistent; while this is a moderately
        # large hammer, this is a particularly sensitive area of code, so the
        # extra assurance is well worth it.
        tree._validate()
        osutils.rmtree('tree')

    def test_no_parents_just_root(self):
        """Test doing an empty commit - no parent, set a root only."""
        basis_shape = Inventory(root_id=None) # empty tree
        new_shape = Inventory() # tree with a root
        self.assertTransitionFromBasisToShape(basis_shape, None, new_shape,
            'new_parent')

    def test_no_parents_full_tree(self):
        """Test doing a regular initial commit with files and dirs."""
        basis_shape = Inventory(root_id=None) # empty tree
        revid = 'new-parent'
        new_shape = Inventory(root_id=None)
        self.add_dir(new_shape, revid, 'root-id', None, '')
        self.add_link(new_shape, revid, 'link-id', 'root-id', 'link', 'target')
        self.add_file(new_shape, revid, 'file-id', 'root-id', 'file', '1' * 32,
            12)
        self.add_dir(new_shape, revid, 'dir-id', 'root-id', 'dir')
        self.add_file(new_shape, revid, 'subfile-id', 'dir-id', 'subfile',
            '2' * 32, 24)
        self.assertTransitionFromBasisToShape(basis_shape, None, new_shape,
            revid)

    def test_file_content_change(self):
        old_revid = 'old-parent'
        basis_shape = Inventory(root_id=None)
        self.add_dir(basis_shape, old_revid, 'root-id', None, '')
        self.add_file(basis_shape, old_revid, 'file-id', 'root-id', 'file',
            '1' * 32, 12)
        new_revid = 'new-parent'
        new_shape = Inventory(root_id=None)
        self.add_new_root(new_shape, old_revid, new_revid)
        self.add_file(new_shape, new_revid, 'file-id', 'root-id', 'file',
            '2' * 32, 24)
        self.assertTransitionFromBasisToShape(basis_shape, old_revid,
            new_shape, new_revid)

    def test_link_content_change(self):
        old_revid = 'old-parent'
        basis_shape = Inventory(root_id=None)
        self.add_dir(basis_shape, old_revid, 'root-id', None, '')
        self.add_link(basis_shape, old_revid, 'link-id', 'root-id', 'link',
            'old-target')
        new_revid = 'new-parent'
        new_shape = Inventory(root_id=None)
        self.add_new_root(new_shape, old_revid, new_revid)
        self.add_link(new_shape, new_revid, 'link-id', 'root-id', 'link',
            'new-target')
        self.assertTransitionFromBasisToShape(basis_shape, old_revid,
            new_shape, new_revid)

    def test_kind_changes(self):
        def do_file(inv, revid):
            self.add_file(inv, revid, 'path-id', 'root-id', 'path', '1' * 32,
                12)
        def do_link(inv, revid):
            self.add_link(inv, revid, 'path-id', 'root-id', 'path', 'target')
        def do_dir(inv, revid):
            self.add_dir(inv, revid, 'path-id', 'root-id', 'path')
        for old_factory in (do_file, do_link, do_dir):
            for new_factory in (do_file, do_link, do_dir):
                if old_factory == new_factory:
                    continue
                old_revid = 'old-parent'
                basis_shape = Inventory(root_id=None)
                self.add_dir(basis_shape, old_revid, 'root-id', None, '')
                old_factory(basis_shape, old_revid)
                new_revid = 'new-parent'
                new_shape = Inventory(root_id=None)
                self.add_new_root(new_shape, old_revid, new_revid)
                new_factory(new_shape, new_revid)
                self.assertTransitionFromBasisToShape(basis_shape, old_revid,
                    new_shape, new_revid)

    def test_content_from_second_parent_is_dropped(self):
        left_revid = 'left-parent'
        basis_shape = Inventory(root_id=None)
        self.add_dir(basis_shape, left_revid, 'root-id', None, '')
        self.add_link(basis_shape, left_revid, 'link-id', 'root-id', 'link',
            'left-target')
        # the right shape has content - file, link, subdir with a child,
        # that should all be discarded by the call.
        right_revid = 'right-parent'
        right_shape = Inventory(root_id=None)
        self.add_dir(right_shape, left_revid, 'root-id', None, '')
        self.add_link(right_shape, right_revid, 'link-id', 'root-id', 'link',
            'some-target')
        self.add_dir(right_shape, right_revid, 'subdir-id', 'root-id', 'dir')
        self.add_file(right_shape, right_revid, 'file-id', 'subdir-id', 'file',
            '2' * 32, 24)
        new_revid = 'new-parent'
        new_shape = Inventory(root_id=None)
        self.add_new_root(new_shape, left_revid, new_revid)
        self.add_link(new_shape, new_revid, 'link-id', 'root-id', 'link',
            'new-target')
        self.assertTransitionFromBasisToShape(basis_shape, left_revid,
            new_shape, new_revid, right_revid)

    def test_parent_id_changed(self):
        # test that when the only change to an entry is its parent id changing
        # that it is handled correctly (that is it keeps the same path)
        old_revid = 'old-parent'
        basis_shape = Inventory(root_id=None)
        self.add_dir(basis_shape, old_revid, 'root-id', None, '')
        self.add_dir(basis_shape, old_revid, 'orig-parent-id', 'root-id', 'dir')
        self.add_dir(basis_shape, old_revid, 'dir-id', 'orig-parent-id', 'dir')
        new_revid = 'new-parent'
        new_shape = Inventory(root_id=None)
        self.add_new_root(new_shape, old_revid, new_revid)
        self.add_dir(new_shape, new_revid, 'new-parent-id', 'root-id', 'dir')
        self.add_dir(new_shape, new_revid, 'dir-id', 'new-parent-id', 'dir')
        self.assertTransitionFromBasisToShape(basis_shape, old_revid,
            new_shape, new_revid)

    def test_name_changed(self):
        # test that when the only change to an entry is its name changing that
        # it is handled correctly (that is it keeps the same parent id)
        old_revid = 'old-parent'
        basis_shape = Inventory(root_id=None)
        self.add_dir(basis_shape, old_revid, 'root-id', None, '')
        self.add_dir(basis_shape, old_revid, 'parent-id', 'root-id', 'origdir')
        self.add_dir(basis_shape, old_revid, 'dir-id', 'parent-id', 'olddir')
        new_revid = 'new-parent'
        new_shape = Inventory(root_id=None)
        self.add_new_root(new_shape, old_revid, new_revid)
        self.add_dir(new_shape, new_revid, 'parent-id', 'root-id', 'newdir')
        self.add_dir(new_shape, new_revid, 'dir-id', 'parent-id', 'newdir')
        self.assertTransitionFromBasisToShape(basis_shape, old_revid,
            new_shape, new_revid)

    def test_parent_child_swap(self):
        # test a A->A/B and A/B->A path swap.
        old_revid = 'old-parent'
        basis_shape = Inventory(root_id=None)
        self.add_dir(basis_shape, old_revid, 'root-id', None, '')
        self.add_dir(basis_shape, old_revid, 'dir-id-A', 'root-id', 'A')
        self.add_dir(basis_shape, old_revid, 'dir-id-B', 'dir-id-A', 'B')
        self.add_link(basis_shape, old_revid, 'link-id-C', 'dir-id-B', 'C', 'C')
        new_revid = 'new-parent'
        new_shape = Inventory(root_id=None)
        self.add_new_root(new_shape, old_revid, new_revid)
        self.add_dir(new_shape, new_revid, 'dir-id-B', 'root-id', 'A')
        self.add_dir(new_shape, new_revid, 'dir-id-A', 'dir-id-B', 'B')
        self.add_link(new_shape, new_revid, 'link-id-C', 'dir-id-A', 'C', 'C')
        self.assertTransitionFromBasisToShape(basis_shape, old_revid,
            new_shape, new_revid)

    def test_parent_deleted_child_renamed(self):
        # test a A->None and A/B->A.
        old_revid = 'old-parent'
        basis_shape = Inventory(root_id=None)
        self.add_dir(basis_shape, old_revid, 'root-id', None, '')
        self.add_dir(basis_shape, old_revid, 'dir-id-A', 'root-id', 'A')
        self.add_dir(basis_shape, old_revid, 'dir-id-B', 'dir-id-A', 'B')
        self.add_link(basis_shape, old_revid, 'link-id-C', 'dir-id-B', 'C', 'C')
        new_revid = 'new-parent'
        new_shape = Inventory(root_id=None)
        self.add_new_root(new_shape, old_revid, new_revid)
        self.add_dir(new_shape, new_revid, 'dir-id-B', 'root-id', 'A')
        self.add_link(new_shape, old_revid, 'link-id-C', 'dir-id-B', 'C', 'C')
        self.assertTransitionFromBasisToShape(basis_shape, old_revid,
            new_shape, new_revid)

    def test_dir_to_root(self):
        # test a A->''.
        old_revid = 'old-parent'
        basis_shape = Inventory(root_id=None)
        self.add_dir(basis_shape, old_revid, 'root-id', None, '')
        self.add_dir(basis_shape, old_revid, 'dir-id-A', 'root-id', 'A')
        self.add_link(basis_shape, old_revid, 'link-id-B', 'dir-id-A', 'B', 'B')
        new_revid = 'new-parent'
        new_shape = Inventory(root_id=None)
        self.add_dir(new_shape, new_revid, 'dir-id-A', None, '')
        self.add_link(new_shape, old_revid, 'link-id-B', 'dir-id-A', 'B', 'B')
        self.assertTransitionFromBasisToShape(basis_shape, old_revid,
            new_shape, new_revid)

    def test_path_swap(self):
        # test a A->B and B->A path swap.
        old_revid = 'old-parent'
        basis_shape = Inventory(root_id=None)
        self.add_dir(basis_shape, old_revid, 'root-id', None, '')
        self.add_dir(basis_shape, old_revid, 'dir-id-A', 'root-id', 'A')
        self.add_dir(basis_shape, old_revid, 'dir-id-B', 'root-id', 'B')
        self.add_link(basis_shape, old_revid, 'link-id-C', 'root-id', 'C', 'C')
        self.add_link(basis_shape, old_revid, 'link-id-D', 'root-id', 'D', 'D')
        self.add_file(basis_shape, old_revid, 'file-id-E', 'root-id', 'E',
            '1' * 32, 12)
        self.add_file(basis_shape, old_revid, 'file-id-F', 'root-id', 'F',
            '2' * 32, 24)
        new_revid = 'new-parent'
        new_shape = Inventory(root_id=None)
        self.add_new_root(new_shape, old_revid, new_revid)
        self.add_dir(new_shape, new_revid, 'dir-id-A', 'root-id', 'B')
        self.add_dir(new_shape, new_revid, 'dir-id-B', 'root-id', 'A')
        self.add_link(new_shape, new_revid, 'link-id-C', 'root-id', 'D', 'C')
        self.add_link(new_shape, new_revid, 'link-id-D', 'root-id', 'C', 'D')
        self.add_file(new_shape, new_revid, 'file-id-E', 'root-id', 'F',
            '1' * 32, 12)
        self.add_file(new_shape, new_revid, 'file-id-F', 'root-id', 'E',
            '2' * 32, 24)
        self.assertTransitionFromBasisToShape(basis_shape, old_revid,
            new_shape, new_revid)

    def test_adds(self):
        # test adding paths and dirs, including adding to a newly added dir.
        old_revid = 'old-parent'
        basis_shape = Inventory(root_id=None)
        # with a root, so its a commit after the first.
        self.add_dir(basis_shape, old_revid, 'root-id', None, '')
        new_revid = 'new-parent'
        new_shape = Inventory(root_id=None)
        self.add_new_root(new_shape, old_revid, new_revid)
        self.add_dir(new_shape, new_revid, 'dir-id-A', 'root-id', 'A')
        self.add_link(new_shape, new_revid, 'link-id-B', 'root-id', 'B', 'C')
        self.add_file(new_shape, new_revid, 'file-id-C', 'root-id', 'C',
            '1' * 32, 12)
        self.add_file(new_shape, new_revid, 'file-id-D', 'dir-id-A', 'D',
            '2' * 32, 24)
        self.assertTransitionFromBasisToShape(basis_shape, old_revid,
            new_shape, new_revid)

    def test_removes(self):
        # test removing paths, including paths that are within other also
        # removed paths.
        old_revid = 'old-parent'
        basis_shape = Inventory(root_id=None)
        self.add_dir(basis_shape, old_revid, 'root-id', None, '')
        self.add_dir(basis_shape, old_revid, 'dir-id-A', 'root-id', 'A')
        self.add_link(basis_shape, old_revid, 'link-id-B', 'root-id', 'B', 'C')
        self.add_file(basis_shape, old_revid, 'file-id-C', 'root-id', 'C',
            '1' * 32, 12)
        self.add_file(basis_shape, old_revid, 'file-id-D', 'dir-id-A', 'D',
            '2' * 32, 24)
        new_revid = 'new-parent'
        new_shape = Inventory(root_id=None)
        self.add_new_root(new_shape, old_revid, new_revid)
        self.assertTransitionFromBasisToShape(basis_shape, old_revid,
            new_shape, new_revid)

    def test_move_to_added_dir(self):
        old_revid = 'old-parent'
        basis_shape = Inventory(root_id=None)
        self.add_dir(basis_shape, old_revid, 'root-id', None, '')
        self.add_link(basis_shape, old_revid, 'link-id-B', 'root-id', 'B', 'C')
        new_revid = 'new-parent'
        new_shape = Inventory(root_id=None)
        self.add_new_root(new_shape, old_revid, new_revid)
        self.add_dir(new_shape, new_revid, 'dir-id-A', 'root-id', 'A')
        self.add_link(new_shape, new_revid, 'link-id-B', 'dir-id-A', 'B', 'C')
        self.assertTransitionFromBasisToShape(basis_shape, old_revid,
            new_shape, new_revid)

    def test_move_from_removed_dir(self):
        old_revid = 'old-parent'
        basis_shape = Inventory(root_id=None)
        self.add_dir(basis_shape, old_revid, 'root-id', None, '')
        self.add_dir(basis_shape, old_revid, 'dir-id-A', 'root-id', 'A')
        self.add_link(basis_shape, old_revid, 'link-id-B', 'dir-id-A', 'B', 'C')
        new_revid = 'new-parent'
        new_shape = Inventory(root_id=None)
        self.add_new_root(new_shape, old_revid, new_revid)
        self.add_link(new_shape, new_revid, 'link-id-B', 'root-id', 'B', 'C')
        self.assertTransitionFromBasisToShape(basis_shape, old_revid,
            new_shape, new_revid)

    def test_move_moves_children_recursively(self):
        old_revid = 'old-parent'
        basis_shape = Inventory(root_id=None)
        self.add_dir(basis_shape, old_revid, 'root-id', None, '')
        self.add_dir(basis_shape, old_revid, 'dir-id-A', 'root-id', 'A')
        self.add_dir(basis_shape, old_revid, 'dir-id-B', 'dir-id-A', 'B')
        self.add_link(basis_shape, old_revid, 'link-id-C', 'dir-id-B', 'C', 'D')
        new_revid = 'new-parent'
        new_shape = Inventory(root_id=None)
        self.add_new_root(new_shape, old_revid, new_revid)
        # the moved path:
        self.add_dir(new_shape, new_revid, 'dir-id-A', 'root-id', 'B')
        # unmoved children.
        self.add_dir(new_shape, old_revid, 'dir-id-B', 'dir-id-A', 'B')
        self.add_link(new_shape, old_revid, 'link-id-C', 'dir-id-B', 'C', 'D')
        self.assertTransitionFromBasisToShape(basis_shape, old_revid,
            new_shape, new_revid)
