# Copyright (C) 2005, 2006 Canonical Ltd
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

"""Tests for InterRepository implementastions."""

import sys

import bzrlib
import bzrlib.bzrdir as bzrdir
from bzrlib.branch import Branch, needs_read_lock, needs_write_lock
import bzrlib.errors as errors
from bzrlib.errors import (FileExists,
                           NoSuchRevision,
                           NoSuchFile,
                           UninitializableFormat,
                           NotBranchError,
                           )
from bzrlib.inventory import Inventory
import bzrlib.repofmt.weaverepo as weaverepo
import bzrlib.repository as repository
from bzrlib.revision import NULL_REVISION, Revision
from bzrlib.tests import (
    TestCase,
    TestCaseWithTransport,
    TestNotApplicable,
    TestSkipped,
    )
from bzrlib.tests.bzrdir_implementations.test_bzrdir import TestCaseWithBzrDir
from bzrlib.transport import get_transport


class TestCaseWithInterRepository(TestCaseWithBzrDir):

    def setUp(self):
        super(TestCaseWithInterRepository, self).setUp()

    def make_branch(self, relpath, format=None):
        repo = self.make_repository(relpath, format=format)
        return repo.bzrdir.create_branch()

    def make_bzrdir(self, relpath, format=None):
        try:
            url = self.get_url(relpath)
            segments = url.split('/')
            if segments and segments[-1] not in ('', '.'):
                parent = '/'.join(segments[:-1])
                t = get_transport(parent)
                try:
                    t.mkdir(segments[-1])
                except FileExists:
                    pass
            if format is None:
                format = self.repository_format._matchingbzrdir
            return format.initialize(url)
        except UninitializableFormat:
            raise TestSkipped("Format %s is not initializable." % format)

    def make_repository(self, relpath, format=None):
        made_control = self.make_bzrdir(relpath, format=format)
        return self.repository_format.initialize(made_control)

    def make_to_repository(self, relpath):
        made_control = self.make_bzrdir(relpath,
            self.repository_format_to._matchingbzrdir)
        return self.repository_format_to.initialize(made_control)


def check_old_format_lock_error(repository_format):
    """Potentially ignore LockError on old formats.

    On win32, with the old OS locks, we get a failure of double-lock when
    we open a object in 2 objects and try to lock both.

    On new formats, LockError would be invalid, but for old formats
    this was not supported on Win32.
    """
    if sys.platform != 'win32':
        raise

    description = repository_format.get_format_description()
    if description in ("Repository format 4",
                       "Weave repository format 5",
                       "Weave repository format 6"):
        # jam 20060701
        # win32 OS locks are not re-entrant. So one process cannot
        # open the same repository twice and lock them both.
        raise TestSkipped('%s on win32 cannot open the same'
                          ' repository twice in different objects'
                          % description)
    raise


def check_repo_format_for_funky_id_on_win32(repo):
    if (isinstance(repo, (weaverepo.AllInOneRepository,
                          weaverepo.WeaveMetaDirRepository))
        and sys.platform == 'win32'):
            raise TestSkipped("funky chars does not permitted"
                              " on this platform in repository"
                              " %s" % repo.__class__.__name__)


