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

"""Helper functions for DirState.

This is the python implementation for DirState functions.
"""

from bzrlib.dirstate import DirState


cdef extern from *:
    ctypedef int size_t

cdef extern from "Python.h":
    # GetItem returns a borrowed reference
    void *PyDict_GetItem(object p, object key)
    int PyDict_SetItem(object p, object key, object val) except -1
    void *PyList_GetItem_object_void "PyList_GET_ITEM" (object lst, int index)
    void *PyTuple_GetItem_void_void "PyTuple_GET_ITEM" (void* tpl, int index)
    object PyUnicode_Split_void_object "PyUnicode_Split" (void* str, )
    int PyList_CheckExact(object)
    int PyTuple_CheckExact(object)

    char *PyString_AsString(object p)
    char *PyString_AsString_void "PyString_AsString" (void *p)
    int PyString_Size(object p)
    int PyString_Size_void "PyString_Size" (void *p)
    int PyString_CheckExact(object p)

    void Py_INCREF(object)
    void Py_DECREF(object)

cdef extern from "string.h":
    int strncmp(char *s1, char *s2, size_t len)
    int strcmp(char *s1, char *s2)
    char *strchr(char *s1, char c)


cdef object _split_from_path(object cache, object path):
    """get the dirblock tuple for a given path.

    :param cache: A Dictionary mapping string paths to tuples
    :param path: The path we care about.
    :return: A borrowed reference to a tuple stored in cache.
        You do not need to Py_DECREF() when you are done, unless you plan on
        using it for a while.
    """
    cdef void* value_ptr
    cdef object value

    value_ptr = PyDict_GetItem(cache, path)
    if value_ptr == NULL:
        value = path.split('/')
        cache[path] = value
    else:
        value = <object>value_ptr

    return value


cdef int _cmp_dirblock_strings(char *path1, int size1, char *path2, int size2):
    """This compares 2 strings separating on path sections.

    This is equivalent to "cmp(path1.split('/'), path2.split('/'))"
    However, we don't want to create an extra object for doing the split.

    :param path1: The first path to compare
    :param size1: The length of the first path
    :param path2: The second path
    :param size1: The length of the second path
    :return: 0 if they are equal, -1 if path1 comes first, 1 if path2 comes
        first
    """
    cdef char *base1
    cdef char *base2
    cdef char *tip1
    cdef char *tip2
    cdef char *end1
    cdef char *end2
    cdef int cur_len1
    cdef int cur_len2
    cdef int cmp_len
    cdef int diff

    base1 = path1
    base2 = path2
    end1 = base1 + size1
    end2 = base2 + size2

    # Ensure that we are pointing to the final NULL terminator on both ends
    assert end1[0] == c'\x00'
    assert end2[0] == c'\x00'

    while base1 < end1 and base2 < end2:
        # Find the next path separator
        # (This is where you would like strchrnul)
        tip1 = strchr(base1, c'/')
        tip2 = strchr(base2, c'/')

        if tip1 == NULL:
            tip1 = end1
        if tip2 == NULL:
            tip2 = end2

        cur_len1 = tip1 - base1
        cur_len2 = tip2 - base2
        cmp_len = cur_len1
        if cur_len2 < cur_len1:
            cmp_len = cur_len2

        diff = strncmp(base1, base2, cmp_len)
        # print 'comparing "%s", "%s", %d = %d' % (base1, base2, cmp_len, diff)
        if diff != 0:
            return diff
        if cur_len1 < cur_len2:
            return -1
        elif cur_len1 > cur_len2:
            return 1
        base1 = tip1+1
        base2 = tip2+1
    # Do we still have uncompared characters?
    if base1 < end1:
        return 1
    if base2 < end2:
        return -1
    return 0


def cmp_dirblock_strings(path1, path2):
    """Compare to python strings in dirblock fashion."""
    return _cmp_dirblock_strings(PyString_AsString(path1),
                                 PyString_Size(path1),
                                 PyString_AsString(path2),
                                 PyString_Size(path2))


