# Copyright (C) 2005 Canonical Ltd

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


import sys
import os

import bzrlib
from bzrlib.trace import mutter, note
from bzrlib.osutils import isdir, quotefn, compact_date, rand_bytes, \
     splitpath, \
     sha_file, appendpath, file_kind

from bzrlib.errors import BzrError, InvalidRevisionNumber, InvalidRevisionId, \
     DivergedBranches, NotBranchError
from bzrlib.textui import show_status
from bzrlib.revision import Revision
from bzrlib.delta import compare_trees
from bzrlib.tree import EmptyTree, RevisionTree
import bzrlib.xml
import bzrlib.ui



BZR_BRANCH_FORMAT = "Bazaar-NG branch, format 0.0.4\n"
## TODO: Maybe include checks for common corruption of newlines, etc?


# TODO: Some operations like log might retrieve the same revisions
# repeatedly to calculate deltas.  We could perhaps have a weakref
# cache in memory to make this faster.

# TODO: please move the revision-string syntax stuff out of the branch
# object; it's clutter


def find_branch(f, **args):
    from bzrlib.transport import transport
    from bzrlib.local_transport import LocalTransport
    t = transport(f)
    # FIXME: This is a hack around transport so that
    #        We can search the local directories for
    #        a branch root.
    if args.has_key('init') and args['init']:
        # Don't search if we are init-ing
        return Branch(t, **args)
    if isinstance(t, LocalTransport):
        root = find_branch_root(f)
        if root != f:
            t = transport(root)
    return Branch(t, **args)

def _relpath(base, path):
    """Return path relative to base, or raise exception.

    The path may be either an absolute path or a path relative to the
    current working directory.

    Lifted out of Branch.relpath for ease of testing.

    os.path.commonprefix (python2.4) has a bad bug that it works just
    on string prefixes, assuming that '/u' is a prefix of '/u2'.  This
    avoids that problem."""
    rp = os.path.abspath(path)

    s = []
    head = rp
    while len(head) >= len(base):
        if head == base:
            break
        head, tail = os.path.split(head)
        if tail:
            s.insert(0, tail)
    else:
        raise NotBranchError("path %r is not within branch %r" % (rp, base))

    return os.sep.join(s)
        

def find_branch_root(f=None):
    """Find the branch root enclosing f, or pwd.

    f may be a filename or a URL.

    It is not necessary that f exists.

    Basically we keep looking up until we find the control directory or
    run into the root.  If there isn't one, raises NotBranchError.
    """
    if f == None:
        f = os.getcwd()
    else:
        f = os.path.realpath(f)
    if not os.path.exists(f):
        raise BzrError('%r does not exist' % f)
        

    orig_f = f

    while True:
        if os.path.exists(os.path.join(f, bzrlib.BZRDIR)):
            return f
        head, tail = os.path.split(f)
        if head == f:
            # reached the root, whatever that may be
            raise NotBranchError('%s is not in a branch' % orig_f)
        f = head




######################################################################
# branch objects