class TestInterRepository(TestCaseWithInterRepository):

    def test_interrepository_get_returns_correct_optimiser(self):
        # we assume the optimising code paths are triggered
        # by the type of the repo not the transport - at this point.
        # we may need to update this test if this changes.
        #
        # XXX: This code tests that we get an InterRepository when we try to
        # convert between the two repositories that it wants to be tested with
        # -- but that's not necessarily correct.  So for now this is disabled.
        # mbp 20070206
        ## source_repo = self.make_repository("source")
        ## target_repo = self.make_to_repository("target")
        ## interrepo = repository.InterRepository.get(source_repo, target_repo)
        ## self.assertEqual(self.interrepo_class, interrepo.__class__)
        pass

    def test_fetch(self):
        tree_a = self.make_branch_and_tree('a')
        self.build_tree(['a/foo'])
        tree_a.add('foo', 'file1')
        tree_a.commit('rev1', rev_id='rev1')
        def check_push_rev1(repo):
            # ensure the revision is missing.
            self.assertRaises(NoSuchRevision, repo.get_revision, 'rev1')
            # fetch with a limit of NULL_REVISION and an explicit progress bar.
            repo.fetch(tree_a.branch.repository,
                       revision_id=NULL_REVISION,
                       pb=bzrlib.progress.DummyProgress())
            # nothing should have been pushed
            self.assertFalse(repo.has_revision('rev1'))
            # fetch with a default limit (grab everything)
            repo.fetch(tree_a.branch.repository)
            # check that b now has all the data from a's first commit.
            rev = repo.get_revision('rev1')
            tree = repo.revision_tree('rev1')
            tree.lock_read()
            self.addCleanup(tree.unlock)
            tree.get_file_text('file1')
            for file_id in tree:
                if tree.inventory[file_id].kind == "file":
                    tree.get_file(file_id).read()

        # makes a target version repo 
        repo_b = self.make_to_repository('b')
        check_push_rev1(repo_b)

    def test_fetch_missing_basis_text(self):
        """If fetching a delta, we should die if a basis is not present."""
        tree = self.make_branch_and_tree('tree')
        self.build_tree(['tree/a'])
        tree.add(['a'], ['a-id'])
        tree.commit('one', rev_id='rev-one')
        self.build_tree_contents([('tree/a', 'new contents\n')])
        tree.commit('two', rev_id='rev-two')

        to_repo = self.make_to_repository('to_repo')
        # We build a broken revision so that we can test the fetch code dies
        # properly. So copy the inventory and revision, but not the text.
        to_repo.lock_write()
        try:
            to_repo.start_write_group()
            inv = tree.branch.repository.get_inventory('rev-one')
            to_repo.add_inventory('rev-one', inv, [])
            rev = tree.branch.repository.get_revision('rev-one')
            to_repo.add_revision('rev-one', rev, inv=inv)
            to_repo.commit_write_group()
        finally:
            to_repo.unlock()

        # Implementations can either copy the missing basis text, or raise an
        # exception
        try:
            to_repo.fetch(tree.branch.repository, 'rev-two')
        except (errors.MissingText, errors.RevisionNotPresent), e:
            # If an exception is raised, the revision should not be in the
            # target.
            self.assertRaises((errors.NoSuchRevision, errors.RevisionNotPresent),
                              to_repo.revision_tree, 'rev-two')
        else:
            # If not exception is raised, then the basis text should be
            # available.
            to_repo.lock_read()
            try:
                rt = to_repo.revision_tree('rev-one')
                self.assertEqual('contents of tree/a\n',
                                 rt.get_file_text('a-id'))
            finally:
                to_repo.unlock()

    def test_fetch_missing_revision_same_location_fails(self):
        repo_a = self.make_repository('.')
        repo_b = repository.Repository.open('.')
        try:
            self.assertRaises(errors.NoSuchRevision, repo_b.fetch, repo_a, revision_id='XXX')
        except errors.LockError, e:
            check_old_format_lock_error(self.repository_format)

    def test_fetch_same_location_trivial_works(self):
        repo_a = self.make_repository('.')
        repo_b = repository.Repository.open('.')
        try:
            repo_a.fetch(repo_b)
        except errors.LockError, e:
            check_old_format_lock_error(self.repository_format)

    def test_fetch_missing_text_other_location_fails(self):
        source_tree = self.make_branch_and_tree('source')
        source = source_tree.branch.repository
        target = self.make_to_repository('target')
    
        # start by adding a file so the data knit for the file exists in
        # repositories that have specific files for each fileid.
        self.build_tree(['source/id'])
        source_tree.add(['id'], ['id'])
        source_tree.commit('a', rev_id='a')
        # now we manually insert a revision with an inventory referencing
        # 'id' at revision 'b', but we do not insert revision b.
        # this should ensure that the new versions of files are being checked
        # for during pull operations
        inv = source.get_inventory('a')
        source.lock_write()
        source.start_write_group()
        inv['id'].revision = 'b'
        inv.revision_id = 'b'
        sha1 = source.add_inventory('b', inv, ['a'])
        rev = Revision(timestamp=0,
                       timezone=None,
                       committer="Foo Bar <foo@example.com>",
                       message="Message",
                       inventory_sha1=sha1,
                       revision_id='b')
        rev.parent_ids = ['a']
        source.add_revision('b', rev)
        source.commit_write_group()
        source.unlock()
        self.assertRaises(errors.RevisionNotPresent, target.fetch, source)
        self.assertFalse(target.has_revision('b'))

    def test_fetch_funky_file_id(self):
        from_tree = self.make_branch_and_tree('tree')
        if sys.platform == 'win32':
            from_repo = from_tree.branch.repository
            check_repo_format_for_funky_id_on_win32(from_repo)
        self.build_tree(['tree/filename'])
        from_tree.add('filename', 'funky-chars<>%&;"\'')
        from_tree.commit('commit filename')
        to_repo = self.make_to_repository('to')
        to_repo.fetch(from_tree.branch.repository, from_tree.get_parent_ids()[0])

    def test_fetch_no_inventory_revision(self):
        """Old inventories lack revision_ids, so simulate this"""
        from_tree = self.make_branch_and_tree('tree')
        if sys.platform == 'win32':
            from_repo = from_tree.branch.repository
            check_repo_format_for_funky_id_on_win32(from_repo)
        self.build_tree(['tree/filename'])
        from_tree.add('filename', 'funky-chars<>%&;"\'')
        from_tree.commit('commit filename')
        old_deserialise = from_tree.branch.repository.deserialise_inventory
        def deserialise(revision_id, text):
            inventory = old_deserialise(revision_id, text)
            inventory.revision_id = None
            return inventory
        from_tree.branch.repository.deserialise_inventory = deserialise
        to_repo = self.make_to_repository('to')
        to_repo.fetch(from_tree.branch.repository, from_tree.last_revision())


