# Copyright (C) 2007 Canonical Ltd
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

"""Indexing facilities."""

__all__ = [
    'CombinedGraphIndex',
    'GraphIndex',
    'GraphIndexBuilder',
    'InMemoryGraphIndex',
    ]

from cStringIO import StringIO
import re

from bzrlib import errors

_OPTION_NODE_REFS = "node_ref_lists="
_SIGNATURE = "Bazaar Graph Index 1\n"


_whitespace_re = re.compile('[\t\n\x0b\x0c\r\x00 ]')
_newline_null_re = re.compile('[\n\0]')


class GraphIndexBuilder(object):
    """A builder that can build a GraphIndex.
    
    The resulting graph has the structure:
    
    _SIGNATURE OPTIONS NODES NEWLINE
    _SIGNATURE     := 'Bazaar Graph Index 1' NEWLINE
    OPTIONS        := 'node_ref_lists=' DIGITS NEWLINE
    NODES          := NODE*
    NODE           := KEY NULL ABSENT? NULL REFERENCES NULL VALUE NEWLINE
    KEY            := Not-whitespace-utf8
    ABSENT         := 'a'
    REFERENCES     := REFERENCE_LIST (TAB REFERENCE_LIST){node_ref_lists - 1}
    REFERENCE_LIST := (REFERENCE (CR REFERENCE)*)?
    REFERENCE      := DIGITS  ; digits is the byte offset in the index of the
                              ; referenced key.
    VALUE          := no-newline-no-null-bytes
    """

    def __init__(self, reference_lists=0):
        """Create a GraphIndex builder.

        :param reference_lists: The number of node references lists for each
            entry.
        """
        self.reference_lists = reference_lists
        self._nodes = {}

    def add_node(self, key, references, value):
        """Add a node to the index.

        :param key: The key. keys must be whitespace free utf8.
        :param references: An iterable of iterables of keys. Each is a
            reference to another key.
        :param value: The value to associate with the key. It may be any
            bytes as long as it does not contain \0 or \n.
        """
        if not key or _whitespace_re.search(key) is not None:
            raise errors.BadIndexKey(key)
        if _newline_null_re.search(value) is not None:
            raise errors.BadIndexValue(value)
        if len(references) != self.reference_lists:
            raise errors.BadIndexValue(references)
        node_refs = []
        for reference_list in references:
            for reference in reference_list:
                if _whitespace_re.search(reference) is not None:
                    raise errors.BadIndexKey(reference)
                if reference not in self._nodes:
                    self._nodes[reference] = ('a', (), '')
            node_refs.append(tuple(reference_list))
        if key in self._nodes and self._nodes[key][0] == '':
            raise errors.BadIndexDuplicateKey(key, self)
        self._nodes[key] = ('', tuple(node_refs), value)

    def finish(self):
        lines = [_SIGNATURE]
        lines.append(_OPTION_NODE_REFS + str(self.reference_lists) + '\n')
        prefix_length = len(lines[0]) + len(lines[1])
        # references are byte offsets. To avoid having to do nasty
        # polynomial work to resolve offsets (references to later in the 
        # file cannot be determined until all the inbetween references have
        # been calculated too) we pad the offsets with 0's to make them be
        # of consistent length. Using binary offsets would break the trivial
        # file parsing.
        # to calculate the width of zero's needed we do three passes:
        # one to gather all the non-reference data and the number of references.
        # one to pad all the data with reference-length and determine entry
        # addresses.
        # One to serialise.
        
        # forward sorted by key. In future we may consider topological sorting,
        # at the cost of table scans for direct lookup, or a second index for
        # direct lookup
        nodes = sorted(self._nodes.items())
        # if we do not prepass, we don't know how long it will be up front.
        expected_bytes = None
        # we only need to pre-pass if we have reference lists at all.
        if self.reference_lists:
            key_offset_info = []
            non_ref_bytes = prefix_length
            total_references = 0
            # TODO use simple multiplication for the constants in this loop.
            for key, (absent, references, value) in nodes:
                # record the offset known *so far* for this key:
                # the non reference bytes to date, and the total references to
                # date - saves reaccumulating on the second pass
                key_offset_info.append((key, non_ref_bytes, total_references))
                # key is literal, value is literal, there are 3 null's, 1 NL
                non_ref_bytes += len(key) + len(value) + 3 + 1
                # one byte for absent if set.
                if absent:
                    non_ref_bytes += 1
                elif self.reference_lists:
                    # (ref_lists -1) tabs
                    non_ref_bytes += self.reference_lists - 1
                    # (ref-1 cr's per ref_list)
                    for ref_list in references:
                        # how many references across the whole file?
                        total_references += len(ref_list)
                        # accrue reference separators
                        if ref_list:
                            non_ref_bytes += len(ref_list) - 1
            # how many digits are needed to represent the total byte count?
            digits = 1
            possible_total_bytes = non_ref_bytes + total_references*digits
            while 10 ** digits < possible_total_bytes:
                digits += 1
                possible_total_bytes = non_ref_bytes + total_references*digits
            expected_bytes = possible_total_bytes + 1 # terminating newline
            # resolve key addresses.
            key_addresses = {}
            for key, non_ref_bytes, total_references in key_offset_info:
                key_addresses[key] = non_ref_bytes + total_references*digits
            # serialise
            format_string = '%%0%sd' % digits
        for key, (absent, references, value) in nodes:
            flattened_references = []
            for ref_list in references:
                ref_addresses = []
                for reference in ref_list:
                    ref_addresses.append(format_string % key_addresses[reference])
                flattened_references.append('\r'.join(ref_addresses))
            lines.append("%s\0%s\0%s\0%s\n" % (key, absent,
                '\t'.join(flattened_references), value))
        lines.append('\n')
        result = StringIO(''.join(lines))
        if expected_bytes and len(result.getvalue()) != expected_bytes:
            raise errors.BzrError('Failed index creation. Internal error:'
                ' mismatched output length and expected length: %d %d' %
                (len(result.getvalue()), expected_bytes))
        return StringIO(''.join(lines))


