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

"""Implementation of Graph algorithms when we have already loaded everything.
"""

import heapq

from bzrlib import (
    revision,
    )


class _KnownGraphNode(object):
    """Represents a single object in the known graph."""

    __slots__ = ('key', 'parent_keys', 'child_keys', 'linear_dominator',
                 'gdfo', 'ancestor_of')

    def __init__(self, key, parent_keys):
        self.key = key
        self.parent_keys = parent_keys
        self.child_keys = []
        # oldest ancestor, such that no parents between here and there have >1
        # child or >1 parent.
        self.linear_dominator = None
        # Greatest distance from origin
        self.gdfo = None
        # This will become a tuple of known heads that have this node as an
        # ancestor
        self.ancestor_of = None

    def __repr__(self):
        return '%s(%s  gdfo:%s par:%s child:%s %s)' % (
            self.__class__.__name__, self.key, self.gdfo,
            self.parent_keys, self.child_keys,
            self.linear_dominator)


class KnownGraph(object):
    """This is a class which assumes we already know the full graph."""

    def __init__(self, parent_map, do_cache=True):
        """Create a new KnownGraph instance.

        :param parent_map: A dictionary mapping key => parent_keys
        """
        self._nodes = {}
        # Maps {sorted(revision_id, revision_id): heads}
        self._known_heads = {}
        self.do_cache = do_cache
        self._initialize_nodes(parent_map)
        self._find_linear_dominators()
        self._find_gdfo()

    def _initialize_nodes(self, parent_map):
        """Populate self._nodes.

        After this has finished, self._nodes will have an entry for every entry
        in parent_map. Ghosts will have a parent_keys = None, all nodes found
        will also have .child_keys populated with all known child_keys.
        """
        nodes = self._nodes
        for key, parent_keys in parent_map.iteritems():
            if key in nodes:
                node = nodes[key]
                node.parent_keys = parent_keys
            else:
                node = _KnownGraphNode(key, parent_keys)
                nodes[key] = node
            for parent_key in parent_keys:
                try:
                    parent_node = nodes[parent_key]
                except KeyError:
                    parent_node = _KnownGraphNode(parent_key, None)
                    nodes[parent_key] = parent_node
                parent_node.child_keys.append(key)

    def _find_linear_dominators(self):
        """For each node in the set, find any linear dominators.

        For any given node, the 'linear dominator' is an ancestor, such that
        all parents between this node and that one have a single parent, and a
        single child. So if A->B->C->D then B,C,D all have a linear dominator
        of A.

        There are two main benefits:
        1) When walking the graph, we can jump to the nearest linear dominator,
           rather than walking all of the nodes inbetween.
        2) When caching heads() results, dominators give the "same" results as
           their children. (If the dominator is a head, then the descendant is
           a head, if the dominator is not a head, then the child isn't
           either.)
        """
        def check_node(node):
            if node.parent_keys is None or len(node.parent_keys) != 1:
                # This node is either a ghost, a tail, or has multiple parents
                # It its own dominator
                node.linear_dominator = node.key
                return None
            parent_node = self._nodes[node.parent_keys[0]]
            if len(parent_node.child_keys) > 1:
                # The parent has multiple children, so *this* node is the
                # dominator
                node.linear_dominator = node.key
                return None
            # The parent is already filled in, so add and continue
            if parent_node.linear_dominator is not None:
                node.linear_dominator = parent_node.linear_dominator
                return None
            # We don't know this node, or its parent node, so start walking to
            # next
            return parent_node

        for node in self._nodes.itervalues():
            # The parent is not filled in, so walk until we get somewhere
            if node.linear_dominator is not None: #already done
                continue
            next_node = check_node(node)
            if next_node is None:
                # Nothing more needs to be done
                continue
            stack = []
            while next_node is not None:
                stack.append(node)
                node = next_node
                next_node = check_node(node)
            # The stack now contains the linear chain, and 'node' should have
            # been labeled
            dominator = node.linear_dominator
            while stack:
                next_node = stack.pop()
                next_node.linear_dominator = dominator
                node = next_node

    def _find_gdfo(self):
        def find_tails():
            return [node for node in self._nodes.itervalues()
                       if not node.parent_keys]
        tails = find_tails()
        todo = []
        heappush = heapq.heappush
        heappop = heapq.heappop
        nodes = self._nodes
        for node in tails:
            node.gdfo = 1
            heappush(todo, (1, node))
        processed = 0
        while todo:
            gdfo, next = heappop(todo)
            processed += 1
            if next.gdfo is not None and gdfo < next.gdfo:
                # This node was reached from a longer path, we assume it was
                # enqued correctly with the longer gdfo, so don't continue
                # processing now
                continue
            next_gdfo = gdfo + 1
            for child_key in next.child_keys:
                child_node = nodes[child_key]
                if child_node.gdfo is None or child_node.gdfo < next_gdfo:
                    # Only enque children when all of their parents have been
                    # resolved
                    for parent_key in child_node.parent_keys:
                        # We know that 'this' parent is counted
                        if parent_key != next.key:
                            parent_node = nodes[parent_key]
                            if parent_node.gdfo is None:
                                break
                    else:
                        child_node.gdfo = next_gdfo
                        heappush(todo, (next_gdfo, child_node))

    def _get_dominators_to_nodes(self, candidate_nodes):
        """Get the reverse mapping from dominator_key => candidate_nodes.

        As a side effect, this can also remove potential candidate nodes if we
        determine that they share a dominator.
        """
        dom_to_node = {}
        keys_to_remove = []
        for node in candidate_nodes.values():
            if node.linear_dominator in dom_to_node:
                # This node already exists, resolve which node supersedes the
                # other
                other_node = dom_to_node[node.linear_dominator]
                # There should be no way that nodes sharing a dominator could
                # 'tie' for gdfo
                if other_node.gdfo > node.gdfo:
                    # The other node has this node as an ancestor
                    keys_to_remove.append(node.key)
                else:
                    # Replace the other node, and set this as the new key
                    keys_to_remove.append(other_node.key)
                    dom_to_node[node.linear_dominator] = node
            else:
                dom_to_node[node.linear_dominator] = node
        for key in keys_to_remove:
            candidate_nodes.pop(key)
        return dom_to_node

    def heads(self, keys):
        """Return the heads from amongst keys.

        This is done by searching the ancestries of each key.  Any key that is
        reachable from another key is not returned; all the others are.

        This operation scales with the relative depth between any two keys. If
        any two keys are completely disconnected all ancestry of both sides
        will be retrieved.

        :param keys: An iterable of keys.
        :return: A set of the heads. Note that as a set there is no ordering
            information. Callers will need to filter their input to create
            order if they need it.
        """
        candidate_nodes = dict((key, self._nodes[key]) for key in keys)
        if revision.NULL_REVISION in candidate_nodes:
            # NULL_REVISION is only a head if it is the only entry
            candidate_nodes.pop(revision.NULL_REVISION)
            if not candidate_nodes:
                return set([revision.NULL_REVISION])
        if len(candidate_nodes) < 2:
            return frozenset(candidate_nodes)
        heads_key = frozenset(candidate_nodes)
        if heads_key != frozenset(keys):
            note('%s != %s', heads_key, frozenset(keys))
        try:
            heads = self._known_heads[heads_key]
            return heads
        except KeyError:
            pass # compute it ourselves
        dom_to_node = self._get_dominators_to_nodes(candidate_nodes)
        if len(candidate_nodes) < 2:
            # We shrunk candidate_nodes and determined a new head
            return frozenset(candidate_nodes)
        dom_heads_key = None
        # Check the linear dominators of these keys, to see if we already
        # know the heads answer
        dom_heads_key = frozenset([node.linear_dominator
                                   for node in candidate_nodes.itervalues()])
        if dom_heads_key in self._known_heads:
            # map back into the original keys
            heads = self._known_heads[dom_heads_key]
            heads = frozenset([dom_to_node[key].key for key in heads])
            return heads
        heads = self._heads_from_candidate_nodes(candidate_nodes, dom_to_node)
        if self.do_cache:
            self._known_heads[heads_key] = heads
            # Cache the dominator heads
            if dom_heads_key is not None:
                dom_heads = frozenset([candidate_nodes[key].linear_dominator
                                       for key in heads])
                self._known_heads[dom_heads_key] = dom_heads
        return heads

    def _heads_from_candidate_nodes(self, candidate_nodes, dom_to_node):
        queue = []
        to_cleanup = []
        to_cleanup_append = to_cleanup.append
        for node in candidate_nodes.itervalues():
            node.ancestor_of = (node.key,)
            queue.append((-node.gdfo, node))
            to_cleanup_append(node)
        heapq.heapify(queue)
        # These are nodes that we determined are 'common' that we are no longer
        # walking
        # Now we walk nodes until all nodes that are being walked are 'common'
        num_candidates = len(candidate_nodes)
        nodes = self._nodes
        heappop = heapq.heappop
        heappush = heapq.heappush
        while queue and len(candidate_nodes) > 1:
            _, node = heappop(queue)
            next_ancestor_of = node.ancestor_of
            if len(next_ancestor_of) == num_candidates:
                # This node is now considered 'common'
                # Make sure all parent nodes are marked as such
                for parent_key in node.parent_keys:
                    parent_node = nodes[parent_key]
                    if parent_node.ancestor_of is not None:
                        parent_node.ancestor_of = next_ancestor_of
                if node.linear_dominator != node.key:
                    parent_node = nodes[node.linear_dominator]
                    if parent_node.ancestor_of is not None:
                        parent_node.ancestor_of = next_ancestor_of
                continue
            if node.parent_keys is None:
                # This is a ghost
                continue
            # Now project the current nodes ancestor list to the parent nodes,
            # and queue them up to be walked
            # Note: using linear_dominator speeds things up quite a bit
            #       enough that we actually start to be slightly faster
            #       than the default heads() implementation
            if node.linear_dominator != node.key:
                # We are at the tip of a long linear region
                # We know that there is nothing between here and the tail
                # that is interesting, so skip to the end
                parent_keys = [node.linear_dominator]
            else:
                parent_keys = node.parent_keys
            for parent_key in parent_keys:
                if parent_key in candidate_nodes:
                    candidate_nodes.pop(parent_key)
                    if len(candidate_nodes) <= 1:
                        break
                elif parent_key in dom_to_node:
                    orig_node = dom_to_node[parent_key]
                    if orig_node is not node:
                        if orig_node.key in candidate_nodes:
                            candidate_nodes.pop(orig_node.key)
                            if len(candidate_nodes) <= 1:
                                break
                parent_node = nodes[parent_key]
                ancestor_of = parent_node.ancestor_of
                if ancestor_of is None:
                    # This node hasn't been walked yet
                    parent_node.ancestor_of = next_ancestor_of
                    # Enqueue this node
                    heappush(queue, (-parent_node.gdfo, parent_node))
                    to_cleanup_append(parent_node)
                elif ancestor_of != next_ancestor_of:
                    # Combine to get the full set of parents
                    all_ancestors = set(ancestor_of)
                    all_ancestors.update(next_ancestor_of)
                    parent_node.ancestor_of = tuple(sorted(all_ancestors))
        def cleanup():
            for node in to_cleanup:
                node.ancestor_of = None
        cleanup()
        return frozenset(candidate_nodes)