def bisect_dirblock(dirblocks, dirname, lo=0, hi=None, cache=None):
    """Return the index where to insert dirname into the dirblocks.

    The return value idx is such that all directories blocks in dirblock[:idx]
    have names < dirname, and all blocks in dirblock[idx:] have names >=
    dirname.

    Optional args lo (default 0) and hi (default len(dirblocks)) bound the
    slice of a to be searched.
    """
    cdef int _lo
    cdef int _hi
    cdef int _mid
    cdef char *dirname_str
    cdef int dirname_size
    cdef char *cur_str
    cdef int cur_size
    cdef void *cur

    if hi is None:
        _hi = len(dirblocks)
    else:
        _hi = hi

    if not PyList_CheckExact(dirblocks):
        raise TypeError('you must pass a python list for dirblocks')
    _lo = lo
    if not PyString_CheckExact(dirname):
        raise TypeError('you must pass a string for dirname')
    dirname_str = PyString_AsString(dirname)
    dirname_size = PyString_Size(dirname)

    while _lo < _hi:
        _mid = (_lo+_hi)/2
        # Grab the dirname for the current dirblock
        # cur = dirblocks[_mid][0]
        cur = PyTuple_GetItem_void_void(
                PyList_GetItem_object_void(dirblocks, _mid), 0)
        cur_str = PyString_AsString_void(cur)
        cur_size = PyString_Size_void(cur)
        if _cmp_dirblock_strings(cur_str, cur_size,
                                 dirname_str, dirname_size) < 0:
            _lo = _mid+1
        else:
            _hi = _mid
    return _lo


def _read_dirblocks(state):
    """Read in the dirblocks for the given DirState object.

    This is tightly bound to the DirState internal representation. It should be
    thought of as a member function, which is only separated out so that we can
    re-write it in pyrex.

    :param state: A DirState object.
    :return: None
    """
    cdef int pos
    cdef int entry_size
    cdef int field_count

    state._state_file.seek(state._end_of_header)
    text = state._state_file.read()
    # TODO: check the crc checksums. crc_measured = zlib.crc32(text)

    fields = text.split('\0')
    # Remove the last blank entry
    trailing = fields.pop()
    assert trailing == ''
    # consider turning fields into a tuple.

    # skip the first field which is the trailing null from the header.
    cur = 1
    # Each line now has an extra '\n' field which is not used
    # so we just skip over it
    # entry size:
    #  3 fields for the key
    #  + number of fields per tree_data (5) * tree count
    #  + newline
    num_present_parents = state._num_present_parents()
    tree_count = 1 + num_present_parents
    entry_size = state._fields_per_entry()
    expected_field_count = entry_size * state._num_entries
    field_count = len(fields)
    # this checks our adjustment, and also catches file too short.
    assert field_count - cur == expected_field_count, \
        'field count incorrect %s != %s, entry_size=%s, '\
        'num_entries=%s fields=%r' % (
            field_count - cur, expected_field_count, entry_size,
            state._num_entries, fields)

    if num_present_parents == 1:
        # Bind external functions to local names
        _int = int
        # We access all fields in order, so we can just iterate over
        # them. Grab an straight iterator over the fields. (We use an
        # iterator because we don't want to do a lot of additions, nor
        # do we want to do a lot of slicing)
        next = iter(fields).next
        # Move the iterator to the current position
        for x in xrange(cur):
            next()
        # The two blocks here are deliberate: the root block and the
        # contents-of-root block.
        state._dirblocks = [('', []), ('', [])]
        current_block = state._dirblocks[0][1]
        current_dirname = ''
        append_entry = current_block.append
        for count in xrange(state._num_entries):
            dirname = next()
            name = next()
            file_id = next()
            if dirname != current_dirname:
                # new block - different dirname
                current_block = []
                current_dirname = dirname
                state._dirblocks.append((current_dirname, current_block))
                append_entry = current_block.append
            # we know current_dirname == dirname, so re-use it to avoid
            # creating new strings
            entry = ((current_dirname, name, file_id),
                     [(# Current Tree
                         next(),                # minikind
                         next(),                # fingerprint
                         _int(next()),          # size
                         next() == 'y',         # executable
                         next(),                # packed_stat or revision_id
                     ),
                     ( # Parent 1
                         next(),                # minikind
                         next(),                # fingerprint
                         _int(next()),          # size
                         next() == 'y',         # executable
                         next(),                # packed_stat or revision_id
                     ),
                     ])
            trailing = next()
            assert trailing == '\n'
            # append the entry to the current block
            append_entry(entry)
        state._split_root_dirblock_into_contents()
    else:

        fields_to_entry = state._get_fields_to_entry()
        entries = []
        entries_append = entries.append
        pos = cur
        entry_size = entry_size
        while pos < field_count:
            entries_append(fields_to_entry(fields[pos:pos+entry_size]))
            pos = pos + entry_size
        state._entries_to_current_state(entries)
    # To convert from format 2  => format 3
    # state._dirblocks = sorted(state._dirblocks,
    #                          key=lambda blk:blk[0].split('/'))
    # To convert from format 3 => format 2
    # state._dirblocks = sorted(state._dirblocks)
    state._dirblock_state = DirState.IN_MEMORY_UNMODIFIED