class GraphIndex(object):
    """An index for data with embedded graphs.
 
    The index maps keys to a list of key reference lists, and a value.
    Each node has the same number of key reference lists. Each key reference
    list can be empty or an arbitrary length. The value is an opaque NULL
    terminated string without any newlines.

    It is presumed that the index will not be mutated - it is static data.

    Currently successive iter_entries/iter_all_entries calls will read the
    entire index each time. Additionally iter_entries calls will read the
    entire index always. XXX: This must be fixed before the index is 
    suitable for production use. :XXX
    """

    def __init__(self, transport, name):
        """Open an index called name on transport.

        :param transport: A bzrlib.transport.Transport.
        :param name: A path to provide to transport API calls.
        """
        self._transport = transport
        self._name = name

    def iter_all_entries(self):
        """Iterate over all keys within the index.

        :return: An iterable of (key, reference_lists, value). There is no
            defined order for the result iteration - it will be in the most
            efficient order for the index.
        """
        stream = self._transport.get(self._name)
        self._read_prefix(stream)
        line_count = 0
        self.keys_by_offset = {}
        trailers = 0
        pos = stream.tell()
        for line in stream.readlines():
            if line == '\n':
                trailers += 1
                continue
            key, absent, references, value = line.split('\0')
            value = value[:-1] # remove the newline
            ref_lists = []
            for ref_string in references.split('\t'):
                ref_lists.append(tuple([
                    int(ref) for ref in ref_string.split('\r') if ref
                    ]))
            ref_lists = tuple(ref_lists)
            self.keys_by_offset[pos] = (key, absent, ref_lists, value)
            pos += len(line)
        for key, absent, references, value in self.keys_by_offset.itervalues():
            if absent:
                continue
            # resolve references:
            if self.node_ref_lists:
                node_refs = []
                for ref_list in references:
                    node_refs.append(tuple([self.keys_by_offset[ref][0] for ref in ref_list]))
                node_refs = tuple(node_refs)
            else:
                node_refs = ()
            yield (key, node_refs, value)
        if trailers != 1:
            # there must be one line - the empty trailer line.
            raise errors.BadIndexData(self)

    def _read_prefix(self, stream):
        signature = stream.read(len(self._signature()))
        if not signature == self._signature():
            raise errors.BadIndexFormatSignature(self._name, GraphIndex)
        options_line = stream.readline()
        if not options_line.startswith(_OPTION_NODE_REFS):
            raise errors.BadIndexOptions(self)
        try:
            self.node_ref_lists = int(options_line[len(_OPTION_NODE_REFS):-1])
        except ValueError:
            raise errors.BadIndexOptions(self)

    def iter_entries(self, keys):
        """Iterate over keys within the index.

        :param keys: An iterable providing the keys to be retrieved.
        :return: An iterable of (key, reference_lists, value). There is no
            defined order for the result iteration - it will be in the most
            efficient order for the index.
        """
        keys = set(keys)
        for node in self.iter_all_entries():
            if node[0] in keys:
                yield node

    def _signature(self):
        """The file signature for this index type."""
        return _SIGNATURE

    def validate(self):
        """Validate that everything in the index can be accessed."""
        # iter_all validates completely at the moment, so just do that.
        for node in self.iter_all_entries():
            pass