class Branch(object):
    """Branch holding a history of revisions.

    base
        Base directory of the branch.

    _lock_mode
        None, or 'r' or 'w'

    _lock_count
        If _lock_mode is true, a positive count of the number of times the
        lock has been taken.

    _lock
        Lock object from bzrlib.lock.
    """
    base = None
    _lock_mode = None
    _lock_count = None
    _lock = None
    cache_root = None
    
    # Map some sort of prefix into a namespace
    # stuff like "revno:10", "revid:", etc.
    # This should match a prefix with a function which accepts
    REVISION_NAMESPACES = {}

    def __init__(self, transport, init=False):
        """Create new branch object at a particular location.

        transport -- A Transport object, defining how to access files.
                (If a string, transport.transport() will be used to
                create a Transport object)
        
        init -- If True, create new control files in a previously
             unversioned directory.  If False, the branch must already
             be versioned.

        In the test suite, creation of new trees is tested using the
        `ScratchBranch` class.
        """
        if isinstance(transport, basestring):
            from transport import transport as get_transport
            transport = get_transport(transport)

        self._transport = transport
        if init:
            self._make_control()
        self._check_format()


    def __str__(self):
        return '%s(%r)' % (self.__class__.__name__, self._transport.base)


    __repr__ = __str__


    def __del__(self):
        if self._lock_mode or self._lock:
            from bzrlib.warnings import warn
            warn("branch %r was not explicitly unlocked" % self)
            self._lock.unlock()

        # TODO: It might be best to do this somewhere else,
        # but it is nice for a Branch object to automatically
        # cache it's information.
        # Alternatively, we could have the Transport objects cache requests
        # See the earlier discussion about how major objects (like Branch)
        # should never expect their __del__ function to run.
        if self.cache_root is not None:
            #from warnings import warn
            #warn("branch %r auto-cleanup of cache files" % self)
            try:
                import shutil
                shutil.rmtree(self.cache_root)
            except:
                pass
            self.cache_root = None

    def _get_base(self):
        if self._transport:
            return self._transport.base
        return None

    base = property(_get_base)


    def lock_write(self):
        # TODO: Upgrade locking to support using a Transport,
        # and potentially a remote locking protocol
        if self._lock_mode:
            if self._lock_mode != 'w':
                from bzrlib.errors import LockError
                raise LockError("can't upgrade to a write lock from %r" %
                                self._lock_mode)
            self._lock_count += 1
        else:
            self._lock = self._transport.lock_write(
                    self._rel_controlfilename('branch-lock'))
            self._lock_mode = 'w'
            self._lock_count = 1


    def lock_read(self):
        if self._lock_mode:
            assert self._lock_mode in ('r', 'w'), \
                   "invalid lock mode %r" % self._lock_mode
            self._lock_count += 1
        else:
            self._lock = self._transport.lock_read(
                    self._rel_controlfilename('branch-lock'))
            self._lock_mode = 'r'
            self._lock_count = 1
                        
    def unlock(self):
        if not self._lock_mode:
            from bzrlib.errors import LockError
            raise LockError('branch %r is not locked' % (self))

        if self._lock_count > 1:
            self._lock_count -= 1
        else:
            self._lock.unlock()
            self._lock = None
            self._lock_mode = self._lock_count = None

    def abspath(self, name):
        """Return absolute filename for something in the branch"""
        return self._transport.abspath(name)

    def relpath(self, path):
        """Return path relative to this branch of something inside it.

        Raises an error if path is not in this branch."""
        return self._transport.relpath(path)


    def _rel_controlfilename(self, file_or_path):
        if isinstance(file_or_path, basestring):
            file_or_path = [file_or_path]
        return [bzrlib.BZRDIR] + file_or_path

    def controlfilename(self, file_or_path):
        """Return location relative to branch."""
        return self._transport.abspath(self._rel_controlfilename(file_or_path))


    def controlfile(self, file_or_path, mode='r'):
        """Open a control file for this branch.

        There are two classes of file in the control directory: text
        and binary.  binary files are untranslated byte streams.  Text
        control files are stored with Unix newlines and in UTF-8, even
        if the platform or locale defaults are different.

        Controlfiles should almost never be opened in write mode but
        rather should be atomically copied and replaced using atomicfile.
        """
        import codecs

        relpath = self._rel_controlfilename(file_or_path)
        #TODO: codecs.open() buffers linewise, so it was overloaded with
        # a much larger buffer, do we need to do the same for getreader/getwriter?
        if mode == 'rb': 
            return self._transport.get(relpath)
        elif mode == 'wb':
            raise BzrError("Branch.controlfile(mode='wb') is not supported, use put_controlfiles")
        elif mode == 'r':
            return codecs.getreader('utf-8')(self._transport.get(relpath), errors='replace')
        elif mode == 'w':
            raise BzrError("Branch.controlfile(mode='w') is not supported, use put_controlfiles")
        else:
            raise BzrError("invalid controlfile mode %r" % mode)

    def put_controlfile(self, path, f, encode=True):
        """Write an entry as a controlfile.

        :param path: The path to put the file, relative to the .bzr control
                     directory
        :param f: A file-like or string object whose contents should be copied.
        :param encode:  If true, encode the contents as utf-8
        """
        self.put_controlfiles([(path, f)], encode=encode)

    def put_controlfiles(self, files, encode=True):
        """Write several entries as controlfiles.

        :param files: A list of [(path, file)] pairs, where the path is the directory
                      underneath the bzr control directory
        :param encode:  If true, encode the contents as utf-8
        """
        import codecs
        ctrl_files = []
        for path, f in files:
            if encode:
                if isinstance(f, basestring):
                    f = f.encode('utf-8', 'replace')
                else:
                    f = codecs.getwriter('utf-8')(f, errors='replace')
            path = self._rel_controlfilename(path)
            ctrl_files.append((path, f))
        self._transport.put_multi(ctrl_files)

    def _make_control(self):
        from bzrlib.inventory import Inventory
        from cStringIO import StringIO
        
        # Create an empty inventory
        sio = StringIO()
        # if we want per-tree root ids then this is the place to set
        # them; they're not needed for now and so ommitted for
        # simplicity.
        bzrlib.xml.serializer_v4.write_inventory(Inventory(), sio)

        dirs = [[], 'text-store', 'inventory-store', 'revision-store']
        files = [('README', 
            "This is a Bazaar-NG control directory.\n"
            "Do not change any files in this directory.\n"),
            ('branch-format', BZR_BRANCH_FORMAT),
            ('revision-history', ''),
            ('merged-patches', ''),
            ('pending-merged-patches', ''),
            ('branch-name', ''),
            ('branch-lock', ''),
            ('pending-merges', ''),
            ('inventory', sio.getvalue())
        ]
        self._transport.mkdir_multi([self._rel_controlfilename(d) for d in dirs])
        self.put_controlfiles(files)
        mutter('created control directory in ' + self._transport.base)

    def _check_format(self):
        """Check this branch format is supported.

        The current tool only supports the current unstable format.

        In the future, we might need different in-memory Branch
        classes to support downlevel branches.  But not yet.
        """
        # This ignores newlines so that we can open branches created
        # on Windows from Linux and so on.  I think it might be better
        # to always make all internal files in unix format.
        fmt = self.controlfile('branch-format', 'r').read()
        fmt = fmt.replace('\r\n', '\n')
        if fmt != BZR_BRANCH_FORMAT:
            raise BzrError('sorry, branch format %r not supported' % fmt,
                           ['use a different bzr version',
                            'or remove the .bzr directory and "bzr init" again'])

        # We know that the format is the currently supported one.
        # So create the rest of the entries.
        from bzrlib.store.compressed_text import CompressedTextStore

        if self._transport.should_cache():
            import tempfile
            self.cache_root = tempfile.mkdtemp(prefix='bzr-cache')
            mutter('Branch %r using caching in %r' % (self, self.cache_root))
        else:
            self.cache_root = None

        def get_store(name):
            relpath = self._rel_controlfilename(name)
            store = CompressedTextStore(self._transport.clone(relpath))
            if self._transport.should_cache():
                from meta_store import CachedStore
                cache_path = os.path.join(self.cache_root, name)
                os.mkdir(cache_path)
                store = CachedStore(store, cache_path)
            return store

        self.text_store = get_store('text-store')
        self.revision_store = get_store('revision-store')
        self.inventory_store = get_store('inventory-store')

    def get_root_id(self):
        """Return the id of this branches root"""
        inv = self.read_working_inventory()
        return inv.root.file_id

    def set_root_id(self, file_id):
        inv = self.read_working_inventory()
        orig_root_id = inv.root.file_id
        del inv._byid[inv.root.file_id]
        inv.root.file_id = file_id
        inv._byid[inv.root.file_id] = inv.root
        for fid in inv:
            entry = inv[fid]
            if entry.parent_id in (None, orig_root_id):
                entry.parent_id = inv.root.file_id
        self._write_inventory(inv)

    def read_working_inventory(self):
        """Read the working inventory."""
        from bzrlib.inventory import Inventory
        self.lock_read()
        try:
            # ElementTree does its own conversion from UTF-8, so open in
            # binary.
            f = self.controlfile('inventory', 'rb')
            return bzrlib.xml.serializer_v4.read_inventory(f)
        finally:
            self.unlock()
            

    def _write_inventory(self, inv):
        """Update the working inventory.

        That is to say, the inventory describing changes underway, that
        will be committed to the next revision.
        """
        from cStringIO import StringIO
        self.lock_write()
        try:
            sio = StringIO()
            bzrlib.xml.serializer_v4.write_inventory(inv, sio)
            sio.seek(0)
            # Transport handles atomicity
            self.put_controlfile('inventory', sio)
        finally:
            self.unlock()
        
        mutter('wrote working inventory')
            

    inventory = property(read_working_inventory, _write_inventory, None,
                         """Inventory for the working copy.""")


    def add(self, files, ids=None):
        """Make files versioned.

        Note that the command line normally calls smart_add instead,
        which can automatically recurse.

        This puts the files in the Added state, so that they will be
        recorded by the next commit.

        files
            List of paths to add, relative to the base of the tree.

        ids
            If set, use these instead of automatically generated ids.
            Must be the same length as the list of files, but may
            contain None for ids that are to be autogenerated.

        TODO: Perhaps have an option to add the ids even if the files do
              not (yet) exist.

        TODO: Perhaps yield the ids and paths as they're added.
        """
        # TODO: Re-adding a file that is removed in the working copy
        # should probably put it back with the previous ID.
        if isinstance(files, basestring):
            assert(ids is None or isinstance(ids, basestring))
            files = [files]
            if ids is not None:
                ids = [ids]

        if ids is None:
            ids = [None] * len(files)
        else:
            assert(len(ids) == len(files))

        self.lock_write()
        try:
            inv = self.read_working_inventory()
            for f,file_id in zip(files, ids):
                if is_control_file(f):
                    raise BzrError("cannot add control file %s" % quotefn(f))

                fp = splitpath(f)

                if len(fp) == 0:
                    raise BzrError("cannot add top-level %r" % f)

                fullpath = os.path.normpath(self.abspath(f))

                try:
                    kind = file_kind(fullpath)
                except OSError:
                    # maybe something better?
                    raise BzrError('cannot add: not a regular file or directory: %s' % quotefn(f))

                if kind != 'file' and kind != 'directory':
                    raise BzrError('cannot add: not a regular file or directory: %s' % quotefn(f))

                if file_id is None:
                    file_id = gen_file_id(f)
                inv.add_path(f, kind=kind, file_id=file_id)

                mutter("add file %s file_id:{%s} kind=%r" % (f, file_id, kind))

            self._write_inventory(inv)
        finally:
            self.unlock()
            

    def print_file(self, file, revno):
        """Print `file` to stdout."""
        self.lock_read()
        try:
            tree = self.revision_tree(self.lookup_revision(revno))
            # use inventory as it was in that revision
            file_id = tree.inventory.path2id(file)
            if not file_id:
                raise BzrError("%r is not present in revision %s" % (file, revno))
            tree.print_file(file_id)
        finally:
            self.unlock()


    def remove(self, files, verbose=False):
        """Mark nominated files for removal from the inventory.

        This does not remove their text.  This does not run on 

        TODO: Refuse to remove modified files unless --force is given?

        TODO: Do something useful with directories.

        TODO: Should this remove the text or not?  Tough call; not
        removing may be useful and the user can just use use rm, and
        is the opposite of add.  Removing it is consistent with most
        other tools.  Maybe an option.
        """
        ## TODO: Normalize names
        ## TODO: Remove nested loops; better scalability
        if isinstance(files, basestring):
            files = [files]

        self.lock_write()

        try:
            tree = self.working_tree()
            inv = tree.inventory

            # do this before any modifications
            for f in files:
                fid = inv.path2id(f)
                if not fid:
                    raise BzrError("cannot remove unversioned file %s" % quotefn(f))
                mutter("remove inventory entry %s {%s}" % (quotefn(f), fid))
                if verbose:
                    # having remove it, it must be either ignored or unknown
                    if tree.is_ignored(f):
                        new_status = 'I'
                    else:
                        new_status = '?'
                    show_status(new_status, inv[fid].kind, quotefn(f))
                del inv[fid]

            self._write_inventory(inv)
        finally:
            self.unlock()


    # FIXME: this doesn't need to be a branch method
    def set_inventory(self, new_inventory_list):
        from bzrlib.inventory import Inventory, InventoryEntry
        inv = Inventory(self.get_root_id())
        for path, file_id, parent, kind in new_inventory_list:
            name = os.path.basename(path)
            if name == "":
                continue
            inv.add(InventoryEntry(file_id, name, kind, parent))
        self._write_inventory(inv)


    def unknowns(self):
        """Return all unknown files.

        These are files in the working directory that are not versioned or
        control files or ignored.
        
        >>> b = ScratchBranch(files=['foo', 'foo~'])
        >>> list(b.unknowns())
        ['foo']
        >>> b.add('foo')
        >>> list(b.unknowns())
        []
        >>> b.remove('foo')
        >>> list(b.unknowns())
        ['foo']
        """
        return self.working_tree().unknowns()


    def append_revision(self, *revision_ids):
        for revision_id in revision_ids:
            mutter("add {%s} to revision-history" % revision_id)

        rev_history = self.revision_history()
        rev_history.extend(revision_ids)

        self.lock_write()
        try:
            self.put_controlfile('revision-history', '\n'.join(rev_history))
        finally:
            self.unlock()


    def get_revision_xml_file(self, revision_id):
        """Return XML file object for revision object."""
        if not revision_id or not isinstance(revision_id, basestring):
            raise InvalidRevisionId(revision_id)

        self.lock_read()
        try:
            try:
                return self.revision_store[revision_id]
            except (IndexError, KeyError):
                raise bzrlib.errors.NoSuchRevision(self, revision_id)
        finally:
            self.unlock()


    #deprecated
    get_revision_xml = get_revision_xml_file


    def get_revision(self, revision_id):
        """Return the Revision object for a named revision"""
        xml_file = self.get_revision_xml_file(revision_id)

        try:
            r = bzrlib.xml.serializer_v4.read_revision(xml_file)
        except SyntaxError, e:
            raise bzrlib.errors.BzrError('failed to unpack revision_xml',
                                         [revision_id,
                                          str(e)])
            
        assert r.revision_id == revision_id
        return r


    def get_revision_delta(self, revno):
        """Return the delta for one revision.

        The delta is relative to its mainline predecessor, or the
        empty tree for revision 1.
        """
        assert isinstance(revno, int)
        rh = self.revision_history()
        if not (1 <= revno <= len(rh)):
            raise InvalidRevisionNumber(revno)

        # revno is 1-based; list is 0-based

        new_tree = self.revision_tree(rh[revno-1])
        if revno == 1:
            old_tree = EmptyTree()
        else:
            old_tree = self.revision_tree(rh[revno-2])

        return compare_trees(old_tree, new_tree)

        
    def get_revisions(self, revision_ids, pb=None):
        """Return the Revision object for a set of named revisions"""
        from bzrlib.revision import Revision
        from bzrlib.xml import unpack_xml

        # TODO: We need to decide what to do here
        # we cannot use a generator with a try/finally, because
        # you cannot guarantee that the caller will iterate through
        # all entries.
        # in the past, get_inventory wasn't even wrapped in a
        # try/finally locking block.
        # We could either lock without the try/finally, or just
        # not lock at all. We are reading entries that should
        # never be updated.
        # I prefer locking with no finally, so that if someone
        # asks for a list of revisions, but doesn't consume them,
        # that is their problem, and they will suffer the consequences
        self.lock_read()
        for xml_file in self.revision_store.get(revision_ids, pb=pb):
            try:
                r = bzrlib.xml.serializer_v4.read_revision(xml_file)
            except SyntaxError, e:
                raise bzrlib.errors.BzrError('failed to unpack revision_xml',
                                             [revision_id,
                                              str(e)])
            yield r
        self.unlock()
            
    def get_revision_sha1(self, revision_id):
        """Hash the stored value of a revision, and return it."""
        # In the future, revision entries will be signed. At that
        # point, it is probably best *not* to include the signature
        # in the revision hash. Because that lets you re-sign
        # the revision, (add signatures/remove signatures) and still
        # have all hash pointers stay consistent.
        # But for now, just hash the contents.
        return bzrlib.osutils.sha_file(self.get_revision_xml(revision_id))


    def get_inventory(self, inventory_id):
        """Get Inventory object by hash.

        TODO: Perhaps for this and similar methods, take a revision
               parameter which can be either an integer revno or a
               string hash.
        """
        f = self.get_inventory_xml_file(inventory_id)
        return bzrlib.xml.serializer_v4.read_inventory(f)


    def get_inventory_xml(self, inventory_id):
        """Get inventory XML as a file object."""
        # Shouldn't this have a read-lock around it?
        # As well as some sort of trap for missing ids?
        return self.inventory_store[inventory_id]

    get_inventory_xml_file = get_inventory_xml
            
    def get_inventories(self, inventory_ids, pb=None, ignore_missing=False):
        """Get Inventory objects by id
        """
        from bzrlib.inventory import Inventory

        # See the discussion in get_revisions for why
        # we don't use a try/finally block here
        self.lock_read()
        for f in self.inventory_store.get(inventory_ids, pb=pb, ignore_missing=ignore_missing):
            if f is not None:
                # TODO: Possibly put a try/except around this to handle
                # read serialization errors
                r = bzrlib.xml.serializer_v4.read_inventory(f)
                yield r
            elif ignore_missing:
                yield None
            else:
                raise bzrlib.errors.NoSuchRevision(self, revision_id)
        self.unlock()

    def get_inventory_sha1(self, inventory_id):
        """Return the sha1 hash of the inventory entry
        """
        return sha_file(self.get_inventory_xml(inventory_id))


    def get_revision_inventory(self, revision_id):
        """Return inventory of a past revision."""
        # bzr 0.0.6 imposes the constraint that the inventory_id
        # must be the same as its revision, so this is trivial.
        if revision_id == None:
            from bzrlib.inventory import Inventory
            return Inventory(self.get_root_id())
        else:
            return self.get_inventory(revision_id)


    def revision_history(self):
        """Return sequence of revision hashes on to this branch.

        >>> ScratchBranch().revision_history()
        []
        """
        self.lock_read()
        try:
            return [l.rstrip('\r\n') for l in
                    self.controlfile('revision-history', 'r').readlines()]
        finally:
            self.unlock()


    def common_ancestor(self, other, self_revno=None, other_revno=None):
        """
        >>> from bzrlib.commit import commit
        >>> sb = ScratchBranch(files=['foo', 'foo~'])
        >>> sb.common_ancestor(sb) == (None, None)
        True
        >>> commit(sb, "Committing first revision", verbose=False)
        >>> sb.common_ancestor(sb)[0]
        1
        >>> clone = sb.clone()
        >>> commit(sb, "Committing second revision", verbose=False)
        >>> sb.common_ancestor(sb)[0]
        2
        >>> sb.common_ancestor(clone)[0]
        1
        >>> commit(clone, "Committing divergent second revision", 
        ...               verbose=False)
        >>> sb.common_ancestor(clone)[0]
        1
        >>> sb.common_ancestor(clone) == clone.common_ancestor(sb)
        True
        >>> sb.common_ancestor(sb) != clone.common_ancestor(clone)
        True
        >>> clone2 = sb.clone()
        >>> sb.common_ancestor(clone2)[0]
        2
        >>> sb.common_ancestor(clone2, self_revno=1)[0]
        1
        >>> sb.common_ancestor(clone2, other_revno=1)[0]
        1
        """
        my_history = self.revision_history()
        other_history = other.revision_history()
        if self_revno is None:
            self_revno = len(my_history)
        if other_revno is None:
            other_revno = len(other_history)
        indices = range(min((self_revno, other_revno)))
        indices.reverse()
        for r in indices:
            if my_history[r] == other_history[r]:
                return r+1, my_history[r]
        return None, None


    def revno(self):
        """Return current revision number for this branch.

        That is equivalent to the number of revisions committed to
        this branch.
        """
        return len(self.revision_history())


    def last_patch(self):
        """Return last patch hash, or None if no history.
        """
        ph = self.revision_history()
        if ph:
            return ph[-1]
        else:
            return None


    def missing_revisions(self, other, stop_revision=None, diverged_ok=False):
        """
        If self and other have not diverged, return a list of the revisions
        present in other, but missing from self.

        >>> from bzrlib.commit import commit
        >>> bzrlib.trace.silent = True
        >>> br1 = ScratchBranch()
        >>> br2 = ScratchBranch()
        >>> br1.missing_revisions(br2)
        []
        >>> commit(br2, "lala!", rev_id="REVISION-ID-1")
        >>> br1.missing_revisions(br2)
        [u'REVISION-ID-1']
        >>> br2.missing_revisions(br1)
        []
        >>> commit(br1, "lala!", rev_id="REVISION-ID-1")
        >>> br1.missing_revisions(br2)
        []
        >>> commit(br2, "lala!", rev_id="REVISION-ID-2A")
        >>> br1.missing_revisions(br2)
        [u'REVISION-ID-2A']
        >>> commit(br1, "lala!", rev_id="REVISION-ID-2B")
        >>> br1.missing_revisions(br2)
        Traceback (most recent call last):
        DivergedBranches: These branches have diverged.
        """
        self_history = self.revision_history()
        self_len = len(self_history)
        other_history = other.revision_history()
        other_len = len(other_history)
        common_index = min(self_len, other_len) -1
        if common_index >= 0 and \
            self_history[common_index] != other_history[common_index]:
            raise DivergedBranches(self, other)

        if stop_revision is None:
            stop_revision = other_len
        elif stop_revision > other_len:
            raise bzrlib.errors.NoSuchRevision(self, stop_revision)
        
        return other_history[self_len:stop_revision]


    def update_revisions(self, other, stop_revision=None):
        """Pull in all new revisions from other branch.
        """
        from bzrlib.fetch import greedy_fetch
        from bzrlib.revision import get_intervening_revisions

        pb = bzrlib.ui.ui_factory.progress_bar()
        pb.update('comparing histories')
        if stop_revision is None:
            other_revision = other.last_patch()
        else:
            other_revision = other.lookup_revision(stop_revision)
        count = greedy_fetch(self, other, other_revision, pb)[0]
        try:
            revision_ids = self.missing_revisions(other, stop_revision)
        except DivergedBranches, e:
            try:
                revision_ids = get_intervening_revisions(self.last_patch(), 
                                                         other_revision, self)
                assert self.last_patch() not in revision_ids
            except bzrlib.errors.NotAncestor:
                raise e

        self.append_revision(*revision_ids)
        pb.clear()

    def install_revisions(self, other, revision_ids, pb):
        # We are going to iterate this many times, so make sure
        # that it is a list, and not a generator
        revision_ids = list(revision_ids)
        if hasattr(other.revision_store, "prefetch"):
            other.revision_store.prefetch(revision_ids)
        if hasattr(other.inventory_store, "prefetch"):
            other.inventory_store.prefetch(inventory_ids)

        if pb is None:
            pb = bzrlib.ui.ui_factory.progress_bar()
                
        # This entire next section is generally done
        # with either generators, or bulk updates
        inventories = other.get_inventories(revision_ids, ignore_missing=True)
        needed_texts = set()

        failures = set()
        good_revisions = set()
        for i, (inv, rev_id) in enumerate(zip(inventories, revision_ids)):
            pb.update('fetching revision', i+1, len(revision_ids))

            # We don't really need to get the revision here, because
            # the only thing we needed was the inventory_id, which now
            # is (by design) identical to the revision_id
            # try:
            #     rev = other.get_revision(rev_id)
            # except bzrlib.errors.NoSuchRevision:
            #     failures.add(rev_id)
            #     continue

            if inv is None:
                failures.add(rev_id)
                continue
            else:
                good_revisions.add(rev_id)

            text_ids = []
            for key, entry in inv.iter_entries():
                if entry.text_id is None:
                    continue
                text_ids.append(entry.text_id)

            has_ids = self.text_store.has(text_ids)
            for has, text_id in zip(has_ids, text_ids):
                if not has:
                    needed_texts.add(text_id)

        pb.clear()
                    
        count, cp_fail = self.text_store.copy_multi(other.text_store, 
                                                    needed_texts)
        #print "Added %d texts." % count 
        count, cp_fail = self.inventory_store.copy_multi(other.inventory_store,
                                                         good_revisions)
        #print "Added %d inventories." % count 
        count, cp_fail = self.revision_store.copy_multi(other.revision_store, 
                                                          good_revisions,
                                                          permit_failure=True)
        assert len(cp_fail) == 0 
        return count, failures
       

    def commit(self, *args, **kw):
        from bzrlib.commit import commit
        commit(self, *args, **kw)
        

    def lookup_revision(self, revision):
        """Return the revision identifier for a given revision information."""
        revno, info = self._get_revision_info(revision)
        return info


    def revision_id_to_revno(self, revision_id):
        """Given a revision id, return its revno"""
        history = self.revision_history()
        try:
            return history.index(revision_id) + 1
        except ValueError:
            raise bzrlib.errors.NoSuchRevision(self, revision_id)


    def get_revision_info(self, revision):
        """Return (revno, revision id) for revision identifier.

        revision can be an integer, in which case it is assumed to be revno (though
            this will translate negative values into positive ones)
        revision can also be a string, in which case it is parsed for something like
            'date:' or 'revid:' etc.
        """
        revno, rev_id = self._get_revision_info(revision)
        if revno is None:
            raise bzrlib.errors.NoSuchRevision(self, revision)
        return revno, rev_id

    def get_rev_id(self, revno, history=None):
        """Find the revision id of the specified revno."""
        if revno == 0:
            return None
        if history is None:
            history = self.revision_history()
        elif revno <= 0 or revno > len(history):
            raise bzrlib.errors.NoSuchRevision(self, revno)
        return history[revno - 1]

    def _get_revision_info(self, revision):
        """Return (revno, revision id) for revision specifier.

        revision can be an integer, in which case it is assumed to be revno
        (though this will translate negative values into positive ones)
        revision can also be a string, in which case it is parsed for something
        like 'date:' or 'revid:' etc.

        A revid is always returned.  If it is None, the specifier referred to
        the null revision.  If the revid does not occur in the revision
        history, revno will be None.
        """
        
        if revision is None:
            return 0, None
        revno = None
        try:# Convert to int if possible
            revision = int(revision)
        except ValueError:
            pass
        revs = self.revision_history()
        if isinstance(revision, int):
            if revision < 0:
                revno = len(revs) + revision + 1
            else:
                revno = revision
            rev_id = self.get_rev_id(revno, revs)
        elif isinstance(revision, basestring):
            for prefix, func in Branch.REVISION_NAMESPACES.iteritems():
                if revision.startswith(prefix):
                    result = func(self, revs, revision)
                    if len(result) > 1:
                        revno, rev_id = result
                    else:
                        revno = result[0]
                        rev_id = self.get_rev_id(revno, revs)
                    break
            else:
                raise BzrError('No namespace registered for string: %r' %
                               revision)
        else:
            raise TypeError('Unhandled revision type %s' % revision)

        if revno is None:
            if rev_id is None:
                raise bzrlib.errors.NoSuchRevision(self, revision)
        return revno, rev_id

    def _namespace_revno(self, revs, revision):
        """Lookup a revision by revision number"""
        assert revision.startswith('revno:')
        try:
            return (int(revision[6:]),)
        except ValueError:
            return None
    REVISION_NAMESPACES['revno:'] = _namespace_revno

    def _namespace_revid(self, revs, revision):
        assert revision.startswith('revid:')
        rev_id = revision[len('revid:'):]
        try:
            return revs.index(rev_id) + 1, rev_id
        except ValueError:
            return None, rev_id
    REVISION_NAMESPACES['revid:'] = _namespace_revid

    def _namespace_last(self, revs, revision):
        assert revision.startswith('last:')
        try:
            offset = int(revision[5:])
        except ValueError:
            return (None,)
        else:
            if offset <= 0:
                raise BzrError('You must supply a positive value for --revision last:XXX')
            return (len(revs) - offset + 1,)
    REVISION_NAMESPACES['last:'] = _namespace_last

    def _namespace_tag(self, revs, revision):
        assert revision.startswith('tag:')
        raise BzrError('tag: namespace registered, but not implemented.')
    REVISION_NAMESPACES['tag:'] = _namespace_tag

    def _namespace_date(self, revs, revision):
        assert revision.startswith('date:')
        import datetime
        # Spec for date revisions:
        #   date:value
        #   value can be 'yesterday', 'today', 'tomorrow' or a YYYY-MM-DD string.
        #   it can also start with a '+/-/='. '+' says match the first
        #   entry after the given date. '-' is match the first entry before the date
        #   '=' is match the first entry after, but still on the given date.
        #
        #   +2005-05-12 says find the first matching entry after May 12th, 2005 at 0:00
        #   -2005-05-12 says find the first matching entry before May 12th, 2005 at 0:00
        #   =2005-05-12 says find the first match after May 12th, 2005 at 0:00 but before
        #       May 13th, 2005 at 0:00
        #
        #   So the proper way of saying 'give me all entries for today' is:
        #       -r {date:+today}:{date:-tomorrow}
        #   The default is '=' when not supplied
        val = revision[5:]
        match_style = '='
        if val[:1] in ('+', '-', '='):
            match_style = val[:1]
            val = val[1:]

        today = datetime.datetime.today().replace(hour=0,minute=0,second=0,microsecond=0)
        if val.lower() == 'yesterday':
            dt = today - datetime.timedelta(days=1)
        elif val.lower() == 'today':
            dt = today
        elif val.lower() == 'tomorrow':
            dt = today + datetime.timedelta(days=1)
        else:
            import re
            # This should be done outside the function to avoid recompiling it.
            _date_re = re.compile(
                    r'(?P<date>(?P<year>\d\d\d\d)-(?P<month>\d\d)-(?P<day>\d\d))?'
                    r'(,|T)?\s*'
                    r'(?P<time>(?P<hour>\d\d):(?P<minute>\d\d)(:(?P<second>\d\d))?)?'
                )
            m = _date_re.match(val)
            if not m or (not m.group('date') and not m.group('time')):
                raise BzrError('Invalid revision date %r' % revision)

            if m.group('date'):
                year, month, day = int(m.group('year')), int(m.group('month')), int(m.group('day'))
            else:
                year, month, day = today.year, today.month, today.day
            if m.group('time'):
                hour = int(m.group('hour'))
                minute = int(m.group('minute'))
                if m.group('second'):
                    second = int(m.group('second'))
                else:
                    second = 0
            else:
                hour, minute, second = 0,0,0

            dt = datetime.datetime(year=year, month=month, day=day,
                    hour=hour, minute=minute, second=second)
        first = dt
        last = None
        reversed = False
        if match_style == '-':
            reversed = True
        elif match_style == '=':
            last = dt + datetime.timedelta(days=1)

        if reversed:
            for i in range(len(revs)-1, -1, -1):
                r = self.get_revision(revs[i])
                # TODO: Handle timezone.
                dt = datetime.datetime.fromtimestamp(r.timestamp)
                if first >= dt and (last is None or dt >= last):
                    return (i+1,)
        else:
            for i in range(len(revs)):
                r = self.get_revision(revs[i])
                # TODO: Handle timezone.
                dt = datetime.datetime.fromtimestamp(r.timestamp)
                if first <= dt and (last is None or dt <= last):
                    return (i+1,)
    REVISION_NAMESPACES['date:'] = _namespace_date


    def _namespace_ancestor(self, revs, revision):
        from revision import common_ancestor, MultipleRevisionSources
        other_branch = find_branch(_trim_namespace('ancestor', revision))
        revision_a = self.last_patch()
        revision_b = other_branch.last_patch()
        for r, b in ((revision_a, self), (revision_b, other_branch)):
            if r is None:
                raise bzrlib.errors.NoCommits(b)
        revision_source = MultipleRevisionSources(self, other_branch)
        result = common_ancestor(revision_a, revision_b, revision_source)
        try:
            revno = self.revision_id_to_revno(result)
        except bzrlib.errors.NoSuchRevision:
            revno = None
        return revno,result
        

    REVISION_NAMESPACES['ancestor:'] = _namespace_ancestor

    def revision_tree(self, revision_id):
        """Return Tree for a revision on this branch.

        `revision_id` may be None for the null revision, in which case
        an `EmptyTree` is returned."""
        # TODO: refactor this to use an existing revision object
        # so we don't need to read it in twice.
        if revision_id == None:
            return EmptyTree()
        else:
            inv = self.get_revision_inventory(revision_id)
            return RevisionTree(self.text_store, inv)


    def working_tree(self):
        """Return a `Tree` for the working copy."""
        from bzrlib.workingtree import WorkingTree
        # TODO: In the future, WorkingTree should utilize Transport
        return WorkingTree(self._transport.base, self.read_working_inventory())


    def basis_tree(self):
        """Return `Tree` object for last revision.

        If there are no revisions yet, return an `EmptyTree`.
        """
        r = self.last_patch()
        if r == None:
            return EmptyTree()
        else:
            return RevisionTree(self.text_store, self.get_revision_inventory(r))



    def rename_one(self, from_rel, to_rel):
        """Rename one file.

        This can change the directory or the filename or both.
        """
        self.lock_write()
        try:
            tree = self.working_tree()
            inv = tree.inventory
            if not tree.has_filename(from_rel):
                raise BzrError("can't rename: old working file %r does not exist" % from_rel)
            if tree.has_filename(to_rel):
                raise BzrError("can't rename: new working file %r already exists" % to_rel)

            file_id = inv.path2id(from_rel)
            if file_id == None:
                raise BzrError("can't rename: old name %r is not versioned" % from_rel)

            if inv.path2id(to_rel):
                raise BzrError("can't rename: new name %r is already versioned" % to_rel)

            to_dir, to_tail = os.path.split(to_rel)
            to_dir_id = inv.path2id(to_dir)
            if to_dir_id == None and to_dir != '':
                raise BzrError("can't determine destination directory id for %r" % to_dir)

            mutter("rename_one:")
            mutter("  file_id    {%s}" % file_id)
            mutter("  from_rel   %r" % from_rel)
            mutter("  to_rel     %r" % to_rel)
            mutter("  to_dir     %r" % to_dir)
            mutter("  to_dir_id  {%s}" % to_dir_id)

            inv.rename(file_id, to_dir_id, to_tail)

            from_abs = self.abspath(from_rel)
            to_abs = self.abspath(to_rel)
            try:
                os.rename(from_abs, to_abs)
            except OSError, e:
                raise BzrError("failed to rename %r to %r: %s"
                        % (from_abs, to_abs, e[1]),
                        ["rename rolled back"])

            self._write_inventory(inv)
        finally:
            self.unlock()


    def move(self, from_paths, to_name):
        """Rename files.

        to_name must exist as a versioned directory.

        If to_name exists and is a directory, the files are moved into
        it, keeping their old names.  If it is a directory, 

        Note that to_name is only the last component of the new name;
        this doesn't change the directory.

        This returns a list of (from_path, to_path) pairs for each
        entry that is moved.
        """
        result = []
        self.lock_write()
        try:
            ## TODO: Option to move IDs only
            assert not isinstance(from_paths, basestring)
            tree = self.working_tree()
            inv = tree.inventory
            to_abs = self.abspath(to_name)
            if not isdir(to_abs):
                raise BzrError("destination %r is not a directory" % to_abs)
            if not tree.has_filename(to_name):
                raise BzrError("destination %r not in working directory" % to_abs)
            to_dir_id = inv.path2id(to_name)
            if to_dir_id == None and to_name != '':
                raise BzrError("destination %r is not a versioned directory" % to_name)
            to_dir_ie = inv[to_dir_id]
            if to_dir_ie.kind not in ('directory', 'root_directory'):
                raise BzrError("destination %r is not a directory" % to_abs)

            to_idpath = inv.get_idpath(to_dir_id)

            for f in from_paths:
                if not tree.has_filename(f):
                    raise BzrError("%r does not exist in working tree" % f)
                f_id = inv.path2id(f)
                if f_id == None:
                    raise BzrError("%r is not versioned" % f)
                name_tail = splitpath(f)[-1]
                dest_path = appendpath(to_name, name_tail)
                if tree.has_filename(dest_path):
                    raise BzrError("destination %r already exists" % dest_path)
                if f_id in to_idpath:
                    raise BzrError("can't move %r to a subdirectory of itself" % f)

            # OK, so there's a race here, it's possible that someone will
            # create a file in this interval and then the rename might be
            # left half-done.  But we should have caught most problems.

            for f in from_paths:
                name_tail = splitpath(f)[-1]
                dest_path = appendpath(to_name, name_tail)
                result.append((f, dest_path))
                inv.rename(inv.path2id(f), to_dir_id, name_tail)
                try:
                    os.rename(self.abspath(f), self.abspath(dest_path))
                except OSError, e:
                    raise BzrError("failed to rename %r to %r: %s" % (f, dest_path, e[1]),
                            ["rename rolled back"])

            self._write_inventory(inv)
        finally:
            self.unlock()

        return result


    def revert(self, filenames, old_tree=None, backups=True):
        """Restore selected files to the versions from a previous tree.

        backups
            If true (default) backups are made of files before
            they're renamed.
        """
        from bzrlib.errors import NotVersionedError, BzrError
        from bzrlib.atomicfile import AtomicFile
        from bzrlib.osutils import backup_file
        
        inv = self.read_working_inventory()
        if old_tree is None:
            old_tree = self.basis_tree()
        old_inv = old_tree.inventory

        nids = []
        for fn in filenames:
            file_id = inv.path2id(fn)
            if not file_id:
                raise NotVersionedError("not a versioned file", fn)
            if not old_inv.has_id(file_id):
                raise BzrError("file not present in old tree", fn, file_id)
            nids.append((fn, file_id))
            
        # TODO: Rename back if it was previously at a different location

        # TODO: If given a directory, restore the entire contents from
        # the previous version.

        # TODO: Make a backup to a temporary file.

        # TODO: If the file previously didn't exist, delete it?
        for fn, file_id in nids:
            backup_file(fn)
            
            f = AtomicFile(fn, 'wb')
            try:
                f.write(old_tree.get_file(file_id).read())
                f.commit()
            finally:
                f.close()


    def pending_merges(self):
        """Return a list of pending merges.

        These are revisions that have been merged into the working
        directory but not yet committed.
        """
        cfn = self._rel_controlfilename('pending-merges')
        if not self._transport.has(cfn):
            return []
        p = []
        for l in self.controlfile('pending-merges', 'r').readlines():
            p.append(l.rstrip('\n'))
        return p


    def add_pending_merge(self, *revision_ids):
        from bzrlib.revision import validate_revision_id

        for rev_id in revision_ids:
            validate_revision_id(rev_id)

        p = self.pending_merges()
        updated = False
        for rev_id in revision_ids:
            if rev_id in p:
                continue
            p.append(rev_id)
            updated = True
        if updated:
            self.set_pending_merges(p)

    def set_pending_merges(self, rev_list):
        self.lock_write()
        try:
            self.put_controlfile('pending-merges', '\n'.join(rev_list))
        finally:
            self.unlock()


    def get_parent(self):
        """Return the parent location of the branch.

        This is the default location for push/pull/missing.  The usual
        pattern is that the user can override it by specifying a
        location.
        """
        import errno
        _locs = ['parent', 'pull', 'x-pull']
        for l in _locs:
            try:
                return self.controlfile(l, 'r').read().strip('\n')
            except IOError, e:
                if e.errno != errno.ENOENT:
                    raise
        return None


    def set_parent(self, url):
        # TODO: Maybe delete old location files?
        from bzrlib.atomicfile import AtomicFile
        self.lock_write()
        try:
            f = AtomicFile(self.controlfilename('parent'))
            try:
                f.write(url + '\n')
                f.commit()
            finally:
                f.close()
        finally:
            self.unlock()

    def check_revno(self, revno):
        """\
        Check whether a revno corresponds to any revision.
        Zero (the NULL revision) is considered valid.
        """
        if revno != 0:
            self.check_real_revno(revno)
            
    def check_real_revno(self, revno):
        """\
        Check whether a revno corresponds to a real revision.
        Zero (the NULL revision) is considered invalid
        """
        if revno < 1 or revno > self.revno():
            raise InvalidRevisionNumber(revno)
        
        


