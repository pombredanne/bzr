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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

"""Display what revisions are missing in 'other' from 'this' and vice versa."""

from __future__ import absolute_import

from bzrlib import (
    log,
    symbol_versioning,
    )
import bzrlib.revision as _mod_revision


def iter_log_revisions(revisions, revision_source, verbose, branch=None):
    last_tree = revision_source.revision_tree(_mod_revision.NULL_REVISION)
    last_rev_id = None

    if branch is not None and branch.supports_tags():
        rev_tag_dict = branch.tags.get_reverse_tag_dict()
    else:
        rev_tag_dict = {}
    for rev in revisions:
        # We need the following for backward compatibilty (hopefully
        # this will be deprecated soon :-/) -- vila 080911
        if len(rev) == 2:
            revno, rev_id = rev
            merge_depth = 0
        else:
            revno, rev_id, merge_depth = rev
        rev = revision_source.get_revision(rev_id)
        if verbose:
            delta = revision_source.get_revision_delta(rev_id)
        else:
            delta = None
        yield log.LogRevision(rev, revno, merge_depth, delta=delta,
                              tags=rev_tag_dict.get(rev_id))


def find_unmerged(local_branch, remote_branch, restrict='all',
                  include_merged=None, backward=False,
                  local_revid_range=None, remote_revid_range=None,
                  include_merges=symbol_versioning.DEPRECATED_PARAMETER):
    """Find revisions from each side that have not been merged.

    :param local_branch: Compare the history of local_branch
    :param remote_branch: versus the history of remote_branch, and determine
        mainline revisions which have not been merged.
    :param restrict: ('all', 'local', 'remote') If 'all', we will return the
        unique revisions from both sides. If 'local', we will return None
        for the remote revisions, similarly if 'remote' we will return None for
        the local revisions.
    :param include_merged: Show mainline revisions only if False,
        all revisions otherwise.
    :param backward: Show oldest versions first when True, newest versions
        first when False.
    :param local_revid_range: Revision-id range for filtering local_branch
        revisions (lower bound, upper bound)
    :param remote_revid_range: Revision-id range for filtering remote_branch
        revisions (lower bound, upper bound)
    :param include_merges: Deprecated historical alias for include_merged

    :return: A list of [(revno, revision_id)] for the mainline revisions on
        each side.
    """
    if symbol_versioning.deprecated_passed(include_merges):
        symbol_versioning.warn(
            'include_merges was deprecated in 2.5.'
            ' Use include_merged instead.',
            DeprecationWarning, stacklevel=2)
        if include_merged is None:
            include_merged = include_merges
    if include_merged is None:
        include_merged = False
    local_branch.lock_read()
    try:
        remote_branch.lock_read()
        try:
            return _find_unmerged(
                local_branch, remote_branch, restrict=restrict,
                include_merged=include_merged, backward=backward,
                local_revid_range=local_revid_range,
                remote_revid_range=remote_revid_range)
        finally:
            remote_branch.unlock()
    finally:
        local_branch.unlock()


def _enumerate_mainline(ancestry, graph, tip_revno, tip, backward=True):
    """Enumerate the mainline revisions for these revisions.

    :param ancestry: A set of revisions that we care about
    :param graph: A Graph which lets us find the parents for a revision
    :param tip_revno: The revision number for the tip revision
    :param tip: The tip of mainline
    :param backward: Show oldest versions first when True, newest versions
        first when False.
    :return: [(revno, revision_id)] for all revisions in ancestry that
        are left-hand parents from tip, or None if ancestry is None.
    """
    if ancestry is None:
        return None
    if not ancestry: #Empty ancestry, no need to do any work
        return []

    # Optionally, we could make 1 call to graph.get_parent_map with all
    # ancestors. However that will often check many more parents than we
    # actually need, and the Graph is likely to already have the parents cached
    # anyway.
    mainline = []
    cur = tip
    cur_revno = tip_revno
    while cur in ancestry:
        parent_map = graph.get_parent_map([cur])
        parents = parent_map.get(cur)
        if not parents:
            break # Ghost, we are done
        mainline.append((str(cur_revno), cur))
        cur = parents[0]
        cur_revno -= 1
    if not backward:
        mainline.reverse()
    return mainline