class TestCaseWithComplexRepository(TestCaseWithInterRepository):

    def setUp(self):
        super(TestCaseWithComplexRepository, self).setUp()
        tree_a = self.make_branch_and_tree('a')
        self.bzrdir = tree_a.branch.bzrdir
        # add a corrupt inventory 'orphan'
        tree_a.branch.repository.lock_write()
        tree_a.branch.repository.start_write_group()
        inv_file = tree_a.branch.repository.get_inventory_weave()
        inv_file.add_lines('orphan', [], [])
        tree_a.branch.repository.commit_write_group()
        tree_a.branch.repository.unlock()
        # add a real revision 'rev1'
        tree_a.commit('rev1', rev_id='rev1', allow_pointless=True)
        # add a real revision 'rev2' based on rev1
        tree_a.commit('rev2', rev_id='rev2', allow_pointless=True)
        # and sign 'rev2'
        tree_a.branch.repository.lock_write()
        tree_a.branch.repository.start_write_group()
        tree_a.branch.repository.sign_revision('rev2', bzrlib.gpg.LoopbackGPGStrategy(None))
        tree_a.branch.repository.commit_write_group()
        tree_a.branch.repository.unlock()

    def test_missing_revision_ids(self):
        # revision ids in repository A but not B are returned, fake ones
        # are stripped. (fake meaning no revision object, but an inventory 
        # as some formats keyed off inventory data in the past.)
        # make a repository to compare against that claims to have rev1
        repo_b = self.make_to_repository('rev1_only')
        repo_a = self.bzrdir.open_repository()
        repo_b.fetch(repo_a, 'rev1')
        # check the test will be valid
        self.assertFalse(repo_b.has_revision('rev2'))
        self.assertEqual(['rev2'],
                         repo_b.missing_revision_ids(repo_a))

    def test_missing_revision_ids_absent_requested_raises(self):
        # Asking for missing revisions with a tip that is itself absent in the
        # source raises NoSuchRevision.
        repo_b = self.make_to_repository('target')
        repo_a = self.bzrdir.open_repository()
        # No pizza revisions anywhere
        self.assertFalse(repo_a.has_revision('pizza'))
        self.assertFalse(repo_b.has_revision('pizza'))
        # Asking specifically for an absent revision errors.
        self.assertRaises(NoSuchRevision, repo_b.missing_revision_ids, repo_a,
            revision_id='pizza', find_ghosts=True)
        self.assertRaises(NoSuchRevision, repo_b.missing_revision_ids, repo_a,
            revision_id='pizza', find_ghosts=False)

    def test_missing_revision_ids_revision_limited(self):
        # revision ids in repository A that are not referenced by the
        # requested revision are not returned.
        # make a repository to compare against that is empty
        repo_b = self.make_to_repository('empty')
        repo_a = self.bzrdir.open_repository()
        self.assertEqual(['rev1'],
                         repo_b.missing_revision_ids(repo_a, revision_id='rev1'))
        
    def test_fetch_fetches_signatures_too(self):
        from_repo = self.bzrdir.open_repository()
        from_signature = from_repo.get_signature_text('rev2')
        to_repo = self.make_to_repository('target')
        to_repo.fetch(from_repo)
        to_signature = to_repo.get_signature_text('rev2')
        self.assertEqual(from_signature, to_signature)