class ScratchBranch(Branch):
    """Special test class: a branch that cleans up after itself.

    >>> b = ScratchBranch()
    >>> isdir(b.base)
    True
    >>> bd = b.base
    >>> b.destroy()
    >>> isdir(bd)
    False
    """
    def __init__(self, files=[], dirs=[], base=None):
        """Make a test branch.

        This creates a temporary directory and runs init-tree in it.

        If any files are listed, they are created in the working copy.
        """
        from tempfile import mkdtemp
        init = False
        if base is None:
            base = mkdtemp()
            init = True
        Branch.__init__(self, base, init=init)
        for d in dirs:
            self._transport.mkdir(d)
            
        for f in files:
            self._transport.put(f, 'content of %s' % f)


    def clone(self):
        """
        >>> orig = ScratchBranch(files=["file1", "file2"])
        >>> clone = orig.clone()
        >>> os.path.samefile(orig.base, clone.base)
        False
        >>> os.path.isfile(os.path.join(clone.base, "file1"))
        True
        """
        from shutil import copytree
        from tempfile import mkdtemp
        base = mkdtemp()
        os.rmdir(base)
        copytree(self.base, base, symlinks=True)
        return ScratchBranch(base=base)


        
    def __del__(self):
        self.destroy()

    def destroy(self):
        """Destroy the test branch, removing the scratch directory."""
        from shutil import rmtree
        try:
            if self.base:
                mutter("delete ScratchBranch %s" % self.base)
                rmtree(self.base)
        except OSError, e:
            # Work around for shutil.rmtree failing on Windows when
            # readonly files are encountered
            mutter("hit exception in destroying ScratchBranch: %s" % e)
            for root, dirs, files in os.walk(self.base, topdown=False):
                for name in files:
                    os.chmod(os.path.join(root, name), 0700)
            rmtree(self.base)
        self._transport = None

    