class CombinedGraphIndex(object):
    """A GraphIndex made up from smaller GraphIndices.
    
    The backing indices must implement GraphIndex, and are presumed to be
    static data.
    """

    def __init__(self, indices):
        """Create a CombinedGraphIndex backed by indices.

        :param indices: The indices to query for data.
        """
        self._indices = indices

    def insert_index(self, pos, index):
        """Insert a new index in the list of indices to query.

        :param pos: The position to insert the index.
        :param index: The index to insert.
        """
        self._indices.insert(pos, index)

    def iter_all_entries(self):
        """Iterate over all keys within the index

        :return: An iterable of (key, reference_lists, value). There is no
            defined order for the result iteration - it will be in the most
            efficient order for the index.
        """
        seen_keys = set()
        for index in self._indices:
            for node in index.iter_all_entries():
                if node[0] not in seen_keys:
                    yield node
                    seen_keys.add(node[0])

    def iter_entries(self, keys):
        """Iterate over keys within the index.

        :param keys: An iterable providing the keys to be retrieved.
        :return: An iterable of (key, reference_lists, value). There is no
            defined order for the result iteration - it will be in the most
            efficient order for the index.
        """
        keys = set(keys)
        for index in self._indices:
            for node in index.iter_entries(keys):
                keys.remove(node[0])
                yield node

    def validate(self):
        """Validate that everything in the index can be accessed."""
        for index in self._indices:
            index.validate()


class InMemoryGraphIndex(GraphIndexBuilder):
    """A GraphIndex which operates entirely out of memory and is mutable.

    This is designed to allow the accumulation of GraphIndex entries during a
    single write operation, where the accumulated entries need to be immediately
    available - for example via a CombinedGraphIndex.
    """

    def add_nodes(self, nodes):
        """Add nodes to the index.

        :param nodes: An iterable of (key, node_refs, value) entries to add.
        """
        for (key, node_refs, value) in nodes:
            self.add_node(key, node_refs, value)

    def iter_all_entries(self):
        """Iterate over all keys within the index

        :return: An iterable of (key, reference_lists, value). There is no
            defined order for the result iteration - it will be in the most
            efficient order for the index (in this case dictionary hash order).
        """
        for key, (absent, references, value) in self._nodes.iteritems():
            if not absent:
                yield key, references, value

    def iter_entries(self, keys):
        """Iterate over keys within the index.

        :param keys: An iterable providing the keys to be retrieved.
        :return: An iterable of (key, reference_lists, value). There is no
            defined order for the result iteration - it will be in the most
            efficient order for the index (keys iteration order in this case).
        """
        keys = set(keys)
        for key in keys.intersection(self._nodes):
            node = self._nodes[key]
            if not node[0]:
                yield key, node[1], node[2]

    def validate(self):
        """In memory index's have no known corruption at the moment."""
