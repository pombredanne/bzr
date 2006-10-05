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

"""Tests for the (un)lock interfaces on all working tree implemenations."""

from bzrlib import (
    branch,
    errors,
    lockdir,
    )
from bzrlib.tests.workingtree_implementations import TestCaseWithWorkingTree


class TestWorkingTreeLocking(TestCaseWithWorkingTree):

    def test_trivial_lock_read_unlock(self):
        """Locking and unlocking should work trivially."""
        wt = self.make_branch_and_tree('.')

        self.assertFalse(wt.is_locked())
        self.assertFalse(wt.branch.is_locked())
        wt.lock_read()
        try:
            self.assertTrue(wt.is_locked())
            self.assertTrue(wt.branch.is_locked())
        finally:
            wt.unlock()
        self.assertFalse(wt.is_locked())
        self.assertFalse(wt.branch.is_locked())

    def test_trivial_lock_write_unlock(self):
        """Locking for write and unlocking should work trivially."""
        wt = self.make_branch_and_tree('.')

        self.assertFalse(wt.is_locked())
        self.assertFalse(wt.branch.is_locked())
        wt.lock_write()
        try:
            self.assertTrue(wt.is_locked())
            self.assertTrue(wt.branch.is_locked())
        finally:
            wt.unlock()
        self.assertFalse(wt.is_locked())
        self.assertFalse(wt.branch.is_locked())
        
    def test_trivial_lock_tree_write_unlock(self):
        """Locking for tree write is ok when the branch is not locked."""
        wt = self.make_branch_and_tree('.')

        self.assertFalse(wt.is_locked())
        self.assertFalse(wt.branch.is_locked())
        wt.lock_tree_write()
        try:
            self.assertTrue(wt.is_locked())
            self.assertTrue(wt.branch.is_locked())
        finally:
            wt.unlock()
        self.assertFalse(wt.is_locked())
        self.assertFalse(wt.branch.is_locked())
        
    def test_trivial_lock_tree_write_branch_read_locked(self):
        """It is ok to lock_tree_write when the branch is read locked."""
        wt = self.make_branch_and_tree('.')

        self.assertFalse(wt.is_locked())
        self.assertFalse(wt.branch.is_locked())
        wt.branch.lock_read()
        try:
            wt.lock_tree_write()
        except errors.ReadOnlyError:
            # When ReadOnlyError is raised, it indicates that the 
            # workingtree shares its lock with the branch, which is what
            # the git/hg/bzr0.6 formats do.
            # in this case, no lock should have been taken - but the tree
            # will have been locked because they share a lock. Unlocking
            # just the branch should make everything match again correctly.
            wt.branch.unlock()
            self.assertFalse(wt.is_locked())
            self.assertFalse(wt.branch.is_locked())
            return
        try:
            self.assertTrue(wt.is_locked())
            self.assertTrue(wt.branch.is_locked())
        finally:
            wt.unlock()
        self.assertFalse(wt.is_locked())
        self.assertTrue(wt.branch.is_locked())
        wt.branch.unlock()
        
    def _test_unlock_with_lock_method(self, methodname):
        """Create a tree and then test its unlocking behaviour.

        :param methodname: The lock method to use to establish locks.
        """
        # when unlocking the last lock count from tree_write_lock,
        # the tree should do a flush().
        # we test that by changing the inventory using set_root_id
        tree = self.make_branch_and_tree('tree')
        # prepare for a series of changes that will modify the 
        # inventory
        getattr(tree, methodname)()
        # note that we dont have a try:finally here because of two reasons:
        # firstly there will only be errors reported if the test fails, and 
        # when it fails thats ok as long as the test suite cleanup still works,
        # which it will as the lock objects are released (thats wher ethe 
        # warning comes from.  Secondly, its hard in this test to be 
        # sure that we've got the right interactions between try:finally
        # and the lock/unlocks we are doing.
        getattr(tree, methodname)()
        # this should really do something within the public api
        # e.g. mkdir('foo') but all the mutating methods at the
        # moment trigger inventory writes and thus will not 
        # let us trigger a read-when-dirty situation.
        old_root = tree.get_root_id()
        tree.set_root_id('new-root')
        # to detect that the inventory is written by unlock, we
        # first check that it was not written yet.
        reference_tree = tree.bzrdir.open_workingtree()
        self.assertEqual(old_root, reference_tree.get_root_id())
        # now unlock the second held lock, which should do nothing.
        tree.unlock()
        reference_tree = tree.bzrdir.open_workingtree()
        self.assertEqual(old_root, reference_tree.get_root_id())
        # unlocking the first lock we took will now flush.
        tree.unlock()
        # and check it was written using another reference tree
        reference_tree = tree.bzrdir.open_workingtree()
        self.assertEqual('new-root', reference_tree.get_root_id())

    def test_unlock_from_tree_write_lock_flushes(self):
        self._test_unlock_with_lock_method("lock_tree_write")
        
    def test_unlock_from_write_lock_flushes(self):
        self._test_unlock_with_lock_method("lock_write")
        
    def test_unlock_branch_failures(self):
        """If the branch unlock fails the tree must still unlock."""
        # The public interface for WorkingTree requires a branch, but
        # does not require that the working tree use the branch - its
        # implementation specific how the WorkingTree, Branch, and Repository
        # hang together.
        # in order to test that implementations which *do* unlock via the branch
        # do so correctly, we unlock the branch after locking the working tree.
        # The next unlock on working tree should trigger a LockNotHeld exception
        # from the branch object, which must be exposed to the caller. To meet 
        # our object model - where locking a tree locks its branch, and
        # unlocking a branch does not unlock a working tree, *even* for 
        # all-in-one implementations like bzr 0.6, git, and hg, implementations
        # must have some separate counter for each object, so our explicit 
        # unlock should trigger some error on all implementations, and 
        # requiring that to be LockNotHeld seems reasonable.
        #
        # we use this approach rather than decorating the Branch, because the
        # public interface of WorkingTree does not permit altering the branch
        # object - and we cannot tell which attribute might allow us to 
        # backdoor-in and change it reliably. For implementation specific tests
        # we can do such skullduggery, but not for interface specific tests.
        # And, its simpler :)
        wt = self.make_branch_and_tree('.')

        self.assertFalse(wt.is_locked())
        self.assertFalse(wt.branch.is_locked())
        wt.lock_write()
        self.assertTrue(wt.is_locked())
        self.assertTrue(wt.branch.is_locked())

        # manually unlock the branch, preparing a LockNotHeld error.
        wt.branch.unlock()
        # the branch *may* still be locked here, if its an all-in-one
        # implementation because there is a single lock object with three
        # references on it, and unlocking the branch only drops this by two
        self.assertRaises(errors.LockNotHeld, wt.unlock)
        # but now, the tree must be unlocked
        self.assertFalse(wt.is_locked())
        # and the branch too.
        self.assertFalse(wt.branch.is_locked())

    def test_failing_to_lock_branch_does_not_lock(self):
        """If the branch cannot be locked, dont lock the tree."""
        # Many implementations treat read-locks as non-blocking, but some
        # treat them as blocking with writes.. Accordingly we test this by
        # opening the branch twice, and locking the branch for write in the
        # second instance.  Our lock contract requires separate instances to
        # mutually exclude if a lock is exclusive at all: If we get no error
        # locking, the test still passes.
        wt = self.make_branch_and_tree('.')
        branch_copy = branch.Branch.open('.')
        branch_copy.lock_write()
        try:
            try:
                wt.lock_read()
            except errors.LockError:
                # any error here means the locks are exclusive in some 
                # manner
                self.assertFalse(wt.is_locked())
                self.assertFalse(wt.branch.is_locked())
                return
            else:
                # no error - the branch allows read locks while writes
                # are taken, just pass.
                wt.unlock()
        finally:
            branch_copy.unlock()

    def test_failing_to_lock_write_branch_does_not_lock(self):
        """If the branch cannot be write locked, dont lock the tree."""
        # all implementations of branch are required to treat write 
        # locks as blocking (compare to repositories which are not required
        # to do so).
        # Accordingly we test this by opening the branch twice, and locking the
        # branch for write in the second instance.  Our lock contract requires
        # separate instances to mutually exclude.
        wt = self.make_branch_and_tree('.')
        branch_copy = branch.Branch.open('.')
        branch_copy.lock_write()
        try:
            try:
                orig_default = lockdir._DEFAULT_TIMEOUT_SECONDS
                try:
                    lockdir._DEFAULT_TIMEOUT_SECONDS = 1
                    self.assertRaises(errors.LockError, wt.lock_write)
                finally:
                    lockdir._DEFAULT_TIMEOUT_SECONDS = orig_default

                self.assertFalse(wt.is_locked())
                self.assertFalse(wt.branch.is_locked())
            finally:
                if wt.is_locked():
                    wt.unlock()
        finally:
            branch_copy.unlock()

    def test_failing_to_lock_tree_write_branch_does_not_lock(self):
        """If the branch cannot be read locked, dont lock the tree."""
        # Many implementations treat read-locks as non-blocking, but some
        # treat them as blocking with writes.. Accordingly we test this by
        # opening the branch twice, and locking the branch for write in the
        # second instance.  Our lock contract requires separate instances to
        # mutually exclude if a lock is exclusive at all: If we get no error
        # locking, the test still passes.
        wt = self.make_branch_and_tree('.')
        branch_copy = branch.Branch.open('.')

        branch_copy.lock_write()
        try:
            try:
                wt.lock_tree_write()
            except errors.LockError:
                # any error here means the locks are exclusive in some 
                # manner
                self.assertFalse(wt.is_locked())
                self.assertFalse(wt.branch.is_locked())
                return
            else:
                # no error - the branch allows read locks while writes
                # are taken, just pass.
                wt.unlock()
        finally:
            branch_copy.unlock()