class TestCaseWithGhosts(TestCaseWithInterRepository):

    def test_fetch_all_fixes_up_ghost(self):
        # we want two repositories at this point:
        # one with a revision that is a ghost in the other
        # repository.
        # 'ghost' is present in has_ghost, 'ghost' is absent in 'missing_ghost'.
        # 'references' is present in both repositories, and 'tip' is present
        # just in has_ghost.
        # has_ghost       missing_ghost
        #------------------------------
        # 'ghost'             -
        # 'references'    'references'
        # 'tip'               -
        # In this test we fetch 'tip' which should not fetch 'ghost'
        has_ghost = self.make_repository('has_ghost')
        missing_ghost = self.make_repository('missing_ghost')
        if [True, True] != [repo._format.supports_ghosts for repo in
            (has_ghost, missing_ghost)]:
            raise TestNotApplicable("Need ghost support.")

        def add_commit(repo, revision_id, parent_ids):
            repo.lock_write()
            repo.start_write_group()
            inv = Inventory(revision_id=revision_id)
            inv.root.revision = revision_id
            root_id = inv.root.file_id
            sha1 = repo.add_inventory(revision_id, inv, parent_ids)
            vf = repo.weave_store.get_weave_or_empty(root_id,
                repo.get_transaction())
            vf.add_lines(revision_id, [], [])
            rev = bzrlib.revision.Revision(timestamp=0,
                                           timezone=None,
                                           committer="Foo Bar <foo@example.com>",
                                           message="Message",
                                           inventory_sha1=sha1,
                                           revision_id=revision_id)
            rev.parent_ids = parent_ids
            repo.add_revision(revision_id, rev)
            repo.commit_write_group()
            repo.unlock()
        add_commit(has_ghost, 'ghost', [])
        add_commit(has_ghost, 'references', ['ghost'])
        add_commit(missing_ghost, 'references', ['ghost'])
        add_commit(has_ghost, 'tip', ['references'])
        missing_ghost.fetch(has_ghost, 'tip', find_ghosts=True)
        # missing ghost now has tip and ghost.
        rev = missing_ghost.get_revision('tip')
        inv = missing_ghost.get_inventory('tip')
        rev = missing_ghost.get_revision('ghost')
        inv = missing_ghost.get_inventory('ghost')
        # rev must not be corrupt now
        self.assertEqual([None, 'ghost', 'references', 'tip'],
            missing_ghost.get_ancestry('tip'))


class TestFetchDependentData(TestCaseWithInterRepository):

    def test_reference(self):
        from_tree = self.make_branch_and_tree('tree')
        to_repo = self.make_to_repository('to')
        if (not from_tree.supports_tree_reference() or
            not from_tree.branch.repository._format.supports_tree_reference or
            not to_repo._format.supports_tree_reference):
            raise TestNotApplicable("Need subtree support.")
        subtree = self.make_branch_and_tree('tree/subtree')
        subtree.commit('subrev 1')
        from_tree.add_reference(subtree)
        tree_rev = from_tree.commit('foo')
        # now from_tree has a last-modified of subtree of the rev id of the
        # commit for foo, and a reference revision of the rev id of the commit
        # for subrev 1
        to_repo.fetch(from_tree.branch.repository, tree_rev)
        # to_repo should have a file_graph for from_tree.path2id('subtree') and
        # revid tree_rev.
        to_repo.lock_read()
        try:
            file_vf = to_repo.weave_store.get_weave(
                from_tree.path2id('subtree'), to_repo.get_transaction())
            self.assertEqual([tree_rev], file_vf.get_ancestry([tree_rev]))
        finally:
            to_repo.unlock()
