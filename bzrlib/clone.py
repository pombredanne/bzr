# Copyright (C) 2004, 2005 by Canonical Ltd

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

"""Make a copy of an entire branch and all its history.

This is the underlying function for the branch/get/clone commands."""

# TODO: This could be done *much* more efficiently by just copying
# all the whole weaves and revisions, rather than getting one
# revision at a time.

# TODO: Optionally, after copying, discard any irrelevant information from
# the destination, such as revisions committed after the last one we're interested 
# in.  This needs to apply a weave prune operation (not written yet) to each
# weave one by one.

# Copying must be done in a way that supports http transports, where we
# can't list a directory, and therefore have to rely on information
# retrieved from top-level objects whose names we do know.
#
# In practice this means we first fetch the revision history and ancestry.
# These give us a list of all the revisions that need to be fetched.  We 
# also get the inventory weave.  We then just need to get a list of all 
# file-ids ever referenced by this tree.  (It might be nice to keep a list
# of them directly.)  This is done by walking over the inventories of all
# copied revisions and accumulating a list of file ids.
#
# For local branches it is possible to optimize this considerably in two
# ways.  One is to hardlink the files (if possible and requested), rather
# than copying them.  Another is to simply list the directory rather than
# walking through the inventories to find out what files are present -- but
# there it may be better to just be consistent with remote branches.

import os
import sys

import bzrlib
from bzrlib.merge import build_working_dir
from bzrlib.branch import Branch
from bzrlib.trace import mutter, note
from bzrlib.store import copy_all
from bzrlib.errors import InvalidRevisionId

def copy_branch(branch_from, to_location, revision=None, basis_branch=None):
    """Copy branch_from into the existing directory to_location.

    Returns the newly created branch object.

    revision
        If not None, only revisions up to this point will be copied.
        The head of the new branch will be that revision.  Must be a
        revid or None.

    to_location -- The destination directory; must either exist and be 
        empty, or not exist, in which case it is created.

    basis_branch
        A local branch to copy revisions from, related to branch_from. 
        This is used when branching from a remote (slow) branch, and we have
        a local branch that might contain some relevant revisions.
    """
    assert isinstance(branch_from, Branch)
    assert isinstance(to_location, basestring)
    if basis_branch is not None:
        note("basis_branch is not supported for fast weave copy yet.")
    branch_from.lock_read()
    try:
        history = _get_truncated_history(branch_from, revision)
        if not bzrlib.osutils.lexists(to_location):
            os.mkdir(to_location)
        branch_to = Branch.initialize(to_location)
        mutter("copy branch from %s to %s", branch_from, branch_to)
        branch_to.working_tree().set_root_id(branch_from.get_root_id())
        _copy_control_weaves(branch_from, branch_to, history)
        _copy_text_weaves(branch_from, branch_to, history)
        _copy_revision_store(branch_from, branch_to, history)
        branch_to.set_parent(branch_from.base)
        # must be done *after* history is copied across
        branch_to.append_revision(*history)
        build_working_dir(to_location)
        mutter("copied")
        return branch_to
    finally:
        branch_from.unlock()


def _get_truncated_history(branch_from, revision_id):
    history = branch_from.revision_history()
    if revision_id is None:
        return history
    try:
        idx = history.index(revision_id)
    except ValueError:
        raise InvalidRevisionId(revision_id=revision, branch=branch_from)
    return history[:idx+1]

def _copy_text_weaves(branch_from, branch_to, history):

    from_set = set(branch_from.get_ancestry(history[-1])[1:])
    file_ids = branch_from.file_involved( from_set )
    branch_to.weave_store.copy_multi(branch_from.weave_store, file_ids )


def _copy_revision_store(branch_from, branch_to, history):

    # copy all revision
    from_set = set(branch_from.get_ancestry(history[-1])[1:])
    branch_to.revision_store.copy_multi(branch_from.revision_store, from_set )


def _copy_control_weaves(branch_from, branch_to, history):
    to_control = branch_to.control_weaves
    from_control = branch_from.control_weaves
    # TODO, we need only the minimal revision !!!!!
    to_control.copy_multi(from_control, ['inventory'])