######################################################################
# predicates


def is_control_file(filename):
    ## FIXME: better check
    filename = os.path.normpath(filename)
    while filename != '':
        head, tail = os.path.split(filename)
        ## mutter('check %r for control file' % ((head, tail), ))
        if tail == bzrlib.BZRDIR:
            return True
        if filename == head:
            break
        filename = head
    return False



def gen_file_id(name):
    """Return new file id.

    This should probably generate proper UUIDs, but for the moment we
    cope with just randomness because running uuidgen every time is
    slow."""
    import re
    from binascii import hexlify
    from time import time

    # get last component
    idx = name.rfind('/')
    if idx != -1:
        name = name[idx+1 : ]
    idx = name.rfind('\\')
    if idx != -1:
        name = name[idx+1 : ]

    # make it not a hidden file
    name = name.lstrip('.')

    # remove any wierd characters; we don't escape them but rather
    # just pull them out
    name = re.sub(r'[^\w.]', '', name)

    s = hexlify(rand_bytes(8))
    return '-'.join((name, compact_date(time()), s))


def gen_root_id():
    """Return a new tree-root file id."""
    return gen_file_id('TREE_ROOT')


def copy_branch(branch_from, to_location, revision=None):
    """Copy branch_from into the existing directory to_location.

    revision
        If not None, only revisions up to this point will be copied.
        The head of the new branch will be that revision.

    to_location
        The name of a local directory that exists but is empty.
    """
    from bzrlib.merge import merge

    assert isinstance(branch_from, Branch)
    assert isinstance(to_location, basestring)
    
    br_to = Branch(to_location, init=True)
    br_to.set_root_id(branch_from.get_root_id())
    if revision is None:
        revno = branch_from.revno()
    else:
        revno, rev_id = branch_from.get_revision_info(revision)
    br_to.update_revisions(branch_from, stop_revision=revno)
    merge((to_location, -1), (to_location, 0), this_dir=to_location,
          check_clean=False, ignore_zero=True)
    br_to.set_parent(branch_from.base)
    return br_to

def _trim_namespace(namespace, spec):
    full_namespace = namespace + ':'
    assert spec.startswith(full_namespace)
    return spec[len(full_namespace):]