def _enumerate_with_merges(branch, ancestry, graph, tip_revno, tip,
                           backward=True):
    """Enumerate the revisions for the ancestry.

    :param branch: The branch we care about
    :param ancestry: A set of revisions that we care about
    :param graph: A Graph which lets us find the parents for a revision
    :param tip_revno: The revision number for the tip revision
    :param tip: The tip of the ancsetry
    :param backward: Show oldest versions first when True, newest versions
        first when False.
    :return: [(revno, revision_id)] for all revisions in ancestry that
        are parents from tip, or None if ancestry is None.
    """
    if ancestry is None:
        return None
    if not ancestry: #Empty ancestry, no need to do any work
        return []

    merge_sorted_revisions = branch.iter_merge_sorted_revisions()
    # Now that we got the correct revnos, keep only the relevant
    # revisions.
    merge_sorted_revisions = [
        # log.reverse_by_depth expects seq_num to be present, but it is
        # stripped by iter_merge_sorted_revisions()
        (0, revid, n, d, e) for revid, n, d, e in merge_sorted_revisions
        if revid in ancestry]
    if not backward:
        merge_sorted_revisions = log.reverse_by_depth(merge_sorted_revisions)
    revline = []
    for seq, rev_id, merge_depth, revno, end_of_merge in merge_sorted_revisions:
        revline.append(('.'.join(map(str, revno)), rev_id, merge_depth))
    return revline


def _filter_revs(graph, revs, revid_range):
    if revid_range is None or revs is None:
        return revs
    return [rev for rev in revs
        if graph.is_between(rev[1], revid_range[0], revid_range[1])]


def _find_unmerged(local_branch, remote_branch, restrict,
                   include_merged, backward,
                   local_revid_range=None, remote_revid_range=None):
    """See find_unmerged.

    The branches should already be locked before entering.
    """
    local_revno, local_revision_id = local_branch.last_revision_info()
    remote_revno, remote_revision_id = remote_branch.last_revision_info()
    if local_revno == remote_revno and local_revision_id == remote_revision_id:
        # A simple shortcut when the tips are at the same point
        return [], []
    graph = local_branch.repository.get_graph(remote_branch.repository)
    if restrict == 'remote':
        local_extra = None
        remote_extra = graph.find_unique_ancestors(remote_revision_id,
                                                   [local_revision_id])
    elif restrict == 'local':
        remote_extra = None
        local_extra = graph.find_unique_ancestors(local_revision_id,
                                                  [remote_revision_id])
    else:
        if restrict != 'all':
            raise ValueError('param restrict not one of "all", "local",'
                             ' "remote": %r' % (restrict,))
        local_extra, remote_extra = graph.find_difference(local_revision_id,
                                                          remote_revision_id)
    if include_merged:
        locals = _enumerate_with_merges(local_branch, local_extra,
                                        graph, local_revno,
                                        local_revision_id, backward)
        remotes = _enumerate_with_merges(remote_branch, remote_extra,
                                         graph, remote_revno,
                                         remote_revision_id, backward)
    else:
        # Now that we have unique ancestors, compute just the mainline, and
        # generate revnos for them.
        locals = _enumerate_mainline(local_extra, graph, local_revno,
                                     local_revision_id, backward)
        remotes = _enumerate_mainline(remote_extra, graph, remote_revno,
                                      remote_revision_id, backward)
    return _filter_revs(graph, locals, local_revid_range), _filter_revs(graph,
        remotes, remote_revid_range)


def sorted_revisions(revisions, history_map):
    revisions = [(history_map[r],r) for r in revisions]
    revisions.sort()
    return revisions
