# Copyright (C) 2005-2011 Canonical Ltd
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

"""builtin bzr commands"""

from __future__ import absolute_import

import os

import bzrlib.bzrdir

from bzrlib import lazy_import
lazy_import.lazy_import(globals(), """
import cStringIO
import errno
import sys
import time

import bzrlib
from bzrlib import (
    bugtracker,
    bundle,
    btree_index,
    controldir,
    directory_service,
    delta,
    config as _mod_config,
    errors,
    globbing,
    hooks,
    log,
    merge as _mod_merge,
    merge_directive,
    osutils,
    reconfigure,
    rename_map,
    revision as _mod_revision,
    static_tuple,
    timestamp,
    transport,
    ui,
    urlutils,
    views,
    gpg,
    )
from bzrlib.branch import Branch
from bzrlib.conflicts import ConflictList
from bzrlib.transport import memory
from bzrlib.revisionspec import RevisionSpec, RevisionInfo
from bzrlib.smtp_connection import SMTPConnection
from bzrlib.workingtree import WorkingTree
from bzrlib.i18n import gettext, ngettext
""")

from bzrlib.commands import (
    Command,
    builtin_command_registry,
    display_command,
    )
from bzrlib.option import (
    ListOption,
    Option,
    RegistryOption,
    custom_help,
    _parse_revision_str,
    )
from bzrlib.trace import mutter, note, warning, is_quiet, get_verbosity_level
from bzrlib import (
    symbol_versioning,
    )


def _get_branch_location(control_dir):
    """Return location of branch for this control dir."""
    try:
        this_branch = control_dir.open_branch()
        # This may be a heavy checkout, where we want the master branch
        master_location = this_branch.get_bound_location()
        if master_location is not None:
            return master_location
        # If not, use a local sibling
        return this_branch.base
    except errors.NotBranchError:
        format = control_dir.find_branch_format()
        if getattr(format, 'get_reference', None) is not None:
            return format.get_reference(control_dir)
        else:
            return control_dir.root_transport.base


def lookup_new_sibling_branch(control_dir, location):
    """Lookup the location for a new sibling branch.

    :param control_dir: Control directory relative to which to look up
        the name.
    :param location: Name of the new branch
    :return: Full location to the new branch
    """
    location = directory_service.directories.dereference(location)
    if '/' not in location and '\\' not in location:
        # This path is meant to be relative to the existing branch
        this_url = _get_branch_location(control_dir)
        # Perhaps the target control dir supports colocated branches?
        try:
            root = controldir.ControlDir.open(this_url,
                possible_transports=[control_dir.user_transport])
        except errors.NotBranchError:
            colocated = False
        else:
            colocated = root._format.colocated_branches

        if colocated:
            return urlutils.join_segment_parameters(this_url,
                {"branch": urlutils.escape(location)})
        else:
            return urlutils.join(this_url, '..', urlutils.escape(location))
    return location


def lookup_sibling_branch(control_dir, location):
    """Lookup sibling branch.
    
    :param control_dir: Control directory relative to which to lookup the
        location.
    :param location: Location to look up
    :return: branch to open
    """
    try:
        # Perhaps it's a colocated branch?
        return control_dir.open_branch(location)
    except (errors.NotBranchError, errors.NoColocatedBranchSupport):
        try:
            return Branch.open(location)
        except errors.NotBranchError:
            this_url = _get_branch_location(control_dir)
            return Branch.open(
                urlutils.join(
                    this_url, '..', urlutils.escape(location)))


@symbol_versioning.deprecated_function(symbol_versioning.deprecated_in((2, 3, 0)))
def tree_files(file_list, default_branch=u'.', canonicalize=True,
    apply_view=True):
    return internal_tree_files(file_list, default_branch, canonicalize,
        apply_view)


def tree_files_for_add(file_list):
    """
    Return a tree and list of absolute paths from a file list.

    Similar to tree_files, but add handles files a bit differently, so it a
    custom implementation.  In particular, MutableTreeTree.smart_add expects
    absolute paths, which it immediately converts to relative paths.
    """
    # FIXME Would be nice to just return the relative paths like
    # internal_tree_files does, but there are a large number of unit tests
    # that assume the current interface to mutabletree.smart_add
    if file_list:
        tree, relpath = WorkingTree.open_containing(file_list[0])
        if tree.supports_views():
            view_files = tree.views.lookup_view()
            if view_files:
                for filename in file_list:
                    if not osutils.is_inside_any(view_files, filename):
                        raise errors.FileOutsideView(filename, view_files)
        file_list = file_list[:]
        file_list[0] = tree.abspath(relpath)
    else:
        tree = WorkingTree.open_containing(u'.')[0]
        if tree.supports_views():
            view_files = tree.views.lookup_view()
            if view_files:
                file_list = view_files
                view_str = views.view_display_str(view_files)
                note(gettext("Ignoring files outside view. View is %s") % view_str)
    return tree, file_list


def _get_one_revision(command_name, revisions):
    if revisions is None:
        return None
    if len(revisions) != 1:
        raise errors.BzrCommandError(gettext(
            'bzr %s --revision takes exactly one revision identifier') % (
                command_name,))
    return revisions[0]


def _get_one_revision_tree(command_name, revisions, branch=None, tree=None):
    """Get a revision tree. Not suitable for commands that change the tree.
    
    Specifically, the basis tree in dirstate trees is coupled to the dirstate
    and doing a commit/uncommit/pull will at best fail due to changing the
    basis revision data.

    If tree is passed in, it should be already locked, for lifetime management
    of the trees internal cached state.
    """
    if branch is None:
        branch = tree.branch
    if revisions is None:
        if tree is not None:
            rev_tree = tree.basis_tree()
        else:
            rev_tree = branch.basis_tree()
    else:
        revision = _get_one_revision(command_name, revisions)
        rev_tree = revision.as_tree(branch)
    return rev_tree


# XXX: Bad function name; should possibly also be a class method of
# WorkingTree rather than a function.
@symbol_versioning.deprecated_function(symbol_versioning.deprecated_in((2, 3, 0)))
def internal_tree_files(file_list, default_branch=u'.', canonicalize=True,
    apply_view=True):
    """Convert command-line paths to a WorkingTree and relative paths.

    Deprecated: use WorkingTree.open_containing_paths instead.

    This is typically used for command-line processors that take one or
    more filenames, and infer the workingtree that contains them.

    The filenames given are not required to exist.

    :param file_list: Filenames to convert.

    :param default_branch: Fallback tree path to use if file_list is empty or
        None.

    :param apply_view: if True and a view is set, apply it or check that
        specified files are within it

    :return: workingtree, [relative_paths]
    """
    return WorkingTree.open_containing_paths(
        file_list, default_directory='.',
        canonicalize=True,
        apply_view=True)


def _get_view_info_for_change_reporter(tree):
    """Get the view information from a tree for change reporting."""
    view_info = None
    try:
        current_view = tree.views.get_view_info()[0]
        if current_view is not None:
            view_info = (current_view, tree.views.lookup_view())
    except errors.ViewsNotSupported:
        pass
    return view_info


def _open_directory_or_containing_tree_or_branch(filename, directory):
    """Open the tree or branch containing the specified file, unless
    the --directory option is used to specify a different branch."""
    if directory is not None:
        return (None, Branch.open(directory), filename)
    return controldir.ControlDir.open_containing_tree_or_branch(filename)


# TODO: Make sure no commands unconditionally use the working directory as a
# branch.  If a filename argument is used, the first of them should be used to
# specify the branch.  (Perhaps this can be factored out into some kind of
# Argument class, representing a file in a branch, where the first occurrence
# opens the branch?)

class cmd_status(Command):
    __doc__ = """Display status summary.

    This reports on versioned and unknown files, reporting them
    grouped by state.  Possible states are:

    added
        Versioned in the working copy but not in the previous revision.

    removed
        Versioned in the previous revision but removed or deleted
        in the working copy.

    renamed
        Path of this file changed from the previous revision;
        the text may also have changed.  This includes files whose
        parent directory was renamed.

    modified
        Text has changed since the previous revision.

    kind changed
        File kind has been changed (e.g. from file to directory).

    unknown
        Not versioned and not matching an ignore pattern.

    Additionally for directories, symlinks and files with a changed
    executable bit, Bazaar indicates their type using a trailing
    character: '/', '@' or '*' respectively. These decorations can be
    disabled using the '--no-classify' option.

    To see ignored files use 'bzr ignored'.  For details on the
    changes to file texts, use 'bzr diff'.

    Note that --short or -S gives status flags for each item, similar
    to Subversion's status command. To get output similar to svn -q,
    use bzr status -SV.

    If no arguments are specified, the status of the entire working
    directory is shown.  Otherwise, only the status of the specified
    files or directories is reported.  If a directory is given, status
    is reported for everything inside that directory.

    Before merges are committed, the pending merge tip revisions are
    shown. To see all pending merge revisions, use the -v option.
    To skip the display of pending merge information altogether, use
    the no-pending option or specify a file/directory.

    To compare the working directory to a specific revision, pass a
    single revision to the revision argument.

    To see which files have changed in a specific revision, or between
    two revisions, pass a revision range to the revision argument.
    This will produce the same results as calling 'bzr diff --summarize'.
    """

    # TODO: --no-recurse, --recurse options

    takes_args = ['file*']
    takes_options = ['show-ids', 'revision', 'change', 'verbose',
                     Option('short', help='Use short status indicators.',
                            short_name='S'),
                     Option('versioned', help='Only show versioned files.',
                            short_name='V'),
                     Option('no-pending', help='Don\'t show pending merges.',
                           ),
                     Option('no-classify',
                            help='Do not mark object type using indicator.',
                           ),
                     ]
    aliases = ['st', 'stat']

    encoding_type = 'replace'
    _see_also = ['diff', 'revert', 'status-flags']

    @display_command
    def run(self, show_ids=False, file_list=None, revision=None, short=False,
            versioned=False, no_pending=False, verbose=False,
            no_classify=False):
        from bzrlib.status import show_tree_status

        if revision and len(revision) > 2:
            raise errors.BzrCommandError(gettext('bzr status --revision takes exactly'
                                         ' one or two revision specifiers'))

        tree, relfile_list = WorkingTree.open_containing_paths(file_list)
        # Avoid asking for specific files when that is not needed.
        if relfile_list == ['']:
            relfile_list = None
            # Don't disable pending merges for full trees other than '.'.
            if file_list == ['.']:
                no_pending = True
        # A specific path within a tree was given.
        elif relfile_list is not None:
            no_pending = True
        show_tree_status(tree, show_ids=show_ids,
                         specific_files=relfile_list, revision=revision,
                         to_file=self.outf, short=short, versioned=versioned,
                         show_pending=(not no_pending), verbose=verbose,
                         classify=not no_classify)


class cmd_cat_revision(Command):
    __doc__ = """Write out metadata for a revision.

    The revision to print can either be specified by a specific
    revision identifier, or you can use --revision.
    """

    hidden = True
    takes_args = ['revision_id?']
    takes_options = ['directory', 'revision']
    # cat-revision is more for frontends so should be exact
    encoding = 'strict'

    def print_revision(self, revisions, revid):
        stream = revisions.get_record_stream([(revid,)], 'unordered', True)
        record = stream.next()
        if record.storage_kind == 'absent':
            raise errors.NoSuchRevision(revisions, revid)
        revtext = record.get_bytes_as('fulltext')
        self.outf.write(revtext.decode('utf-8'))

    @display_command
    def run(self, revision_id=None, revision=None, directory=u'.'):
        if revision_id is not None and revision is not None:
            raise errors.BzrCommandError(gettext('You can only supply one of'
                                         ' revision_id or --revision'))
        if revision_id is None and revision is None:
            raise errors.BzrCommandError(gettext('You must supply either'
                                         ' --revision or a revision_id'))

        b = controldir.ControlDir.open_containing_tree_or_branch(directory)[1]

        revisions = b.repository.revisions
        if revisions is None:
            raise errors.BzrCommandError(gettext('Repository %r does not support '
                'access to raw revision texts'))

        b.repository.lock_read()
        try:
            # TODO: jam 20060112 should cat-revision always output utf-8?
            if revision_id is not None:
                revision_id = osutils.safe_revision_id(revision_id, warn=False)
                try:
                    self.print_revision(revisions, revision_id)
                except errors.NoSuchRevision:
                    msg = gettext("The repository {0} contains no revision {1}.").format(
                        b.repository.base, revision_id)
                    raise errors.BzrCommandError(msg)
            elif revision is not None:
                for rev in revision:
                    if rev is None:
                        raise errors.BzrCommandError(
                            gettext('You cannot specify a NULL revision.'))
                    rev_id = rev.as_revision_id(b)
                    self.print_revision(revisions, rev_id)
        finally:
            b.repository.unlock()
        

class cmd_dump_btree(Command):
    __doc__ = """Dump the contents of a btree index file to stdout.

    PATH is a btree index file, it can be any URL. This includes things like
    .bzr/repository/pack-names, or .bzr/repository/indices/a34b3a...ca4a4.iix

    By default, the tuples stored in the index file will be displayed. With
    --raw, we will uncompress the pages, but otherwise display the raw bytes
    stored in the index.
    """

    # TODO: Do we want to dump the internal nodes as well?
    # TODO: It would be nice to be able to dump the un-parsed information,
    #       rather than only going through iter_all_entries. However, this is
    #       good enough for a start
    hidden = True
    encoding_type = 'exact'
    takes_args = ['path']
    takes_options = [Option('raw', help='Write the uncompressed bytes out,'
                                        ' rather than the parsed tuples.'),
                    ]

    def run(self, path, raw=False):
        dirname, basename = osutils.split(path)
        t = transport.get_transport(dirname)
        if raw:
            self._dump_raw_bytes(t, basename)
        else:
            self._dump_entries(t, basename)

    def _get_index_and_bytes(self, trans, basename):
        """Create a BTreeGraphIndex and raw bytes."""
        bt = btree_index.BTreeGraphIndex(trans, basename, None)
        bytes = trans.get_bytes(basename)
        bt._file = cStringIO.StringIO(bytes)
        bt._size = len(bytes)
        return bt, bytes

    def _dump_raw_bytes(self, trans, basename):
        import zlib

        # We need to parse at least the root node.
        # This is because the first page of every row starts with an
        # uncompressed header.
        bt, bytes = self._get_index_and_bytes(trans, basename)
        for page_idx, page_start in enumerate(xrange(0, len(bytes),
                                                     btree_index._PAGE_SIZE)):
            page_end = min(page_start + btree_index._PAGE_SIZE, len(bytes))
            page_bytes = bytes[page_start:page_end]
            if page_idx == 0:
                self.outf.write('Root node:\n')
                header_end, data = bt._parse_header_from_bytes(page_bytes)
                self.outf.write(page_bytes[:header_end])
                page_bytes = data
            self.outf.write('\nPage %d\n' % (page_idx,))
            if len(page_bytes) == 0:
                self.outf.write('(empty)\n');
            else:
                decomp_bytes = zlib.decompress(page_bytes)
                self.outf.write(decomp_bytes)
                self.outf.write('\n')

    def _dump_entries(self, trans, basename):
        try:
            st = trans.stat(basename)
        except errors.TransportNotPossible:
            # We can't stat, so we'll fake it because we have to do the 'get()'
            # anyway.
            bt, _ = self._get_index_and_bytes(trans, basename)
        else:
            bt = btree_index.BTreeGraphIndex(trans, basename, st.st_size)
        for node in bt.iter_all_entries():
            # Node is made up of:
            # (index, key, value, [references])
            try:
                refs = node[3]
            except IndexError:
                refs_as_tuples = None
            else:
                refs_as_tuples = static_tuple.as_tuples(refs)
            as_tuple = (tuple(node[1]), node[2], refs_as_tuples)
            self.outf.write('%s\n' % (as_tuple,))


class cmd_remove_tree(Command):
    __doc__ = """Remove the working tree from a given branch/checkout.

    Since a lightweight checkout is little more than a working tree
    this will refuse to run against one.

    To re-create the working tree, use "bzr checkout".
    """
    _see_also = ['checkout', 'working-trees']
    takes_args = ['location*']
    takes_options = [
        Option('force',
               help='Remove the working tree even if it has '
                    'uncommitted or shelved changes.'),
        ]

    def run(self, location_list, force=False):
        if not location_list:
            location_list=['.']

        for location in location_list:
            d = controldir.ControlDir.open(location)

            try:
                working = d.open_workingtree()
            except errors.NoWorkingTree:
                raise errors.BzrCommandError(gettext("No working tree to remove"))
            except errors.NotLocalUrl:
                raise errors.BzrCommandError(gettext("You cannot remove the working tree"
                                             " of a remote path"))
            if not force:
                if (working.has_changes()):
                    raise errors.UncommittedChanges(working)
                if working.get_shelf_manager().last_shelf() is not None:
                    raise errors.ShelvedChanges(working)

            if working.user_url != working.branch.user_url:
                raise errors.BzrCommandError(gettext("You cannot remove the working tree"
                                             " from a lightweight checkout"))

            d.destroy_workingtree()


class cmd_repair_workingtree(Command):
    __doc__ = """Reset the working tree state file.

    This is not meant to be used normally, but more as a way to recover from
    filesystem corruption, etc. This rebuilds the working inventory back to a
    'known good' state. Any new modifications (adding a file, renaming, etc)
    will be lost, though modified files will still be detected as such.

    Most users will want something more like "bzr revert" or "bzr update"
    unless the state file has become corrupted.

    By default this attempts to recover the current state by looking at the
    headers of the state file. If the state file is too corrupted to even do
    that, you can supply --revision to force the state of the tree.
    """

    takes_options = ['revision', 'directory',
        Option('force',
               help='Reset the tree even if it doesn\'t appear to be'
                    ' corrupted.'),
    ]
    hidden = True

    def run(self, revision=None, directory='.', force=False):
        tree, _ = WorkingTree.open_containing(directory)
        self.add_cleanup(tree.lock_tree_write().unlock)
        if not force:
            try:
                tree.check_state()
            except errors.BzrError:
                pass # There seems to be a real error here, so we'll reset
            else:
                # Refuse
                raise errors.BzrCommandError(gettext(
                    'The tree does not appear to be corrupt. You probably'
                    ' want "bzr revert" instead. Use "--force" if you are'
                    ' sure you want to reset the working tree.'))
        if revision is None:
            revision_ids = None
        else:
            revision_ids = [r.as_revision_id(tree.branch) for r in revision]
        try:
            tree.reset_state(revision_ids)
        except errors.BzrError, e:
            if revision_ids is None:
                extra = (gettext(', the header appears corrupt, try passing -r -1'
                         ' to set the state to the last commit'))
            else:
                extra = ''
            raise errors.BzrCommandError(gettext('failed to reset the tree state{0}').format(extra))


class cmd_revno(Command):
    __doc__ = """Show current revision number.

    This is equal to the number of revisions on this branch.
    """

    _see_also = ['info']
    takes_args = ['location?']
    takes_options = [
        Option('tree', help='Show revno of working tree.'),
        'revision',
        ]

    @display_command
    def run(self, tree=False, location=u'.', revision=None):
        if revision is not None and tree:
            raise errors.BzrCommandError(gettext("--tree and --revision can "
                "not be used together"))

        if tree:
            try:
                wt = WorkingTree.open_containing(location)[0]
                self.add_cleanup(wt.lock_read().unlock)
            except (errors.NoWorkingTree, errors.NotLocalUrl):
                raise errors.NoWorkingTree(location)
            b = wt.branch
            revid = wt.last_revision()
        else:
            b = Branch.open_containing(location)[0]
            self.add_cleanup(b.lock_read().unlock)
            if revision:
                if len(revision) != 1:
                    raise errors.BzrCommandError(gettext(
                        "Tags can only be placed on a single revision, "
                        "not on a range"))
                revid = revision[0].as_revision_id(b)
            else:
                revid = b.last_revision()
        try:
            revno_t = b.revision_id_to_dotted_revno(revid)
        except errors.NoSuchRevision:
            revno_t = ('???',)
        revno = ".".join(str(n) for n in revno_t)
        self.cleanup_now()
        self.outf.write(revno + '\n')


class cmd_revision_info(Command):
    __doc__ = """Show revision number and revision id for a given revision identifier.
    """
    hidden = True
    takes_args = ['revision_info*']
    takes_options = [
        'revision',
        custom_help('directory',
            help='Branch to examine, '
                 'rather than the one containing the working directory.'),
        Option('tree', help='Show revno of working tree.'),
        ]

    @display_command
    def run(self, revision=None, directory=u'.', tree=False,
            revision_info_list=[]):

        try:
            wt = WorkingTree.open_containing(directory)[0]
            b = wt.branch
            self.add_cleanup(wt.lock_read().unlock)
        except (errors.NoWorkingTree, errors.NotLocalUrl):
            wt = None
            b = Branch.open_containing(directory)[0]
            self.add_cleanup(b.lock_read().unlock)
        revision_ids = []
        if revision is not None:
            revision_ids.extend(rev.as_revision_id(b) for rev in revision)
        if revision_info_list is not None:
            for rev_str in revision_info_list:
                rev_spec = RevisionSpec.from_string(rev_str)
                revision_ids.append(rev_spec.as_revision_id(b))
        # No arguments supplied, default to the last revision
        if len(revision_ids) == 0:
            if tree:
                if wt is None:
                    raise errors.NoWorkingTree(directory)
                revision_ids.append(wt.last_revision())
            else:
                revision_ids.append(b.last_revision())

        revinfos = []
        maxlen = 0
        for revision_id in revision_ids:
            try:
                dotted_revno = b.revision_id_to_dotted_revno(revision_id)
                revno = '.'.join(str(i) for i in dotted_revno)
            except errors.NoSuchRevision:
                revno = '???'
            maxlen = max(maxlen, len(revno))
            revinfos.append([revno, revision_id])

        self.cleanup_now()
        for ri in revinfos:
            self.outf.write('%*s %s\n' % (maxlen, ri[0], ri[1]))


class cmd_add(Command):
    __doc__ = """Add specified files or directories.

    In non-recursive mode, all the named items are added, regardless
    of whether they were previously ignored.  A warning is given if
    any of the named files are already versioned.

    In recursive mode (the default), files are treated the same way
    but the behaviour for directories is different.  Directories that
    are already versioned do not give a warning.  All directories,
    whether already versioned or not, are searched for files or
    subdirectories that are neither versioned or ignored, and these
    are added.  This search proceeds recursively into versioned
    directories.  If no names are given '.' is assumed.

    A warning will be printed when nested trees are encountered,
    unless they are explicitly ignored.

    Therefore simply saying 'bzr add' will version all files that
    are currently unknown.

    Adding a file whose parent directory is not versioned will
    implicitly add the parent, and so on up to the root. This means
    you should never need to explicitly add a directory, they'll just
    get added when you add a file in the directory.

    --dry-run will show which files would be added, but not actually
    add them.

    --file-ids-from will try to use the file ids from the supplied path.
    It looks up ids trying to find a matching parent directory with the
    same filename, and then by pure path. This option is rarely needed
    but can be useful when adding the same logical file into two
    branches that will be merged later (without showing the two different
    adds as a conflict). It is also useful when merging another project
    into a subdirectory of this one.
    
    Any files matching patterns in the ignore list will not be added
    unless they are explicitly mentioned.
    
    In recursive mode, files larger than the configuration option 
    add.maximum_file_size will be skipped. Named items are never skipped due
    to file size.
    """
    takes_args = ['file*']
    takes_options = [
        Option('no-recurse',
               help="Don't recursively add the contents of directories."),
        Option('dry-run',
               help="Show what would be done, but don't actually do anything."),
        'verbose',
        Option('file-ids-from',
               type=unicode,
               help='Lookup file ids from this tree.'),
        ]
    encoding_type = 'replace'
    _see_also = ['remove', 'ignore']

    def run(self, file_list, no_recurse=False, dry_run=False, verbose=False,
            file_ids_from=None):
        import bzrlib.add

        base_tree = None
        if file_ids_from is not None:
            try:
                base_tree, base_path = WorkingTree.open_containing(
                                            file_ids_from)
            except errors.NoWorkingTree:
                base_branch, base_path = Branch.open_containing(
                                            file_ids_from)
                base_tree = base_branch.basis_tree()

            action = bzrlib.add.AddFromBaseAction(base_tree, base_path,
                          to_file=self.outf, should_print=(not is_quiet()))
        else:
            action = bzrlib.add.AddWithSkipLargeAction(to_file=self.outf,
                should_print=(not is_quiet()))

        if base_tree:
            self.add_cleanup(base_tree.lock_read().unlock)
        tree, file_list = tree_files_for_add(file_list)
        added, ignored = tree.smart_add(file_list, not
            no_recurse, action=action, save=not dry_run)
        self.cleanup_now()
        if len(ignored) > 0:
            if verbose:
                for glob in sorted(ignored.keys()):
                    for path in ignored[glob]:
                        self.outf.write(
                         gettext("ignored {0} matching \"{1}\"\n").format(
                         path, glob))


class cmd_mkdir(Command):
    __doc__ = """Create a new versioned directory.

    This is equivalent to creating the directory and then adding it.
    """

    takes_args = ['dir+']
    takes_options = [
        Option(
            'parents',
            help='No error if existing, make parent directories as needed.',
            short_name='p'
            )
        ]
    encoding_type = 'replace'

    @classmethod
    def add_file_with_parents(cls, wt, relpath):
        if wt.path2id(relpath) is not None:
            return
        cls.add_file_with_parents(wt, osutils.dirname(relpath))
        wt.add([relpath])

    @classmethod
    def add_file_single(cls, wt, relpath):
        wt.add([relpath])

    def run(self, dir_list, parents=False):
        if parents:
            add_file = self.add_file_with_parents
        else:
            add_file = self.add_file_single
        for dir in dir_list:
            wt, relpath = WorkingTree.open_containing(dir)
            if parents:
                try:
                    os.makedirs(dir)
                except OSError, e:
                    if e.errno != errno.EEXIST:
                        raise
            else:
                os.mkdir(dir)
            add_file(wt, relpath)
            if not is_quiet():
                self.outf.write(gettext('added %s\n') % dir)


class cmd_relpath(Command):
    __doc__ = """Show path of a file relative to root"""

    takes_args = ['filename']
    hidden = True

    @display_command
    def run(self, filename):
        # TODO: jam 20050106 Can relpath return a munged path if
        #       sys.stdout encoding cannot represent it?
        tree, relpath = WorkingTree.open_containing(filename)
        self.outf.write(relpath)
        self.outf.write('\n')


class cmd_inventory(Command):
    __doc__ = """Show inventory of the current working copy or a revision.

    It is possible to limit the output to a particular entry
    type using the --kind option.  For example: --kind file.

    It is also possible to restrict the list of files to a specific
    set. For example: bzr inventory --show-ids this/file
    """

    hidden = True
    _see_also = ['ls']
    takes_options = [
        'revision',
        'show-ids',
        Option('kind',
               help='List entries of a particular kind: file, directory, symlink.',
               type=unicode),
        ]
    takes_args = ['file*']

    @display_command
    def run(self, revision=None, show_ids=False, kind=None, file_list=None):
        if kind and kind not in ['file', 'directory', 'symlink']:
            raise errors.BzrCommandError(gettext('invalid kind %r specified') % (kind,))

        revision = _get_one_revision('inventory', revision)
        work_tree, file_list = WorkingTree.open_containing_paths(file_list)
        self.add_cleanup(work_tree.lock_read().unlock)
        if revision is not None:
            tree = revision.as_tree(work_tree.branch)

            extra_trees = [work_tree]
            self.add_cleanup(tree.lock_read().unlock)
        else:
            tree = work_tree
            extra_trees = []

        if file_list is not None:
            file_ids = tree.paths2ids(file_list, trees=extra_trees,
                                      require_versioned=True)
            # find_ids_across_trees may include some paths that don't
            # exist in 'tree'.
            entries = sorted(
                (tree.id2path(file_id), tree.inventory[file_id])
                for file_id in file_ids if tree.has_id(file_id))
        else:
            entries = tree.inventory.entries()

        self.cleanup_now()
        for path, entry in entries:
            if kind and kind != entry.kind:
                continue
            if show_ids:
                self.outf.write('%-50s %s\n' % (path, entry.file_id))
            else:
                self.outf.write(path)
                self.outf.write('\n')


class cmd_mv(Command):
    __doc__ = """Move or rename a file.

    :Usage:
        bzr mv OLDNAME NEWNAME

        bzr mv SOURCE... DESTINATION

    If the last argument is a versioned directory, all the other names
    are moved into it.  Otherwise, there must be exactly two arguments
    and the file is changed to a new name.

    If OLDNAME does not exist on the filesystem but is versioned and
    NEWNAME does exist on the filesystem but is not versioned, mv
    assumes that the file has been manually moved and only updates
    its internal inventory to reflect that change.
    The same is valid when moving many SOURCE files to a DESTINATION.

    Files cannot be moved between branches.
    """

    takes_args = ['names*']
    takes_options = [Option("after", help="Move only the bzr identifier"
        " of the file, because the file has already been moved."),
        Option('auto', help='Automatically guess renames.'),
        Option('dry-run', help='Avoid making changes when guessing renames.'),
        ]
    aliases = ['move', 'rename']
    encoding_type = 'replace'

    def run(self, names_list, after=False, auto=False, dry_run=False):
        if auto:
            return self.run_auto(names_list, after, dry_run)
        elif dry_run:
            raise errors.BzrCommandError(gettext('--dry-run requires --auto.'))
        if names_list is None:
            names_list = []
        if len(names_list) < 2:
            raise errors.BzrCommandError(gettext("missing file argument"))
        tree, rel_names = WorkingTree.open_containing_paths(names_list, canonicalize=False)
        for file_name in rel_names[0:-1]:
            if file_name == '':
                raise errors.BzrCommandError(gettext("can not move root of branch"))
        self.add_cleanup(tree.lock_tree_write().unlock)
        self._run(tree, names_list, rel_names, after)

    def run_auto(self, names_list, after, dry_run):
        if names_list is not None and len(names_list) > 1:
            raise errors.BzrCommandError(gettext('Only one path may be specified to'
                                         ' --auto.'))
        if after:
            raise errors.BzrCommandError(gettext('--after cannot be specified with'
                                         ' --auto.'))
        work_tree, file_list = WorkingTree.open_containing_paths(
            names_list, default_directory='.')
        self.add_cleanup(work_tree.lock_tree_write().unlock)
        rename_map.RenameMap.guess_renames(work_tree, dry_run)

    def _run(self, tree, names_list, rel_names, after):
        into_existing = osutils.isdir(names_list[-1])
        if into_existing and len(names_list) == 2:
            # special cases:
            # a. case-insensitive filesystem and change case of dir
            # b. move directory after the fact (if the source used to be
            #    a directory, but now doesn't exist in the working tree
            #    and the target is an existing directory, just rename it)
            if (not tree.case_sensitive
                and rel_names[0].lower() == rel_names[1].lower()):
                into_existing = False
            else:
                inv = tree.inventory
                # 'fix' the case of a potential 'from'
                from_id = tree.path2id(
                            tree.get_canonical_inventory_path(rel_names[0]))
                if (not osutils.lexists(names_list[0]) and
                    from_id and inv.get_file_kind(from_id) == "directory"):
                    into_existing = False
        # move/rename
        if into_existing:
            # move into existing directory
            # All entries reference existing inventory items, so fix them up
            # for cicp file-systems.
            rel_names = tree.get_canonical_inventory_paths(rel_names)
            for src, dest in tree.move(rel_names[:-1], rel_names[-1], after=after):
                if not is_quiet():
                    self.outf.write("%s => %s\n" % (src, dest))
        else:
            if len(names_list) != 2:
                raise errors.BzrCommandError(gettext('to mv multiple files the'
                                             ' destination must be a versioned'
                                             ' directory'))

            # for cicp file-systems: the src references an existing inventory
            # item:
            src = tree.get_canonical_inventory_path(rel_names[0])
            # Find the canonical version of the destination:  In all cases, the
            # parent of the target must be in the inventory, so we fetch the
            # canonical version from there (we do not always *use* the
            # canonicalized tail portion - we may be attempting to rename the
            # case of the tail)
            canon_dest = tree.get_canonical_inventory_path(rel_names[1])
            dest_parent = osutils.dirname(canon_dest)
            spec_tail = osutils.basename(rel_names[1])
            # For a CICP file-system, we need to avoid creating 2 inventory
            # entries that differ only by case.  So regardless of the case
            # we *want* to use (ie, specified by the user or the file-system),
            # we must always choose to use the case of any existing inventory
            # items.  The only exception to this is when we are attempting a
            # case-only rename (ie, canonical versions of src and dest are
            # the same)
            dest_id = tree.path2id(canon_dest)
            if dest_id is None or tree.path2id(src) == dest_id:
                # No existing item we care about, so work out what case we
                # are actually going to use.
                if after:
                    # If 'after' is specified, the tail must refer to a file on disk.
                    if dest_parent:
                        dest_parent_fq = osutils.pathjoin(tree.basedir, dest_parent)
                    else:
                        # pathjoin with an empty tail adds a slash, which breaks
                        # relpath :(
                        dest_parent_fq = tree.basedir

                    dest_tail = osutils.canonical_relpath(
                                    dest_parent_fq,
                                    osutils.pathjoin(dest_parent_fq, spec_tail))
                else:
                    # not 'after', so case as specified is used
                    dest_tail = spec_tail
            else:
                # Use the existing item so 'mv' fails with AlreadyVersioned.
                dest_tail = os.path.basename(canon_dest)
            dest = osutils.pathjoin(dest_parent, dest_tail)
            mutter("attempting to move %s => %s", src, dest)
            tree.rename_one(src, dest, after=after)
            if not is_quiet():
                self.outf.write("%s => %s\n" % (src, dest))


class cmd_pull(Command):
    __doc__ = """Turn this branch into a mirror of another branch.

    By default, this command only works on branches that have not diverged.
    Branches are considered diverged if the destination branch's most recent 
    commit is one that has not been merged (directly or indirectly) into the 
    parent.

    If branches have diverged, you can use 'bzr merge' to integrate the changes
    from one into the other.  Once one branch has merged, the other should
    be able to pull it again.

    If you want to replace your local changes and just want your branch to
    match the remote one, use pull --overwrite. This will work even if the two
    branches have diverged.

    If there is no default location set, the first pull will set it (use
    --no-remember to avoid setting it). After that, you can omit the
    location to use the default.  To change the default, use --remember. The
    value will only be saved if the remote location can be accessed.

    The --verbose option will display the revisions pulled using the log_format
    configuration option. You can use a different format by overriding it with
    -Olog_format=<other_format>.

    Note: The location can be specified either in the form of a branch,
    or in the form of a path to a file containing a merge directive generated
    with bzr send.
    """

    _see_also = ['push', 'update', 'status-flags', 'send']
    takes_options = ['remember', 'overwrite', 'revision',
        custom_help('verbose',
            help='Show logs of pulled revisions.'),
        custom_help('directory',
            help='Branch to pull into, '
                 'rather than the one containing the working directory.'),
        Option('local',
            help="Perform a local pull in a bound "
                 "branch.  Local pulls are not applied to "
                 "the master branch."
            ),
        Option('show-base',
            help="Show base revision text in conflicts.")
        ]
    takes_args = ['location?']
    encoding_type = 'replace'

    def run(self, location=None, remember=None, overwrite=False,
            revision=None, verbose=False,
            directory=None, local=False,
            show_base=False):
        # FIXME: too much stuff is in the command class
        revision_id = None
        mergeable = None
        if directory is None:
            directory = u'.'
        try:
            tree_to = WorkingTree.open_containing(directory)[0]
            branch_to = tree_to.branch
            self.add_cleanup(tree_to.lock_write().unlock)
        except errors.NoWorkingTree:
            tree_to = None
            branch_to = Branch.open_containing(directory)[0]
            self.add_cleanup(branch_to.lock_write().unlock)

        if tree_to is None and show_base:
            raise errors.BzrCommandError(gettext("Need working tree for --show-base."))

        if local and not branch_to.get_bound_location():
            raise errors.LocalRequiresBoundBranch()

        possible_transports = []
        if location is not None:
            try:
                mergeable = bundle.read_mergeable_from_url(location,
                    possible_transports=possible_transports)
            except errors.NotABundle:
                mergeable = None

        stored_loc = branch_to.get_parent()
        if location is None:
            if stored_loc is None:
                raise errors.BzrCommandError(gettext("No pull location known or"
                                             " specified."))
            else:
                display_url = urlutils.unescape_for_display(stored_loc,
                        self.outf.encoding)
                if not is_quiet():
                    self.outf.write(gettext("Using saved parent location: %s\n") % display_url)
                location = stored_loc

        revision = _get_one_revision('pull', revision)
        if mergeable is not None:
            if revision is not None:
                raise errors.BzrCommandError(gettext(
                    'Cannot use -r with merge directives or bundles'))
            mergeable.install_revisions(branch_to.repository)
            base_revision_id, revision_id, verified = \
                mergeable.get_merge_request(branch_to.repository)
            branch_from = branch_to
        else:
            branch_from = Branch.open(location,
                possible_transports=possible_transports)
            self.add_cleanup(branch_from.lock_read().unlock)
            # Remembers if asked explicitly or no previous location is set
            if (remember
                or (remember is None and branch_to.get_parent() is None)):
                branch_to.set_parent(branch_from.base)

        if revision is not None:
            revision_id = revision.as_revision_id(branch_from)

        if tree_to is not None:
            view_info = _get_view_info_for_change_reporter(tree_to)
            change_reporter = delta._ChangeReporter(
                unversioned_filter=tree_to.is_ignored,
                view_info=view_info)
            result = tree_to.pull(
                branch_from, overwrite, revision_id, change_reporter,
                local=local, show_base=show_base)
        else:
            result = branch_to.pull(
                branch_from, overwrite, revision_id, local=local)

        result.report(self.outf)
        if verbose and result.old_revid != result.new_revid:
            log.show_branch_change(
                branch_to, self.outf, result.old_revno,
                result.old_revid)
        if getattr(result, 'tag_conflicts', None):
            return 1
        else:
            return 0


class cmd_push(Command):
    __doc__ = """Update a mirror of this branch.

    The target branch will not have its working tree populated because this
    is both expensive, and is not supported on remote file systems.

    Some smart servers or protocols *may* put the working tree in place in
    the future.

    This command only works on branches that have not diverged.  Branches are
    considered diverged if the destination branch's most recent commit is one
    that has not been merged (directly or indirectly) by the source branch.

    If branches have diverged, you can use 'bzr push --overwrite' to replace
    the other branch completely, discarding its unmerged changes.

    If you want to ensure you have the different changes in the other branch,
    do a merge (see bzr help merge) from the other branch, and commit that.
    After that you will be able to do a push without '--overwrite'.

    If there is no default push location set, the first push will set it (use
    --no-remember to avoid setting it).  After that, you can omit the
    location to use the default.  To change the default, use --remember. The
    value will only be saved if the remote location can be accessed.

    The --verbose option will display the revisions pushed using the log_format
    configuration option. You can use a different format by overriding it with
    -Olog_format=<other_format>.
    """

    _see_also = ['pull', 'update', 'working-trees']
    takes_options = ['remember', 'overwrite', 'verbose', 'revision',
        Option('create-prefix',
               help='Create the path leading up to the branch '
                    'if it does not already exist.'),
        custom_help('directory',
            help='Branch to push from, '
                 'rather than the one containing the working directory.'),
        Option('use-existing-dir',
               help='By default push will fail if the target'
                    ' directory exists, but does not already'
                    ' have a control directory.  This flag will'
                    ' allow push to proceed.'),
        Option('stacked',
            help='Create a stacked branch that references the public location '
                'of the parent branch.'),
        Option('stacked-on',
            help='Create a stacked branch that refers to another branch '
                'for the commit history. Only the work not present in the '
                'referenced branch is included in the branch created.',
            type=unicode),
        Option('strict',
               help='Refuse to push if there are uncommitted changes in'
               ' the working tree, --no-strict disables the check.'),
        Option('no-tree',
               help="Don't populate the working tree, even for protocols"
               " that support it."),
        ]
    takes_args = ['location?']
    encoding_type = 'replace'

    def run(self, location=None, remember=None, overwrite=False,
        create_prefix=False, verbose=False, revision=None,
        use_existing_dir=False, directory=None, stacked_on=None,
        stacked=False, strict=None, no_tree=False):
        from bzrlib.push import _show_push_branch

        if directory is None:
            directory = '.'
        # Get the source branch
        (tree, br_from,
         _unused) = controldir.ControlDir.open_containing_tree_or_branch(directory)
        # Get the tip's revision_id
        revision = _get_one_revision('push', revision)
        if revision is not None:
            revision_id = revision.in_history(br_from).rev_id
        else:
            revision_id = None
        if tree is not None and revision_id is None:
            tree.check_changed_or_out_of_date(
                strict, 'push_strict',
                more_error='Use --no-strict to force the push.',
                more_warning='Uncommitted changes will not be pushed.')
        # Get the stacked_on branch, if any
        if stacked_on is not None:
            stacked_on = urlutils.normalize_url(stacked_on)
        elif stacked:
            parent_url = br_from.get_parent()
            if parent_url:
                parent = Branch.open(parent_url)
                stacked_on = parent.get_public_branch()
                if not stacked_on:
                    # I considered excluding non-http url's here, thus forcing
                    # 'public' branches only, but that only works for some
                    # users, so it's best to just depend on the user spotting an
                    # error by the feedback given to them. RBC 20080227.
                    stacked_on = parent_url
            if not stacked_on:
                raise errors.BzrCommandError(gettext(
                    "Could not determine branch to refer to."))

        # Get the destination location
        if location is None:
            stored_loc = br_from.get_push_location()
            if stored_loc is None:
                parent_loc = br_from.get_parent()
                if parent_loc:
                    raise errors.BzrCommandError(gettext(
                        "No push location known or specified. To push to the "
                        "parent branch (at %s), use 'bzr push :parent'." %
                        urlutils.unescape_for_display(parent_loc,
                            self.outf.encoding)))
                else:
                    raise errors.BzrCommandError(gettext(
                        "No push location known or specified."))
            else:
                display_url = urlutils.unescape_for_display(stored_loc,
                        self.outf.encoding)
                note(gettext("Using saved push location: %s") % display_url)
                location = stored_loc

        _show_push_branch(br_from, revision_id, location, self.outf,
            verbose=verbose, overwrite=overwrite, remember=remember,
            stacked_on=stacked_on, create_prefix=create_prefix,
            use_existing_dir=use_existing_dir, no_tree=no_tree)


class cmd_branch(Command):
    __doc__ = """Create a new branch that is a copy of an existing branch.

    If the TO_LOCATION is omitted, the last component of the FROM_LOCATION will
    be used.  In other words, "branch ../foo/bar" will attempt to create ./bar.
    If the FROM_LOCATION has no / or path separator embedded, the TO_LOCATION
    is derived from the FROM_LOCATION by stripping a leading scheme or drive
    identifier, if any. For example, "branch lp:foo-bar" will attempt to
    create ./foo-bar.

    To retrieve the branch as of a particular revision, supply the --revision
    parameter, as in "branch foo/bar -r 5".

    The synonyms 'clone' and 'get' for this command are deprecated.
    """

    _see_also = ['checkout']
    takes_args = ['from_location', 'to_location?']
    takes_options = ['revision',
        Option('hardlink', help='Hard-link working tree files where possible.'),
        Option('files-from', type=str,
               help="Get file contents from this tree."),
        Option('no-tree',
            help="Create a branch without a working-tree."),
        Option('switch',
            help="Switch the checkout in the current directory "
                 "to the new branch."),
        Option('stacked',
            help='Create a stacked branch referring to the source branch. '
                'The new branch will depend on the availability of the source '
                'branch for all operations.'),
        Option('standalone',
               help='Do not use a shared repository, even if available.'),
        Option('use-existing-dir',
               help='By default branch will fail if the target'
                    ' directory exists, but does not already'
                    ' have a control directory.  This flag will'
                    ' allow branch to proceed.'),
        Option('bind',
            help="Bind new branch to from location."),
        ]
    aliases = ['get', 'clone']

    def run(self, from_location, to_location=None, revision=None,
            hardlink=False, stacked=False, standalone=False, no_tree=False,
            use_existing_dir=False, switch=False, bind=False,
            files_from=None):
        from bzrlib import switch as _mod_switch
        from bzrlib.tag import _merge_tags_if_possible
        if self.invoked_as in ['get', 'clone']:
            ui.ui_factory.show_user_warning(
                'deprecated_command',
                deprecated_name=self.invoked_as,
                recommended_name='branch',
                deprecated_in_version='2.4')
        accelerator_tree, br_from = controldir.ControlDir.open_tree_or_branch(
            from_location)
        if not (hardlink or files_from):
            # accelerator_tree is usually slower because you have to read N
            # files (no readahead, lots of seeks, etc), but allow the user to
            # explicitly request it
            accelerator_tree = None
        if files_from is not None and files_from != from_location:
            accelerator_tree = WorkingTree.open(files_from)
        revision = _get_one_revision('branch', revision)
        self.add_cleanup(br_from.lock_read().unlock)
        if revision is not None:
            revision_id = revision.as_revision_id(br_from)
        else:
            # FIXME - wt.last_revision, fallback to branch, fall back to
            # None or perhaps NULL_REVISION to mean copy nothing
            # RBC 20060209
            revision_id = br_from.last_revision()
        if to_location is None:
            to_location = getattr(br_from, "name", None)
            if to_location is None:
                to_location = urlutils.derive_to_location(from_location)
        to_transport = transport.get_transport(to_location)
        try:
            to_transport.mkdir('.')
        except errors.FileExists:
            try:
                to_dir = controldir.ControlDir.open_from_transport(
                    to_transport)
            except errors.NotBranchError:
                if not use_existing_dir:
                    raise errors.BzrCommandError(gettext('Target directory "%s" '
                        'already exists.') % to_location)
                else:
                    to_dir = None
            else:
                try:
                    to_dir.open_branch()
                except errors.NotBranchError:
                    pass
                else:
                    raise errors.AlreadyBranchError(to_location)
        except errors.NoSuchFile:
            raise errors.BzrCommandError(gettext('Parent of "%s" does not exist.')
                                         % to_location)
        else:
            to_dir = None
        if to_dir is None:
            try:
                # preserve whatever source format we have.
                to_dir = br_from.bzrdir.sprout(to_transport.base, revision_id,
                                            possible_transports=[to_transport],
                                            accelerator_tree=accelerator_tree,
                                            hardlink=hardlink, stacked=stacked,
                                            force_new_repo=standalone,
                                            create_tree_if_local=not no_tree,
                                            source_branch=br_from)
                branch = to_dir.open_branch(
                    possible_transports=[
                        br_from.bzrdir.root_transport, to_transport])
            except errors.NoSuchRevision:
                to_transport.delete_tree('.')
                msg = gettext("The branch {0} has no revision {1}.").format(
                    from_location, revision)
                raise errors.BzrCommandError(msg)
        else:
            branch = br_from.sprout(to_dir, revision_id=revision_id)
        _merge_tags_if_possible(br_from, branch)
        # If the source branch is stacked, the new branch may
        # be stacked whether we asked for that explicitly or not.
        # We therefore need a try/except here and not just 'if stacked:'
        try:
            note(gettext('Created new stacked branch referring to %s.') %
                branch.get_stacked_on_url())
        except (errors.NotStacked, errors.UnstackableBranchFormat,
            errors.UnstackableRepositoryFormat), e:
            note(ngettext('Branched %d revision.', 'Branched %d revisions.', branch.revno()) % branch.revno())
        if bind:
            # Bind to the parent
            parent_branch = Branch.open(from_location)
            branch.bind(parent_branch)
            note(gettext('New branch bound to %s') % from_location)
        if switch:
            # Switch to the new branch
            wt, _ = WorkingTree.open_containing('.')
            _mod_switch.switch(wt.bzrdir, branch)
            note(gettext('Switched to branch: %s'),
                urlutils.unescape_for_display(branch.base, 'utf-8'))


class cmd_branches(Command):
    __doc__ = """List the branches available at the current location.

    This command will print the names of all the branches at the current
    location.
    """

    takes_args = ['location?']
    takes_options = [
                  Option('recursive', short_name='R',
                         help='Recursively scan for branches rather than '
                              'just looking in the specified location.')]

    def run(self, location=".", recursive=False):
        if recursive:
            t = transport.get_transport(location)
            if not t.listable():
                raise errors.BzrCommandError(
                    "Can't scan this type of location.")
            for b in controldir.ControlDir.find_branches(t):
                self.outf.write("%s\n" % urlutils.unescape_for_display(
                    urlutils.relative_url(t.base, b.base),
                    self.outf.encoding).rstrip("/"))
        else:
            dir = controldir.ControlDir.open_containing(location)[0]
            try:
                active_branch = dir.open_branch(name=None)
            except errors.NotBranchError:
                active_branch = None
            branches = dir.get_branches()
            names = {}
            for name, branch in branches.iteritems():
                if name is None:
                    continue
                active = (active_branch is not None and
                          active_branch.base == branch.base)
                names[name] = active
            # Only mention the current branch explicitly if it's not
            # one of the colocated branches
            if not any(names.values()) and active_branch is not None:
                self.outf.write("* %s\n" % gettext("(default)"))
            for name in sorted(names.keys()):
                active = names[name]
                if active:
                    prefix = "*"
                else:
                    prefix = " "
                self.outf.write("%s %s\n" % (
                    prefix, name.encode(self.outf.encoding)))


class cmd_checkout(Command):
    __doc__ = """Create a new checkout of an existing branch.

    If BRANCH_LOCATION is omitted, checkout will reconstitute a working tree for
    the branch found in '.'. This is useful if you have removed the working tree
    or if it was never created - i.e. if you pushed the branch to its current
    location using SFTP.

    If the TO_LOCATION is omitted, the last component of the BRANCH_LOCATION will
    be used.  In other words, "checkout ../foo/bar" will attempt to create ./bar.
    If the BRANCH_LOCATION has no / or path separator embedded, the TO_LOCATION
    is derived from the BRANCH_LOCATION by stripping a leading scheme or drive
    identifier, if any. For example, "checkout lp:foo-bar" will attempt to
    create ./foo-bar.

    To retrieve the branch as of a particular revision, supply the --revision
    parameter, as in "checkout foo/bar -r 5". Note that this will be immediately
    out of date [so you cannot commit] but it may be useful (i.e. to examine old
    code.)
    """

    _see_also = ['checkouts', 'branch']
    takes_args = ['branch_location?', 'to_location?']
    takes_options = ['revision',
                     Option('lightweight',
                            help="Perform a lightweight checkout.  Lightweight "
                                 "checkouts depend on access to the branch for "
                                 "every operation.  Normal checkouts can perform "
                                 "common operations like diff and status without "
                                 "such access, and also support local commits."
                            ),
                     Option('files-from', type=str,
                            help="Get file contents from this tree."),
                     Option('hardlink',
                            help='Hard-link working tree files where possible.'
                            ),
                     ]
    aliases = ['co']

    def run(self, branch_location=None, to_location=None, revision=None,
            lightweight=False, files_from=None, hardlink=False):
        if branch_location is None:
            branch_location = osutils.getcwd()
            to_location = branch_location
        accelerator_tree, source = controldir.ControlDir.open_tree_or_branch(
            branch_location)
        if not (hardlink or files_from):
            # accelerator_tree is usually slower because you have to read N
            # files (no readahead, lots of seeks, etc), but allow the user to
            # explicitly request it
            accelerator_tree = None
        revision = _get_one_revision('checkout', revision)
        if files_from is not None and files_from != branch_location:
            accelerator_tree = WorkingTree.open(files_from)
        if revision is not None:
            revision_id = revision.as_revision_id(source)
        else:
            revision_id = None
        if to_location is None:
            to_location = urlutils.derive_to_location(branch_location)
        # if the source and to_location are the same,
        # and there is no working tree,
        # then reconstitute a branch
        if (osutils.abspath(to_location) ==
            osutils.abspath(branch_location)):
            try:
                source.bzrdir.open_workingtree()
            except errors.NoWorkingTree:
                source.bzrdir.create_workingtree(revision_id)
                return
        source.create_checkout(to_location, revision_id, lightweight,
                               accelerator_tree, hardlink)


class cmd_renames(Command):
    __doc__ = """Show list of renamed files.
    """
    # TODO: Option to show renames between two historical versions.

    # TODO: Only show renames under dir, rather than in the whole branch.
    _see_also = ['status']
    takes_args = ['dir?']

    @display_command
    def run(self, dir=u'.'):
        tree = WorkingTree.open_containing(dir)[0]
        self.add_cleanup(tree.lock_read().unlock)
        new_inv = tree.inventory
        old_tree = tree.basis_tree()
        self.add_cleanup(old_tree.lock_read().unlock)
        old_inv = old_tree.inventory
        renames = []
        iterator = tree.iter_changes(old_tree, include_unchanged=True)
        for f, paths, c, v, p, n, k, e in iterator:
            if paths[0] == paths[1]:
                continue
            if None in (paths):
                continue
            renames.append(paths)
        renames.sort()
        for old_name, new_name in renames:
            self.outf.write("%s => %s\n" % (old_name, new_name))


class cmd_update(Command):
    __doc__ = """Update a working tree to a new revision.

    This will perform a merge of the destination revision (the tip of the
    branch, or the specified revision) into the working tree, and then make
    that revision the basis revision for the working tree.  

    You can use this to visit an older revision, or to update a working tree
    that is out of date from its branch.
    
    If there are any uncommitted changes in the tree, they will be carried
    across and remain as uncommitted changes after the update.  To discard
    these changes, use 'bzr revert'.  The uncommitted changes may conflict
    with the changes brought in by the change in basis revision.

    If the tree's branch is bound to a master branch, bzr will also update
    the branch from the master.

    You cannot update just a single file or directory, because each Bazaar
    working tree has just a single basis revision.  If you want to restore a
    file that has been removed locally, use 'bzr revert' instead of 'bzr
    update'.  If you want to restore a file to its state in a previous
    revision, use 'bzr revert' with a '-r' option, or use 'bzr cat' to write
    out the old content of that file to a new location.

    The 'dir' argument, if given, must be the location of the root of a
    working tree to update.  By default, the working tree that contains the 
    current working directory is used.
    """

    _see_also = ['pull', 'working-trees', 'status-flags']
    takes_args = ['dir?']
    takes_options = ['revision',
                     Option('show-base',
                            help="Show base revision text in conflicts."),
                     ]
    aliases = ['up']

    def run(self, dir=None, revision=None, show_base=None):
        if revision is not None and len(revision) != 1:
            raise errors.BzrCommandError(gettext(
                "bzr update --revision takes exactly one revision"))
        if dir is None:
            tree = WorkingTree.open_containing('.')[0]
        else:
            tree, relpath = WorkingTree.open_containing(dir)
            if relpath:
                # See bug 557886.
                raise errors.BzrCommandError(gettext(
                    "bzr update can only update a whole tree, "
                    "not a file or subdirectory"))
        branch = tree.branch
        possible_transports = []
        master = branch.get_master_branch(
            possible_transports=possible_transports)
        if master is not None:
            branch_location = master.base
            tree.lock_write()
        else:
            branch_location = tree.branch.base
            tree.lock_tree_write()
        self.add_cleanup(tree.unlock)
        # get rid of the final '/' and be ready for display
        branch_location = urlutils.unescape_for_display(
            branch_location.rstrip('/'),
            self.outf.encoding)
        existing_pending_merges = tree.get_parent_ids()[1:]
        if master is None:
            old_tip = None
        else:
            # may need to fetch data into a heavyweight checkout
            # XXX: this may take some time, maybe we should display a
            # message
            old_tip = branch.update(possible_transports)
        if revision is not None:
            revision_id = revision[0].as_revision_id(branch)
        else:
            revision_id = branch.last_revision()
        if revision_id == _mod_revision.ensure_null(tree.last_revision()):
            revno = branch.revision_id_to_dotted_revno(revision_id)
            note(gettext("Tree is up to date at revision {0} of branch {1}"
                        ).format('.'.join(map(str, revno)), branch_location))
            return 0
        view_info = _get_view_info_for_change_reporter(tree)
        change_reporter = delta._ChangeReporter(
            unversioned_filter=tree.is_ignored,
            view_info=view_info)
        try:
            conflicts = tree.update(
                change_reporter,
                possible_transports=possible_transports,
                revision=revision_id,
                old_tip=old_tip,
                show_base=show_base)
        except errors.NoSuchRevision, e:
            raise errors.BzrCommandError(gettext(
                                  "branch has no revision %s\n"
                                  "bzr update --revision only works"
                                  " for a revision in the branch history")
                                  % (e.revision))
        revno = tree.branch.revision_id_to_dotted_revno(
            _mod_revision.ensure_null(tree.last_revision()))
        note(gettext('Updated to revision {0} of branch {1}').format(
             '.'.join(map(str, revno)), branch_location))
        parent_ids = tree.get_parent_ids()
        if parent_ids[1:] and parent_ids[1:] != existing_pending_merges:
            note(gettext('Your local commits will now show as pending merges with '
                 "'bzr status', and can be committed with 'bzr commit'."))
        if conflicts != 0:
            return 1
        else:
            return 0


class cmd_info(Command):
    __doc__ = """Show information about a working tree, branch or repository.

    This command will show all known locations and formats associated to the
    tree, branch or repository.

    In verbose mode, statistical information is included with each report.
    To see extended statistic information, use a verbosity level of 2 or
    higher by specifying the verbose option multiple times, e.g. -vv.

    Branches and working trees will also report any missing revisions.

    :Examples:

      Display information on the format and related locations:

        bzr info

      Display the above together with extended format information and
      basic statistics (like the number of files in the working tree and
      number of revisions in the branch and repository):

        bzr info -v

      Display the above together with number of committers to the branch:

        bzr info -vv
    """
    _see_also = ['revno', 'working-trees', 'repositories']
    takes_args = ['location?']
    takes_options = ['verbose']
    encoding_type = 'replace'

    @display_command
    def run(self, location=None, verbose=False):
        if verbose:
            noise_level = get_verbosity_level()
        else:
            noise_level = 0
        from bzrlib.info import show_bzrdir_info
        show_bzrdir_info(controldir.ControlDir.open_containing(location)[0],
                         verbose=noise_level, outfile=self.outf)


class cmd_remove(Command):
    __doc__ = """Remove files or directories.

    This makes Bazaar stop tracking changes to the specified files. Bazaar will
    delete them if they can easily be recovered using revert otherwise they
    will be backed up (adding an extention of the form .~#~). If no options or
    parameters are given Bazaar will scan for files that are being tracked by
    Bazaar but missing in your tree and stop tracking them for you.
    """
    takes_args = ['file*']
    takes_options = ['verbose',
        Option('new', help='Only remove files that have never been committed.'),
        RegistryOption.from_kwargs('file-deletion-strategy',
            'The file deletion mode to be used.',
            title='Deletion Strategy', value_switches=True, enum_switch=False,
            safe='Backup changed files (default).',
            keep='Delete from bzr but leave the working copy.',
            no_backup='Don\'t backup changed files.',
            force='Delete all the specified files, even if they can not be '
                'recovered and even if they are non-empty directories. '
                '(deprecated, use no-backup)')]
    aliases = ['rm', 'del']
    encoding_type = 'replace'

    def run(self, file_list, verbose=False, new=False,
        file_deletion_strategy='safe'):
        if file_deletion_strategy == 'force':
            note(gettext("(The --force option is deprecated, rather use --no-backup "
                "in future.)"))
            file_deletion_strategy = 'no-backup'

        tree, file_list = WorkingTree.open_containing_paths(file_list)

        if file_list is not None:
            file_list = [f for f in file_list]

        self.add_cleanup(tree.lock_write().unlock)
        # Heuristics should probably all move into tree.remove_smart or
        # some such?
        if new:
            added = tree.changes_from(tree.basis_tree(),
                specific_files=file_list).added
            file_list = sorted([f[0] for f in added], reverse=True)
            if len(file_list) == 0:
                raise errors.BzrCommandError(gettext('No matching files.'))
        elif file_list is None:
            # missing files show up in iter_changes(basis) as
            # versioned-with-no-kind.
            missing = []
            for change in tree.iter_changes(tree.basis_tree()):
                # Find paths in the working tree that have no kind:
                if change[1][1] is not None and change[6][1] is None:
                    missing.append(change[1][1])
            file_list = sorted(missing, reverse=True)
            file_deletion_strategy = 'keep'
        tree.remove(file_list, verbose=verbose, to_file=self.outf,
            keep_files=file_deletion_strategy=='keep',
            force=(file_deletion_strategy=='no-backup'))


class cmd_file_id(Command):
    __doc__ = """Print file_id of a particular file or directory.

    The file_id is assigned when the file is first added and remains the
    same through all revisions where the file exists, even when it is
    moved or renamed.
    """

    hidden = True
    _see_also = ['inventory', 'ls']
    takes_args = ['filename']

    @display_command
    def run(self, filename):
        tree, relpath = WorkingTree.open_containing(filename)
        i = tree.path2id(relpath)
        if i is None:
            raise errors.NotVersionedError(filename)
        else:
            self.outf.write(i + '\n')


class cmd_file_path(Command):
    __doc__ = """Print path of file_ids to a file or directory.

    This prints one line for each directory down to the target,
    starting at the branch root.
    """

    hidden = True
    takes_args = ['filename']

    @display_command
    def run(self, filename):
        tree, relpath = WorkingTree.open_containing(filename)
        fid = tree.path2id(relpath)
        if fid is None:
            raise errors.NotVersionedError(filename)
        segments = osutils.splitpath(relpath)
        for pos in range(1, len(segments) + 1):
            path = osutils.joinpath(segments[:pos])
            self.outf.write("%s\n" % tree.path2id(path))


class cmd_reconcile(Command):
    __doc__ = """Reconcile bzr metadata in a branch.

    This can correct data mismatches that may have been caused by
    previous ghost operations or bzr upgrades. You should only
    need to run this command if 'bzr check' or a bzr developer
    advises you to run it.

    If a second branch is provided, cross-branch reconciliation is
    also attempted, which will check that data like the tree root
    id which was not present in very early bzr versions is represented
    correctly in both branches.

    At the same time it is run it may recompress data resulting in
    a potential saving in disk space or performance gain.

    The branch *MUST* be on a listable system such as local disk or sftp.
    """

    _see_also = ['check']
    takes_args = ['branch?']
    takes_options = [
        Option('canonicalize-chks',
               help='Make sure CHKs are in canonical form (repairs '
                    'bug 522637).',
               hidden=True),
        ]

    def run(self, branch=".", canonicalize_chks=False):
        from bzrlib.reconcile import reconcile
        dir = controldir.ControlDir.open(branch)
        reconcile(dir, canonicalize_chks=canonicalize_chks)


class cmd_revision_history(Command):
    __doc__ = """Display the list of revision ids on a branch."""

    _see_also = ['log']
    takes_args = ['location?']

    hidden = True

    @display_command
    def run(self, location="."):
        branch = Branch.open_containing(location)[0]
        self.add_cleanup(branch.lock_read().unlock)
        graph = branch.repository.get_graph()
        history = list(graph.iter_lefthand_ancestry(branch.last_revision(),
            [_mod_revision.NULL_REVISION]))
        for revid in reversed(history):
            self.outf.write(revid)
            self.outf.write('\n')


class cmd_ancestry(Command):
    __doc__ = """List all revisions merged into this branch."""

    _see_also = ['log', 'revision-history']
    takes_args = ['location?']

    hidden = True

    @display_command
    def run(self, location="."):
        try:
            wt = WorkingTree.open_containing(location)[0]
        except errors.NoWorkingTree:
            b = Branch.open(location)
            last_revision = b.last_revision()
        else:
            b = wt.branch
            last_revision = wt.last_revision()

        self.add_cleanup(b.repository.lock_read().unlock)
        graph = b.repository.get_graph()
        revisions = [revid for revid, parents in
            graph.iter_ancestry([last_revision])]
        for revision_id in reversed(revisions):
            if _mod_revision.is_null(revision_id):
                continue
            self.outf.write(revision_id + '\n')


class cmd_init(Command):
    __doc__ = """Make a directory into a versioned branch.

    Use this to create an empty branch, or before importing an
    existing project.

    If there is a repository in a parent directory of the location, then
    the history of the branch will be stored in the repository.  Otherwise
    init creates a standalone branch which carries its own history
    in the .bzr directory.

    If there is already a branch at the location but it has no working tree,
    the tree can be populated with 'bzr checkout'.

    Recipe for importing a tree of files::

        cd ~/project
        bzr init
        bzr add .
        bzr status
        bzr commit -m "imported project"
    """

    _see_also = ['init-repository', 'branch', 'checkout']
    takes_args = ['location?']
    takes_options = [
        Option('create-prefix',
               help='Create the path leading up to the branch '
                    'if it does not already exist.'),
         RegistryOption('format',
                help='Specify a format for this branch. '
                'See "help formats".',
                lazy_registry=('bzrlib.bzrdir', 'format_registry'),
                converter=lambda name: controldir.format_registry.make_bzrdir(name),
                value_switches=True,
                title="Branch format",
                ),
         Option('append-revisions-only',
                help='Never change revnos or the existing log.'
                '  Append revisions to it only.'),
         Option('no-tree',
                'Create a branch without a working tree.')
         ]
    def run(self, location=None, format=None, append_revisions_only=False,
            create_prefix=False, no_tree=False):
        if format is None:
            format = controldir.format_registry.make_bzrdir('default')
        if location is None:
            location = u'.'

        to_transport = transport.get_transport(location)

        # The path has to exist to initialize a
        # branch inside of it.
        # Just using os.mkdir, since I don't
        # believe that we want to create a bunch of
        # locations if the user supplies an extended path
        try:
            to_transport.ensure_base()
        except errors.NoSuchFile:
            if not create_prefix:
                raise errors.BzrCommandError(gettext("Parent directory of %s"
                    " does not exist."
                    "\nYou may supply --create-prefix to create all"
                    " leading parent directories.")
                    % location)
            to_transport.create_prefix()

        try:
            a_bzrdir = controldir.ControlDir.open_from_transport(to_transport)
        except errors.NotBranchError:
            # really a NotBzrDir error...
            create_branch = controldir.ControlDir.create_branch_convenience
            if no_tree:
                force_new_tree = False
            else:
                force_new_tree = None
            branch = create_branch(to_transport.base, format=format,
                                   possible_transports=[to_transport],
                                   force_new_tree=force_new_tree)
            a_bzrdir = branch.bzrdir
        else:
            from bzrlib.transport.local import LocalTransport
            if a_bzrdir.has_branch():
                if (isinstance(to_transport, LocalTransport)
                    and not a_bzrdir.has_workingtree()):
                        raise errors.BranchExistsWithoutWorkingTree(location)
                raise errors.AlreadyBranchError(location)
            branch = a_bzrdir.create_branch()
            if not no_tree and not a_bzrdir.has_workingtree():
                a_bzrdir.create_workingtree()
        if append_revisions_only:
            try:
                branch.set_append_revisions_only(True)
            except errors.UpgradeRequired:
                raise errors.BzrCommandError(gettext('This branch format cannot be set'
                    ' to append-revisions-only.  Try --default.'))
        if not is_quiet():
            from bzrlib.info import describe_layout, describe_format
            try:
                tree = a_bzrdir.open_workingtree(recommend_upgrade=False)
            except (errors.NoWorkingTree, errors.NotLocalUrl):
                tree = None
            repository = branch.repository
            layout = describe_layout(repository, branch, tree).lower()
            format = describe_format(a_bzrdir, repository, branch, tree)
            self.outf.write(gettext("Created a {0} (format: {1})\n").format(
                  layout, format))
            if repository.is_shared():
                #XXX: maybe this can be refactored into transport.path_or_url()
                url = repository.bzrdir.root_transport.external_url()
                try:
                    url = urlutils.local_path_from_url(url)
                except errors.InvalidURL:
                    pass
                self.outf.write(gettext("Using shared repository: %s\n") % url)


class cmd_init_repository(Command):
    __doc__ = """Create a shared repository for branches to share storage space.

    New branches created under the repository directory will store their
    revisions in the repository, not in the branch directory.  For branches
    with shared history, this reduces the amount of storage needed and 
    speeds up the creation of new branches.

    If the --no-trees option is given then the branches in the repository
    will not have working trees by default.  They will still exist as 
    directories on disk, but they will not have separate copies of the 
    files at a certain revision.  This can be useful for repositories that
    store branches which are interacted with through checkouts or remote
    branches, such as on a server.

    :Examples:
        Create a shared repository holding just branches::

            bzr init-repo --no-trees repo
            bzr init repo/trunk

        Make a lightweight checkout elsewhere::

            bzr checkout --lightweight repo/trunk trunk-checkout
            cd trunk-checkout
            (add files here)
    """

    _see_also = ['init', 'branch', 'checkout', 'repositories']
    takes_args = ["location"]
    takes_options = [RegistryOption('format',
                            help='Specify a format for this repository. See'
                                 ' "bzr help formats" for details.',
                            lazy_registry=('bzrlib.controldir', 'format_registry'),
                            converter=lambda name: controldir.format_registry.make_bzrdir(name),
                            value_switches=True, title='Repository format'),
                     Option('no-trees',
                             help='Branches in the repository will default to'
                                  ' not having a working tree.'),
                    ]
    aliases = ["init-repo"]

    def run(self, location, format=None, no_trees=False):
        if format is None:
            format = controldir.format_registry.make_bzrdir('default')

        if location is None:
            location = '.'

        to_transport = transport.get_transport(location)

        (repo, newdir, require_stacking, repository_policy) = (
            format.initialize_on_transport_ex(to_transport,
            create_prefix=True, make_working_trees=not no_trees,
            shared_repo=True, force_new_repo=True,
            use_existing_dir=True,
            repo_format_name=format.repository_format.get_format_string()))
        if not is_quiet():
            from bzrlib.info import show_bzrdir_info
            show_bzrdir_info(newdir, verbose=0, outfile=self.outf)


class cmd_diff(Command):
    __doc__ = """Show differences in the working tree, between revisions or branches.

    If no arguments are given, all changes for the current tree are listed.
    If files are given, only the changes in those files are listed.
    Remote and multiple branches can be compared by using the --old and
    --new options. If not provided, the default for both is derived from
    the first argument, if any, or the current tree if no arguments are
    given.

    "bzr diff -p1" is equivalent to "bzr diff --prefix old/:new/", and
    produces patches suitable for "patch -p1".

    Note that when using the -r argument with a range of revisions, the
    differences are computed between the two specified revisions.  That
    is, the command does not show the changes introduced by the first 
    revision in the range.  This differs from the interpretation of 
    revision ranges used by "bzr log" which includes the first revision
    in the range.

    :Exit values:
        1 - changed
        2 - unrepresentable changes
        3 - error
        0 - no change

    :Examples:
        Shows the difference in the working tree versus the last commit::

            bzr diff

        Difference between the working tree and revision 1::

            bzr diff -r1

        Difference between revision 3 and revision 1::

            bzr diff -r1..3

        Difference between revision 3 and revision 1 for branch xxx::

            bzr diff -r1..3 xxx

        The changes introduced by revision 2 (equivalent to -r1..2)::

            bzr diff -c2

        To see the changes introduced by revision X::
        
            bzr diff -cX

        Note that in the case of a merge, the -c option shows the changes
        compared to the left hand parent. To see the changes against
        another parent, use::

            bzr diff -r<chosen_parent>..X

        The changes between the current revision and the previous revision
        (equivalent to -c-1 and -r-2..-1)

            bzr diff -r-2..

        Show just the differences for file NEWS::

            bzr diff NEWS

        Show the differences in working tree xxx for file NEWS::

            bzr diff xxx/NEWS

        Show the differences from branch xxx to this working tree:

            bzr diff --old xxx

        Show the differences between two branches for file NEWS::

            bzr diff --old xxx --new yyy NEWS

        Same as 'bzr diff' but prefix paths with old/ and new/::

            bzr diff --prefix old/:new/
            
        Show the differences using a custom diff program with options::
        
            bzr diff --using /usr/bin/diff --diff-options -wu
    """
    _see_also = ['status']
    takes_args = ['file*']
    takes_options = [
        Option('diff-options', type=str,
               help='Pass these options to the external diff program.'),
        Option('prefix', type=str,
               short_name='p',
               help='Set prefixes added to old and new filenames, as '
                    'two values separated by a colon. (eg "old/:new/").'),
        Option('old',
            help='Branch/tree to compare from.',
            type=unicode,
            ),
        Option('new',
            help='Branch/tree to compare to.',
            type=unicode,
            ),
        'revision',
        'change',
        Option('using',
            help='Use this command to compare files.',
            type=unicode,
            ),
        RegistryOption('format',
            short_name='F',
            help='Diff format to use.',
            lazy_registry=('bzrlib.diff', 'format_registry'),
            title='Diff format'),
        ]
    aliases = ['di', 'dif']
    encoding_type = 'exact'

    @display_command
    def run(self, revision=None, file_list=None, diff_options=None,
            prefix=None, old=None, new=None, using=None, format=None):
        from bzrlib.diff import (get_trees_and_branches_to_diff_locked,
            show_diff_trees)

        if (prefix is None) or (prefix == '0'):
            # diff -p0 format
            old_label = ''
            new_label = ''
        elif prefix == '1':
            old_label = 'old/'
            new_label = 'new/'
        elif ':' in prefix:
            old_label, new_label = prefix.split(":")
        else:
            raise errors.BzrCommandError(gettext(
                '--prefix expects two values separated by a colon'
                ' (eg "old/:new/")'))

        if revision and len(revision) > 2:
            raise errors.BzrCommandError(gettext('bzr diff --revision takes exactly'
                                         ' one or two revision specifiers'))

        if using is not None and format is not None:
            raise errors.BzrCommandError(gettext(
                '{0} and {1} are mutually exclusive').format(
                '--using', '--format'))

        (old_tree, new_tree,
         old_branch, new_branch,
         specific_files, extra_trees) = get_trees_and_branches_to_diff_locked(
            file_list, revision, old, new, self.add_cleanup, apply_view=True)
        # GNU diff on Windows uses ANSI encoding for filenames
        path_encoding = osutils.get_diff_header_encoding()
        return show_diff_trees(old_tree, new_tree, sys.stdout,
                               specific_files=specific_files,
                               external_diff_options=diff_options,
                               old_label=old_label, new_label=new_label,
                               extra_trees=extra_trees,
                               path_encoding=path_encoding,
                               using=using,
                               format_cls=format)


class cmd_deleted(Command):
    __doc__ = """List files deleted in the working tree.
    """
    # TODO: Show files deleted since a previous revision, or
    # between two revisions.
    # TODO: Much more efficient way to do this: read in new
    # directories with readdir, rather than stating each one.  Same
    # level of effort but possibly much less IO.  (Or possibly not,
    # if the directories are very large...)
    _see_also = ['status', 'ls']
    takes_options = ['directory', 'show-ids']

    @display_command
    def run(self, show_ids=False, directory=u'.'):
        tree = WorkingTree.open_containing(directory)[0]
        self.add_cleanup(tree.lock_read().unlock)
        old = tree.basis_tree()
        self.add_cleanup(old.lock_read().unlock)
        for path, ie in old.inventory.iter_entries():
            if not tree.has_id(ie.file_id):
                self.outf.write(path)
                if show_ids:
                    self.outf.write(' ')
                    self.outf.write(ie.file_id)
                self.outf.write('\n')


class cmd_modified(Command):
    __doc__ = """List files modified in working tree.
    """

    hidden = True
    _see_also = ['status', 'ls']
    takes_options = ['directory', 'null']

    @display_command
    def run(self, null=False, directory=u'.'):
        tree = WorkingTree.open_containing(directory)[0]
        self.add_cleanup(tree.lock_read().unlock)
        td = tree.changes_from(tree.basis_tree())
        self.cleanup_now()
        for path, id, kind, text_modified, meta_modified in td.modified:
            if null:
                self.outf.write(path + '\0')
            else:
                self.outf.write(osutils.quotefn(path) + '\n')


class cmd_added(Command):
    __doc__ = """List files added in working tree.
    """

    hidden = True
    _see_also = ['status', 'ls']
    takes_options = ['directory', 'null']

    @display_command
    def run(self, null=False, directory=u'.'):
        wt = WorkingTree.open_containing(directory)[0]
        self.add_cleanup(wt.lock_read().unlock)
        basis = wt.basis_tree()
        self.add_cleanup(basis.lock_read().unlock)
        basis_inv = basis.inventory
        inv = wt.inventory
        for file_id in inv:
            if basis_inv.has_id(file_id):
                continue
            if inv.is_root(file_id) and len(basis_inv) == 0:
                continue
            path = inv.id2path(file_id)
            if not os.access(osutils.pathjoin(wt.basedir, path), os.F_OK):
                continue
            if null:
                self.outf.write(path + '\0')
            else:
                self.outf.write(osutils.quotefn(path) + '\n')


class cmd_root(Command):
    __doc__ = """Show the tree root directory.

    The root is the nearest enclosing directory with a .bzr control
    directory."""

    takes_args = ['filename?']
    @display_command
    def run(self, filename=None):
        """Print the branch root."""
        tree = WorkingTree.open_containing(filename)[0]
        self.outf.write(tree.basedir + '\n')


def _parse_limit(limitstring):
    try:
        return int(limitstring)
    except ValueError:
        msg = gettext("The limit argument must be an integer.")
        raise errors.BzrCommandError(msg)


def _parse_levels(s):
    try:
        return int(s)
    except ValueError:
        msg = gettext("The levels argument must be an integer.")
        raise errors.BzrCommandError(msg)


class cmd_log(Command):
    __doc__ = """Show historical log for a branch or subset of a branch.

    log is bzr's default tool for exploring the history of a branch.
    The branch to use is taken from the first parameter. If no parameters
    are given, the branch containing the working directory is logged.
    Here are some simple examples::

      bzr log                       log the current branch
      bzr log foo.py                log a file in its branch
      bzr log http://server/branch  log a branch on a server

    The filtering, ordering and information shown for each revision can
    be controlled as explained below. By default, all revisions are
    shown sorted (topologically) so that newer revisions appear before
    older ones and descendants always appear before ancestors. If displayed,
    merged revisions are shown indented under the revision in which they
    were merged.

    :Output control:

      The log format controls how information about each revision is
      displayed. The standard log formats are called ``long``, ``short``
      and ``line``. The default is long. See ``bzr help log-formats``
      for more details on log formats.

      The following options can be used to control what information is
      displayed::

        -l N        display a maximum of N revisions
        -n N        display N levels of revisions (0 for all, 1 for collapsed)
        -v          display a status summary (delta) for each revision
        -p          display a diff (patch) for each revision
        --show-ids  display revision-ids (and file-ids), not just revnos

      Note that the default number of levels to display is a function of the
      log format. If the -n option is not used, the standard log formats show
      just the top level (mainline).

      Status summaries are shown using status flags like A, M, etc. To see
      the changes explained using words like ``added`` and ``modified``
      instead, use the -vv option.

    :Ordering control:

      To display revisions from oldest to newest, use the --forward option.
      In most cases, using this option will have little impact on the total
      time taken to produce a log, though --forward does not incrementally
      display revisions like --reverse does when it can.

    :Revision filtering:

      The -r option can be used to specify what revision or range of revisions
      to filter against. The various forms are shown below::

        -rX      display revision X
        -rX..    display revision X and later
        -r..Y    display up to and including revision Y
        -rX..Y   display from X to Y inclusive

      See ``bzr help revisionspec`` for details on how to specify X and Y.
      Some common examples are given below::

        -r-1                show just the tip
        -r-10..             show the last 10 mainline revisions
        -rsubmit:..         show what's new on this branch
        -rancestor:path..   show changes since the common ancestor of this
                            branch and the one at location path
        -rdate:yesterday..  show changes since yesterday

      When logging a range of revisions using -rX..Y, log starts at
      revision Y and searches back in history through the primary
      ("left-hand") parents until it finds X. When logging just the
      top level (using -n1), an error is reported if X is not found
      along the way. If multi-level logging is used (-n0), X may be
      a nested merge revision and the log will be truncated accordingly.

    :Path filtering:

      If parameters are given and the first one is not a branch, the log
      will be filtered to show only those revisions that changed the
      nominated files or directories.

      Filenames are interpreted within their historical context. To log a
      deleted file, specify a revision range so that the file existed at
      the end or start of the range.

      Historical context is also important when interpreting pathnames of
      renamed files/directories. Consider the following example:

      * revision 1: add tutorial.txt
      * revision 2: modify tutorial.txt
      * revision 3: rename tutorial.txt to guide.txt; add tutorial.txt

      In this case:

      * ``bzr log guide.txt`` will log the file added in revision 1

      * ``bzr log tutorial.txt`` will log the new file added in revision 3

      * ``bzr log -r2 -p tutorial.txt`` will show the changes made to
        the original file in revision 2.

      * ``bzr log -r2 -p guide.txt`` will display an error message as there
        was no file called guide.txt in revision 2.

      Renames are always followed by log. By design, there is no need to
      explicitly ask for this (and no way to stop logging a file back
      until it was last renamed).

    :Other filtering:

      The --match option can be used for finding revisions that match a
      regular expression in a commit message, committer, author or bug.
      Specifying the option several times will match any of the supplied
      expressions. --match-author, --match-bugs, --match-committer and
      --match-message can be used to only match a specific field.

    :Tips & tricks:

      GUI tools and IDEs are often better at exploring history than command
      line tools: you may prefer qlog or viz from qbzr or bzr-gtk, the
      bzr-explorer shell, or the Loggerhead web interface.  See the Plugin
      Guide <http://doc.bazaar.canonical.com/plugins/en/> and
      <http://wiki.bazaar.canonical.com/IDEIntegration>.  

      You may find it useful to add the aliases below to ``bazaar.conf``::

        [ALIASES]
        tip = log -r-1
        top = log -l10 --line
        show = log -v -p

      ``bzr tip`` will then show the latest revision while ``bzr top``
      will show the last 10 mainline revisions. To see the details of a
      particular revision X,  ``bzr show -rX``.

      If you are interested in looking deeper into a particular merge X,
      use ``bzr log -n0 -rX``.

      ``bzr log -v`` on a branch with lots of history is currently
      very slow. A fix for this issue is currently under development.
      With or without that fix, it is recommended that a revision range
      be given when using the -v option.

      bzr has a generic full-text matching plugin, bzr-search, that can be
      used to find revisions matching user names, commit messages, etc.
      Among other features, this plugin can find all revisions containing
      a list of words but not others.

      When exploring non-mainline history on large projects with deep
      history, the performance of log can be greatly improved by installing
      the historycache plugin. This plugin buffers historical information
      trading disk space for faster speed.
    """
    takes_args = ['file*']
    _see_also = ['log-formats', 'revisionspec']
    takes_options = [
            Option('forward',
                   help='Show from oldest to newest.'),
            'timezone',
            custom_help('verbose',
                   help='Show files changed in each revision.'),
            'show-ids',
            'revision',
            Option('change',
                   type=bzrlib.option._parse_revision_str,
                   short_name='c',
                   help='Show just the specified revision.'
                   ' See also "help revisionspec".'),
            'log-format',
            RegistryOption('authors',
                'What names to list as authors - first, all or committer.',
                title='Authors',
                lazy_registry=('bzrlib.log', 'author_list_registry'),
            ),
            Option('levels',
                   short_name='n',
                   help='Number of levels to display - 0 for all, 1 for flat.',
                   argname='N',
                   type=_parse_levels),
            Option('message',
                   help='Show revisions whose message matches this '
                        'regular expression.',
                   type=str,
                   hidden=True),
            Option('limit',
                   short_name='l',
                   help='Limit the output to the first N revisions.',
                   argname='N',
                   type=_parse_limit),
            Option('show-diff',
                   short_name='p',
                   help='Show changes made in each revision as a patch.'),
            Option('include-merged',
                   help='Show merged revisions like --levels 0 does.'),
            Option('include-merges', hidden=True,
                   help='Historical alias for --include-merged.'),
            Option('omit-merges',
                   help='Do not report commits with more than one parent.'),
            Option('exclude-common-ancestry',
                   help='Display only the revisions that are not part'
                   ' of both ancestries (require -rX..Y).'
                   ),
            Option('signatures',
                   help='Show digital signature validity.'),
            ListOption('match',
                short_name='m',
                help='Show revisions whose properties match this '
                'expression.',
                type=str),
            ListOption('match-message',
                   help='Show revisions whose message matches this '
                   'expression.',
                type=str),
            ListOption('match-committer',
                   help='Show revisions whose committer matches this '
                   'expression.',
                type=str),
            ListOption('match-author',
                   help='Show revisions whose authors match this '
                   'expression.',
                type=str),
            ListOption('match-bugs',
                   help='Show revisions whose bugs match this '
                   'expression.',
                type=str)
            ]
    encoding_type = 'replace'

    @display_command
    def run(self, file_list=None, timezone='original',
            verbose=False,
            show_ids=False,
            forward=False,
            revision=None,
            change=None,
            log_format=None,
            levels=None,
            message=None,
            limit=None,
            show_diff=False,
            include_merged=None,
            authors=None,
            exclude_common_ancestry=False,
            signatures=False,
            match=None,
            match_message=None,
            match_committer=None,
            match_author=None,
            match_bugs=None,
            omit_merges=False,
            include_merges=symbol_versioning.DEPRECATED_PARAMETER,
            ):
        from bzrlib.log import (
            Logger,
            make_log_request_dict,
            _get_info_for_log_files,
            )
        direction = (forward and 'forward') or 'reverse'
        if symbol_versioning.deprecated_passed(include_merges):
            ui.ui_factory.show_user_warning(
                'deprecated_command_option',
                deprecated_name='--include-merges',
                recommended_name='--include-merged',
                deprecated_in_version='2.5',
                command=self.invoked_as)
            if include_merged is None:
                include_merged = include_merges
            else:
                raise errors.BzrCommandError(gettext(
                    '{0} and {1} are mutually exclusive').format(
                    '--include-merges', '--include-merged'))
        if include_merged is None:
            include_merged = False
        if (exclude_common_ancestry
            and (revision is None or len(revision) != 2)):
            raise errors.BzrCommandError(gettext(
                '--exclude-common-ancestry requires -r with two revisions'))
        if include_merged:
            if levels is None:
                levels = 0
            else:
                raise errors.BzrCommandError(gettext(
                    '{0} and {1} are mutually exclusive').format(
                    '--levels', '--include-merged'))

        if change is not None:
            if len(change) > 1:
                raise errors.RangeInChangeOption()
            if revision is not None:
                raise errors.BzrCommandError(gettext(
                    '{0} and {1} are mutually exclusive').format(
                    '--revision', '--change'))
            else:
                revision = change

        file_ids = []
        filter_by_dir = False
        if file_list:
            # find the file ids to log and check for directory filtering
            b, file_info_list, rev1, rev2 = _get_info_for_log_files(
                revision, file_list, self.add_cleanup)
            for relpath, file_id, kind in file_info_list:
                if file_id is None:
                    raise errors.BzrCommandError(gettext(
                        "Path unknown at end or start of revision range: %s") %
                        relpath)
                # If the relpath is the top of the tree, we log everything
                if relpath == '':
                    file_ids = []
                    break
                else:
                    file_ids.append(file_id)
                filter_by_dir = filter_by_dir or (
                    kind in ['directory', 'tree-reference'])
        else:
            # log everything
            # FIXME ? log the current subdir only RBC 20060203
            if revision is not None \
                    and len(revision) > 0 and revision[0].get_branch():
                location = revision[0].get_branch()
            else:
                location = '.'
            dir, relpath = controldir.ControlDir.open_containing(location)
            b = dir.open_branch()
            self.add_cleanup(b.lock_read().unlock)
            rev1, rev2 = _get_revision_range(revision, b, self.name())

        if b.get_config().validate_signatures_in_log():
            signatures = True

        if signatures:
            if not gpg.GPGStrategy.verify_signatures_available():
                raise errors.GpgmeNotInstalled(None)

        # Decide on the type of delta & diff filtering to use
        # TODO: add an --all-files option to make this configurable & consistent
        if not verbose:
            delta_type = None
        else:
            delta_type = 'full'
        if not show_diff:
            diff_type = None
        elif file_ids:
            diff_type = 'partial'
        else:
            diff_type = 'full'

        # Build the log formatter
        if log_format is None:
            log_format = log.log_formatter_registry.get_default(b)
        # Make a non-encoding output to include the diffs - bug 328007
        unencoded_output = ui.ui_factory.make_output_stream(encoding_type='exact')
        lf = log_format(show_ids=show_ids, to_file=self.outf,
                        to_exact_file=unencoded_output,
                        show_timezone=timezone,
                        delta_format=get_verbosity_level(),
                        levels=levels,
                        show_advice=levels is None,
                        author_list_handler=authors)

        # Choose the algorithm for doing the logging. It's annoying
        # having multiple code paths like this but necessary until
        # the underlying repository format is faster at generating
        # deltas or can provide everything we need from the indices.
        # The default algorithm - match-using-deltas - works for
        # multiple files and directories and is faster for small
        # amounts of history (200 revisions say). However, it's too
        # slow for logging a single file in a repository with deep
        # history, i.e. > 10K revisions. In the spirit of "do no
        # evil when adding features", we continue to use the
        # original algorithm - per-file-graph - for the "single
        # file that isn't a directory without showing a delta" case.
        partial_history = revision and b.repository._format.supports_chks
        match_using_deltas = (len(file_ids) != 1 or filter_by_dir
            or delta_type or partial_history)

        match_dict = {}
        if match:
            match_dict[''] = match
        if match_message:
            match_dict['message'] = match_message
        if match_committer:
            match_dict['committer'] = match_committer
        if match_author:
            match_dict['author'] = match_author
        if match_bugs:
            match_dict['bugs'] = match_bugs

        # Build the LogRequest and execute it
        if len(file_ids) == 0:
            file_ids = None
        rqst = make_log_request_dict(
            direction=direction, specific_fileids=file_ids,
            start_revision=rev1, end_revision=rev2, limit=limit,
            message_search=message, delta_type=delta_type,
            diff_type=diff_type, _match_using_deltas=match_using_deltas,
            exclude_common_ancestry=exclude_common_ancestry, match=match_dict,
            signature=signatures, omit_merges=omit_merges,
            )
        Logger(b, rqst).show(lf)


def _get_revision_range(revisionspec_list, branch, command_name):
    """Take the input of a revision option and turn it into a revision range.

    It returns RevisionInfo objects which can be used to obtain the rev_id's
    of the desired revisions. It does some user input validations.
    """
    if revisionspec_list is None:
        rev1 = None
        rev2 = None
    elif len(revisionspec_list) == 1:
        rev1 = rev2 = revisionspec_list[0].in_history(branch)
    elif len(revisionspec_list) == 2:
        start_spec = revisionspec_list[0]
        end_spec = revisionspec_list[1]
        if end_spec.get_branch() != start_spec.get_branch():
            # b is taken from revision[0].get_branch(), and
            # show_log will use its revision_history. Having
            # different branches will lead to weird behaviors.
            raise errors.BzrCommandError(gettext(
                "bzr %s doesn't accept two revisions in different"
                " branches.") % command_name)
        if start_spec.spec is None:
            # Avoid loading all the history.
            rev1 = RevisionInfo(branch, None, None)
        else:
            rev1 = start_spec.in_history(branch)
        # Avoid loading all of history when we know a missing
        # end of range means the last revision ...
        if end_spec.spec is None:
            last_revno, last_revision_id = branch.last_revision_info()
            rev2 = RevisionInfo(branch, last_revno, last_revision_id)
        else:
            rev2 = end_spec.in_history(branch)
    else:
        raise errors.BzrCommandError(gettext(
            'bzr %s --revision takes one or two values.') % command_name)
    return rev1, rev2


def _revision_range_to_revid_range(revision_range):
    rev_id1 = None
    rev_id2 = None
    if revision_range[0] is not None:
        rev_id1 = revision_range[0].rev_id
    if revision_range[1] is not None:
        rev_id2 = revision_range[1].rev_id
    return rev_id1, rev_id2

def get_log_format(long=False, short=False, line=False, default='long'):
    log_format = default
    if long:
        log_format = 'long'
    if short:
        log_format = 'short'
    if line:
        log_format = 'line'
    return log_format


class cmd_touching_revisions(Command):
    __doc__ = """Return revision-ids which affected a particular file.

    A more user-friendly interface is "bzr log FILE".
    """

    hidden = True
    takes_args = ["filename"]

    @display_command
    def run(self, filename):
        tree, relpath = WorkingTree.open_containing(filename)
        file_id = tree.path2id(relpath)
        b = tree.branch
        self.add_cleanup(b.lock_read().unlock)
        touching_revs = log.find_touching_revisions(b, file_id)
        for revno, revision_id, what in touching_revs:
            self.outf.write("%6d %s\n" % (revno, what))


class cmd_ls(Command):
    __doc__ = """List files in a tree.
    """

    _see_also = ['status', 'cat']
    takes_args = ['path?']
    takes_options = [
            'verbose',
            'revision',
            Option('recursive', short_name='R',
                   help='Recurse into subdirectories.'),
            Option('from-root',
                   help='Print paths relative to the root of the branch.'),
            Option('unknown', short_name='u',
                help='Print unknown files.'),
            Option('versioned', help='Print versioned files.',
                   short_name='V'),
            Option('ignored', short_name='i',
                help='Print ignored files.'),
            Option('kind', short_name='k',
                   help='List entries of a particular kind: file, directory, symlink.',
                   type=unicode),
            'null',
            'show-ids',
            'directory',
            ]
    @display_command
    def run(self, revision=None, verbose=False,
            recursive=False, from_root=False,
            unknown=False, versioned=False, ignored=False,
            null=False, kind=None, show_ids=False, path=None, directory=None):

        if kind and kind not in ('file', 'directory', 'symlink'):
            raise errors.BzrCommandError(gettext('invalid kind specified'))

        if verbose and null:
            raise errors.BzrCommandError(gettext('Cannot set both --verbose and --null'))
        all = not (unknown or versioned or ignored)

        selection = {'I':ignored, '?':unknown, 'V':versioned}

        if path is None:
            fs_path = '.'
        else:
            if from_root:
                raise errors.BzrCommandError(gettext('cannot specify both --from-root'
                                             ' and PATH'))
            fs_path = path
        tree, branch, relpath = \
            _open_directory_or_containing_tree_or_branch(fs_path, directory)

        # Calculate the prefix to use
        prefix = None
        if from_root:
            if relpath:
                prefix = relpath + '/'
        elif fs_path != '.' and not fs_path.endswith('/'):
            prefix = fs_path + '/'

        if revision is not None or tree is None:
            tree = _get_one_revision_tree('ls', revision, branch=branch)

        apply_view = False
        if isinstance(tree, WorkingTree) and tree.supports_views():
            view_files = tree.views.lookup_view()
            if view_files:
                apply_view = True
                view_str = views.view_display_str(view_files)
                note(gettext("Ignoring files outside view. View is %s") % view_str)

        self.add_cleanup(tree.lock_read().unlock)
        for fp, fc, fkind, fid, entry in tree.list_files(include_root=False,
            from_dir=relpath, recursive=recursive):
            # Apply additional masking
            if not all and not selection[fc]:
                continue
            if kind is not None and fkind != kind:
                continue
            if apply_view:
                try:
                    if relpath:
                        fullpath = osutils.pathjoin(relpath, fp)
                    else:
                        fullpath = fp
                    views.check_path_in_view(tree, fullpath)
                except errors.FileOutsideView:
                    continue

            # Output the entry
            if prefix:
                fp = osutils.pathjoin(prefix, fp)
            kindch = entry.kind_character()
            outstring = fp + kindch
            ui.ui_factory.clear_term()
            if verbose:
                outstring = '%-8s %s' % (fc, outstring)
                if show_ids and fid is not None:
                    outstring = "%-50s %s" % (outstring, fid)
                self.outf.write(outstring + '\n')
            elif null:
                self.outf.write(fp + '\0')
                if show_ids:
                    if fid is not None:
                        self.outf.write(fid)
                    self.outf.write('\0')
                self.outf.flush()
            else:
                if show_ids:
                    if fid is not None:
                        my_id = fid
                    else:
                        my_id = ''
                    self.outf.write('%-50s %s\n' % (outstring, my_id))
                else:
                    self.outf.write(outstring + '\n')


class cmd_unknowns(Command):
    __doc__ = """List unknown files.
    """

    hidden = True
    _see_also = ['ls']
    takes_options = ['directory']

    @display_command
    def run(self, directory=u'.'):
        for f in WorkingTree.open_containing(directory)[0].unknowns():
            self.outf.write(osutils.quotefn(f) + '\n')


class cmd_ignore(Command):
    __doc__ = """Ignore specified files or patterns.

    See ``bzr help patterns`` for details on the syntax of patterns.

    If a .bzrignore file does not exist, the ignore command
    will create one and add the specified files or patterns to the newly
    created file. The ignore command will also automatically add the 
    .bzrignore file to be versioned. Creating a .bzrignore file without
    the use of the ignore command will require an explicit add command.

    To remove patterns from the ignore list, edit the .bzrignore file.
    After adding, editing or deleting that file either indirectly by
    using this command or directly by using an editor, be sure to commit
    it.
    
    Bazaar also supports a global ignore file ~/.bazaar/ignore. On Windows
    the global ignore file can be found in the application data directory as
    C:\\Documents and Settings\\<user>\\Application Data\\Bazaar\\2.0\\ignore.
    Global ignores are not touched by this command. The global ignore file
    can be edited directly using an editor.

    Patterns prefixed with '!' are exceptions to ignore patterns and take
    precedence over regular ignores.  Such exceptions are used to specify
    files that should be versioned which would otherwise be ignored.
    
    Patterns prefixed with '!!' act as regular ignore patterns, but have
    precedence over the '!' exception patterns.

    :Notes: 
        
    * Ignore patterns containing shell wildcards must be quoted from
      the shell on Unix.

    * Ignore patterns starting with "#" act as comments in the ignore file.
      To ignore patterns that begin with that character, use the "RE:" prefix.

    :Examples:
        Ignore the top level Makefile::

            bzr ignore ./Makefile

        Ignore .class files in all directories...::

            bzr ignore "*.class"

        ...but do not ignore "special.class"::

            bzr ignore "!special.class"

        Ignore files whose name begins with the "#" character::

            bzr ignore "RE:^#"

        Ignore .o files under the lib directory::

            bzr ignore "lib/**/*.o"

        Ignore .o files under the lib directory::

            bzr ignore "RE:lib/.*\.o"

        Ignore everything but the "debian" toplevel directory::

            bzr ignore "RE:(?!debian/).*"
        
        Ignore everything except the "local" toplevel directory,
        but always ignore autosave files ending in ~, even under local/::
        
            bzr ignore "*"
            bzr ignore "!./local"
            bzr ignore "!!*~"
    """

    _see_also = ['status', 'ignored', 'patterns']
    takes_args = ['name_pattern*']
    takes_options = ['directory',
        Option('default-rules',
               help='Display the default ignore rules that bzr uses.')
        ]

    def run(self, name_pattern_list=None, default_rules=None,
            directory=u'.'):
        from bzrlib import ignores
        if default_rules is not None:
            # dump the default rules and exit
            for pattern in ignores.USER_DEFAULTS:
                self.outf.write("%s\n" % pattern)
            return
        if not name_pattern_list:
            raise errors.BzrCommandError(gettext("ignore requires at least one "
                "NAME_PATTERN or --default-rules."))
        name_pattern_list = [globbing.normalize_pattern(p)
                             for p in name_pattern_list]
        bad_patterns = ''
        bad_patterns_count = 0
        for p in name_pattern_list:
            if not globbing.Globster.is_pattern_valid(p):
                bad_patterns_count += 1
                bad_patterns += ('\n  %s' % p)
        if bad_patterns:
            msg = (ngettext('Invalid ignore pattern found. %s', 
                            'Invalid ignore patterns found. %s',
                            bad_patterns_count) % bad_patterns)
            ui.ui_factory.show_error(msg)
            raise errors.InvalidPattern('')
        for name_pattern in name_pattern_list:
            if (name_pattern[0] == '/' or
                (len(name_pattern) > 1 and name_pattern[1] == ':')):
                raise errors.BzrCommandError(gettext(
                    "NAME_PATTERN should not be an absolute path"))
        tree, relpath = WorkingTree.open_containing(directory)
        ignores.tree_ignores_add_patterns(tree, name_pattern_list)
        ignored = globbing.Globster(name_pattern_list)
        matches = []
        self.add_cleanup(tree.lock_read().unlock)
        for entry in tree.list_files():
            id = entry[3]
            if id is not None:
                filename = entry[0]
                if ignored.match(filename):
                    matches.append(filename)
        if len(matches) > 0:
            self.outf.write(gettext("Warning: the following files are version "
                  "controlled and match your ignore pattern:\n%s"
                  "\nThese files will continue to be version controlled"
                  " unless you 'bzr remove' them.\n") % ("\n".join(matches),))


class cmd_ignored(Command):
    __doc__ = """List ignored files and the patterns that matched them.

    List all the ignored files and the ignore pattern that caused the file to
    be ignored.

    Alternatively, to list just the files::

        bzr ls --ignored
    """

    encoding_type = 'replace'
    _see_also = ['ignore', 'ls']
    takes_options = ['directory']

    @display_command
    def run(self, directory=u'.'):
        tree = WorkingTree.open_containing(directory)[0]
        self.add_cleanup(tree.lock_read().unlock)
        for path, file_class, kind, file_id, entry in tree.list_files():
            if file_class != 'I':
                continue
            ## XXX: Slightly inefficient since this was already calculated
            pat = tree.is_ignored(path)
            self.outf.write('%-50s %s\n' % (path, pat))


class cmd_lookup_revision(Command):
    __doc__ = """Lookup the revision-id from a revision-number

    :Examples:
        bzr lookup-revision 33
    """
    hidden = True
    takes_args = ['revno']
    takes_options = ['directory']

    @display_command
    def run(self, revno, directory=u'.'):
        try:
            revno = int(revno)
        except ValueError:
            raise errors.BzrCommandError(gettext("not a valid revision-number: %r")
                                         % revno)
        revid = WorkingTree.open_containing(directory)[0].branch.get_rev_id(revno)
        self.outf.write("%s\n" % revid)


class cmd_export(Command):
    __doc__ = """Export current or past revision to a destination directory or archive.

    If no revision is specified this exports the last committed revision.

    Format may be an "exporter" name, such as tar, tgz, tbz2.  If none is
    given, try to find the format with the extension. If no extension
    is found exports to a directory (equivalent to --format=dir).

    If root is supplied, it will be used as the root directory inside
    container formats (tar, zip, etc). If it is not supplied it will default
    to the exported filename. The root option has no effect for 'dir' format.

    If branch is omitted then the branch containing the current working
    directory will be used.

    Note: Export of tree with non-ASCII filenames to zip is not supported.

      =================       =========================
      Supported formats       Autodetected by extension
      =================       =========================
         dir                         (none)
         tar                          .tar
         tbz2                    .tar.bz2, .tbz2
         tgz                      .tar.gz, .tgz
         zip                          .zip
      =================       =========================
    """
    encoding = 'exact'
    takes_args = ['dest', 'branch_or_subdir?']
    takes_options = ['directory',
        Option('format',
               help="Type of file to export to.",
               type=unicode),
        'revision',
        Option('filters', help='Apply content filters to export the '
                'convenient form.'),
        Option('root',
               type=str,
               help="Name of the root directory inside the exported file."),
        Option('per-file-timestamps',
               help='Set modification time of files to that of the last '
                    'revision in which it was changed.'),
        Option('uncommitted',
               help='Export the working tree contents rather than that of the '
                    'last revision.'),
        ]
    def run(self, dest, branch_or_subdir=None, revision=None, format=None,
        root=None, filters=False, per_file_timestamps=False, uncommitted=False,
        directory=u'.'):
        from bzrlib.export import export

        if branch_or_subdir is None:
            branch_or_subdir = directory

        (tree, b, subdir) = controldir.ControlDir.open_containing_tree_or_branch(
            branch_or_subdir)
        if tree is not None:
            self.add_cleanup(tree.lock_read().unlock)

        if uncommitted:
            if tree is None:
                raise errors.BzrCommandError(
                    gettext("--uncommitted requires a working tree"))
            export_tree = tree
        else:
            export_tree = _get_one_revision_tree('export', revision, branch=b, tree=tree)
        try:
            export(export_tree, dest, format, root, subdir, filtered=filters,
                   per_file_timestamps=per_file_timestamps)
        except errors.NoSuchExportFormat, e:
            raise errors.BzrCommandError(
                gettext('Unsupported export format: %s') % e.format)


class cmd_cat(Command):
    __doc__ = """Write the contents of a file as of a given revision to standard output.

    If no revision is nominated, the last revision is used.

    Note: Take care to redirect standard output when using this command on a
    binary file.
    """

    _see_also = ['ls']
    takes_options = ['directory',
        Option('name-from-revision', help='The path name in the old tree.'),
        Option('filters', help='Apply content filters to display the '
                'convenience form.'),
        'revision',
        ]
    takes_args = ['filename']
    encoding_type = 'exact'

    @display_command
    def run(self, filename, revision=None, name_from_revision=False,
            filters=False, directory=None):
        if revision is not None and len(revision) != 1:
            raise errors.BzrCommandError(gettext("bzr cat --revision takes exactly"
                                         " one revision specifier"))
        tree, branch, relpath = \
            _open_directory_or_containing_tree_or_branch(filename, directory)
        self.add_cleanup(branch.lock_read().unlock)
        return self._run(tree, branch, relpath, filename, revision,
                         name_from_revision, filters)

    def _run(self, tree, b, relpath, filename, revision, name_from_revision,
        filtered):
        if tree is None:
            tree = b.basis_tree()
        rev_tree = _get_one_revision_tree('cat', revision, branch=b)
        self.add_cleanup(rev_tree.lock_read().unlock)

        old_file_id = rev_tree.path2id(relpath)

        # TODO: Split out this code to something that generically finds the
        # best id for a path across one or more trees; it's like
        # find_ids_across_trees but restricted to find just one. -- mbp
        # 20110705.
        if name_from_revision:
            # Try in revision if requested
            if old_file_id is None:
                raise errors.BzrCommandError(gettext(
                    "{0!r} is not present in revision {1}").format(
                        filename, rev_tree.get_revision_id()))
            else:
                actual_file_id = old_file_id
        else:
            cur_file_id = tree.path2id(relpath)
            if cur_file_id is not None and rev_tree.has_id(cur_file_id):
                actual_file_id = cur_file_id
            elif old_file_id is not None:
                actual_file_id = old_file_id
            else:
                raise errors.BzrCommandError(gettext(
                    "{0!r} is not present in revision {1}").format(
                        filename, rev_tree.get_revision_id()))
        if filtered:
            from bzrlib.filter_tree import ContentFilterTree
            filter_tree = ContentFilterTree(rev_tree,
                rev_tree._content_filter_stack)
            content = filter_tree.get_file_text(actual_file_id)
        else:
            content = rev_tree.get_file_text(actual_file_id)
        self.cleanup_now()
        self.outf.write(content)


class cmd_local_time_offset(Command):
    __doc__ = """Show the offset in seconds from GMT to local time."""
    hidden = True
    @display_command
    def run(self):
        self.outf.write("%s\n" % osutils.local_time_offset())



class cmd_commit(Command):
    __doc__ = """Commit changes into a new revision.

    An explanatory message needs to be given for each commit. This is
    often done by using the --message option (getting the message from the
    command line) or by using the --file option (getting the message from
    a file). If neither of these options is given, an editor is opened for
    the user to enter the message. To see the changed files in the
    boilerplate text loaded into the editor, use the --show-diff option.

    By default, the entire tree is committed and the person doing the
    commit is assumed to be the author. These defaults can be overridden
    as explained below.

    :Selective commits:

      If selected files are specified, only changes to those files are
      committed.  If a directory is specified then the directory and
      everything within it is committed.
  
      When excludes are given, they take precedence over selected files.
      For example, to commit only changes within foo, but not changes
      within foo/bar::
  
        bzr commit foo -x foo/bar
  
      A selective commit after a merge is not yet supported.

    :Custom authors:

      If the author of the change is not the same person as the committer,
      you can specify the author's name using the --author option. The
      name should be in the same format as a committer-id, e.g.
      "John Doe <jdoe@example.com>". If there is more than one author of
      the change you can specify the option multiple times, once for each
      author.
  
    :Checks:

      A common mistake is to forget to add a new file or directory before
      running the commit command. The --strict option checks for unknown
      files and aborts the commit if any are found. More advanced pre-commit
      checks can be implemented by defining hooks. See ``bzr help hooks``
      for details.

    :Things to note:

      If you accidentially commit the wrong changes or make a spelling
      mistake in the commit message say, you can use the uncommit command
      to undo it. See ``bzr help uncommit`` for details.

      Hooks can also be configured to run after a commit. This allows you
      to trigger updates to external systems like bug trackers. The --fixes
      option can be used to record the association between a revision and
      one or more bugs. See ``bzr help bugs`` for details.
    """

    _see_also = ['add', 'bugs', 'hooks', 'uncommit']
    takes_args = ['selected*']
    takes_options = [
            ListOption('exclude', type=str, short_name='x',
                help="Do not consider changes made to a given path."),
            Option('message', type=unicode,
                   short_name='m',
                   help="Description of the new revision."),
            'verbose',
             Option('unchanged',
                    help='Commit even if nothing has changed.'),
             Option('file', type=str,
                    short_name='F',
                    argname='msgfile',
                    help='Take commit message from this file.'),
             Option('strict',
                    help="Refuse to commit if there are unknown "
                    "files in the working tree."),
             Option('commit-time', type=str,
                    help="Manually set a commit time using commit date "
                    "format, e.g. '2009-10-10 08:00:00 +0100'."),
             ListOption('fixes', type=str,
                    help="Mark a bug as being fixed by this revision "
                         "(see \"bzr help bugs\")."),
             ListOption('author', type=unicode,
                    help="Set the author's name, if it's different "
                         "from the committer."),
             Option('local',
                    help="Perform a local commit in a bound "
                         "branch.  Local commits are not pushed to "
                         "the master branch until a normal commit "
                         "is performed."
                    ),
             Option('show-diff', short_name='p',
                    help='When no message is supplied, show the diff along'
                    ' with the status summary in the message editor.'),
             Option('lossy', 
                    help='When committing to a foreign version control '
                    'system do not push data that can not be natively '
                    'represented.'),
             ]
    aliases = ['ci', 'checkin']

    def _iter_bug_fix_urls(self, fixes, branch):
        default_bugtracker  = None
        # Configure the properties for bug fixing attributes.
        for fixed_bug in fixes:
            tokens = fixed_bug.split(':')
            if len(tokens) == 1:
                if default_bugtracker is None:
                    branch_config = branch.get_config()
                    default_bugtracker = branch_config.get_user_option(
                        "bugtracker")
                if default_bugtracker is None:
                    raise errors.BzrCommandError(gettext(
                        "No tracker specified for bug %s. Use the form "
                        "'tracker:id' or specify a default bug tracker "
                        "using the `bugtracker` option.\nSee "
                        "\"bzr help bugs\" for more information on this "
                        "feature. Commit refused.") % fixed_bug)
                tag = default_bugtracker
                bug_id = tokens[0]
            elif len(tokens) != 2:
                raise errors.BzrCommandError(gettext(
                    "Invalid bug %s. Must be in the form of 'tracker:id'. "
                    "See \"bzr help bugs\" for more information on this "
                    "feature.\nCommit refused.") % fixed_bug)
            else:
                tag, bug_id = tokens
            try:
                yield bugtracker.get_bug_url(tag, branch, bug_id)
            except errors.UnknownBugTrackerAbbreviation:
                raise errors.BzrCommandError(gettext(
                    'Unrecognized bug %s. Commit refused.') % fixed_bug)
            except errors.MalformedBugIdentifier, e:
                raise errors.BzrCommandError(gettext(
                    "%s\nCommit refused.") % (str(e),))

    def run(self, message=None, file=None, verbose=False, selected_list=None,
            unchanged=False, strict=False, local=False, fixes=None,
            author=None, show_diff=False, exclude=None, commit_time=None,
            lossy=False):
        from bzrlib.errors import (
            PointlessCommit,
            ConflictsInTree,
            StrictCommitFailed
        )
        from bzrlib.msgeditor import (
            edit_commit_message_encoded,
            generate_commit_message_template,
            make_commit_message_template_encoded,
            set_commit_message,
        )

        commit_stamp = offset = None
        if commit_time is not None:
            try:
                commit_stamp, offset = timestamp.parse_patch_date(commit_time)
            except ValueError, e:
                raise errors.BzrCommandError(gettext(
                    "Could not parse --commit-time: " + str(e)))

        properties = {}

        tree, selected_list = WorkingTree.open_containing_paths(selected_list)
        if selected_list == ['']:
            # workaround - commit of root of tree should be exactly the same
            # as just default commit in that tree, and succeed even though
            # selected-file merge commit is not done yet
            selected_list = []

        if fixes is None:
            fixes = []
        bug_property = bugtracker.encode_fixes_bug_urls(
            self._iter_bug_fix_urls(fixes, tree.branch))
        if bug_property:
            properties['bugs'] = bug_property

        if local and not tree.branch.get_bound_location():
            raise errors.LocalRequiresBoundBranch()

        if message is not None:
            try:
                file_exists = osutils.lexists(message)
            except UnicodeError:
                # The commit message contains unicode characters that can't be
                # represented in the filesystem encoding, so that can't be a
                # file.
                file_exists = False
            if file_exists:
                warning_msg = (
                    'The commit message is a file name: "%(f)s".\n'
                    '(use --file "%(f)s" to take commit message from that file)'
                    % { 'f': message })
                ui.ui_factory.show_warning(warning_msg)
            if '\r' in message:
                message = message.replace('\r\n', '\n')
                message = message.replace('\r', '\n')
            if file:
                raise errors.BzrCommandError(gettext(
                    "please specify either --message or --file"))

        def get_message(commit_obj):
            """Callback to get commit message"""
            if file:
                f = open(file)
                try:
                    my_message = f.read().decode(osutils.get_user_encoding())
                finally:
                    f.close()
            elif message is not None:
                my_message = message
            else:
                # No message supplied: make one up.
                # text is the status of the tree
                text = make_commit_message_template_encoded(tree,
                        selected_list, diff=show_diff,
                        output_encoding=osutils.get_user_encoding())
                # start_message is the template generated from hooks
                # XXX: Warning - looks like hooks return unicode,
                # make_commit_message_template_encoded returns user encoding.
                # We probably want to be using edit_commit_message instead to
                # avoid this.
                my_message = set_commit_message(commit_obj)
                if my_message is None:
                    start_message = generate_commit_message_template(commit_obj)
                    my_message = edit_commit_message_encoded(text,
                        start_message=start_message)
                if my_message is None:
                    raise errors.BzrCommandError(gettext("please specify a commit"
                        " message with either --message or --file"))
                if my_message == "":
                    raise errors.BzrCommandError(gettext("Empty commit message specified."
                            " Please specify a commit message with either"
                            " --message or --file or leave a blank message"
                            " with --message \"\"."))
            return my_message

        # The API permits a commit with a filter of [] to mean 'select nothing'
        # but the command line should not do that.
        if not selected_list:
            selected_list = None
        try:
            tree.commit(message_callback=get_message,
                        specific_files=selected_list,
                        allow_pointless=unchanged, strict=strict, local=local,
                        reporter=None, verbose=verbose, revprops=properties,
                        authors=author, timestamp=commit_stamp,
                        timezone=offset,
                        exclude=tree.safe_relpath_files(exclude),
                        lossy=lossy)
        except PointlessCommit:
            raise errors.BzrCommandError(gettext("No changes to commit."
                " Please 'bzr add' the files you want to commit, or use"
                " --unchanged to force an empty commit."))
        except ConflictsInTree:
            raise errors.BzrCommandError(gettext('Conflicts detected in working '
                'tree.  Use "bzr conflicts" to list, "bzr resolve FILE" to'
                ' resolve.'))
        except StrictCommitFailed:
            raise errors.BzrCommandError(gettext("Commit refused because there are"
                              " unknown files in the working tree."))
        except errors.BoundBranchOutOfDate, e:
            e.extra_help = (gettext("\n"
                'To commit to master branch, run update and then commit.\n'
                'You can also pass --local to commit to continue working '
                'disconnected.'))
            raise


class cmd_check(Command):
    __doc__ = """Validate working tree structure, branch consistency and repository history.

    This command checks various invariants about branch and repository storage
    to detect data corruption or bzr bugs.

    The working tree and branch checks will only give output if a problem is
    detected. The output fields of the repository check are:

    revisions
        This is just the number of revisions checked.  It doesn't
        indicate a problem.

    versionedfiles
        This is just the number of versionedfiles checked.  It
        doesn't indicate a problem.

    unreferenced ancestors
        Texts that are ancestors of other texts, but
        are not properly referenced by the revision ancestry.  This is a
        subtle problem that Bazaar can work around.

    unique file texts
        This is the total number of unique file contents
        seen in the checked revisions.  It does not indicate a problem.

    repeated file texts
        This is the total number of repeated texts seen
        in the checked revisions.  Texts can be repeated when their file
        entries are modified, but the file contents are not.  It does not
        indicate a problem.

    If no restrictions are specified, all Bazaar data that is found at the given
    location will be checked.

    :Examples:

        Check the tree and branch at 'foo'::

            bzr check --tree --branch foo

        Check only the repository at 'bar'::

            bzr check --repo bar

        Check everything at 'baz'::

            bzr check baz
    """

    _see_also = ['reconcile']
    takes_args = ['path?']
    takes_options = ['verbose',
                     Option('branch', help="Check the branch related to the"
                                           " current directory."),
                     Option('repo', help="Check the repository related to the"
                                         " current directory."),
                     Option('tree', help="Check the working tree related to"
                                         " the current directory.")]

    def run(self, path=None, verbose=False, branch=False, repo=False,
            tree=False):
        from bzrlib.check import check_dwim
        if path is None:
            path = '.'
        if not branch and not repo and not tree:
            branch = repo = tree = True
        check_dwim(path, verbose, do_branch=branch, do_repo=repo, do_tree=tree)


class cmd_upgrade(Command):
    __doc__ = """Upgrade a repository, branch or working tree to a newer format.

    When the default format has changed after a major new release of
    Bazaar, you may be informed during certain operations that you
    should upgrade. Upgrading to a newer format may improve performance
    or make new features available. It may however limit interoperability
    with older repositories or with older versions of Bazaar.

    If you wish to upgrade to a particular format rather than the
    current default, that can be specified using the --format option.
    As a consequence, you can use the upgrade command this way to
    "downgrade" to an earlier format, though some conversions are
    a one way process (e.g. changing from the 1.x default to the
    2.x default) so downgrading is not always possible.

    A backup.bzr.~#~ directory is created at the start of the conversion
    process (where # is a number). By default, this is left there on
    completion. If the conversion fails, delete the new .bzr directory
    and rename this one back in its place. Use the --clean option to ask
    for the backup.bzr directory to be removed on successful conversion.
    Alternatively, you can delete it by hand if everything looks good
    afterwards.

    If the location given is a shared repository, dependent branches
    are also converted provided the repository converts successfully.
    If the conversion of a branch fails, remaining branches are still
    tried.

    For more information on upgrades, see the Bazaar Upgrade Guide,
    http://doc.bazaar.canonical.com/latest/en/upgrade-guide/.
    """

    _see_also = ['check', 'reconcile', 'formats']
    takes_args = ['url?']
    takes_options = [
        RegistryOption('format',
            help='Upgrade to a specific format.  See "bzr help'
                 ' formats" for details.',
            lazy_registry=('bzrlib.controldir', 'format_registry'),
            converter=lambda name: controldir.format_registry.make_bzrdir(name),
            value_switches=True, title='Branch format'),
        Option('clean',
            help='Remove the backup.bzr directory if successful.'),
        Option('dry-run',
            help="Show what would be done, but don't actually do anything."),
    ]

    def run(self, url='.', format=None, clean=False, dry_run=False):
        from bzrlib.upgrade import upgrade
        exceptions = upgrade(url, format, clean_up=clean, dry_run=dry_run)
        if exceptions:
            if len(exceptions) == 1:
                # Compatibility with historical behavior
                raise exceptions[0]
            else:
                return 3


class cmd_whoami(Command):
    __doc__ = """Show or set bzr user id.

    :Examples:
        Show the email of the current user::

            bzr whoami --email

        Set the current user::

            bzr whoami "Frank Chu <fchu@example.com>"
    """
    takes_options = [ 'directory',
                      Option('email',
                             help='Display email address only.'),
                      Option('branch',
                             help='Set identity for the current branch instead of '
                                  'globally.'),
                    ]
    takes_args = ['name?']
    encoding_type = 'replace'

    @display_command
    def run(self, email=False, branch=False, name=None, directory=None):
        if name is None:
            if directory is None:
                # use branch if we're inside one; otherwise global config
                try:
                    c = Branch.open_containing(u'.')[0].get_config_stack()
                except errors.NotBranchError:
                    c = _mod_config.GlobalStack()
            else:
                c = Branch.open(directory).get_config_stack()
            identity = c.get('email')
            if email:
                self.outf.write(_mod_config.extract_email_address(identity)
                                + '\n')
            else:
                self.outf.write(identity + '\n')
            return

        if email:
            raise errors.BzrCommandError(gettext("--email can only be used to display existing "
                                         "identity"))

        # display a warning if an email address isn't included in the given name.
        try:
            _mod_config.extract_email_address(name)
        except errors.NoEmailInUsername, e:
            warning('"%s" does not seem to contain an email address.  '
                    'This is allowed, but not recommended.', name)

        # use global config unless --branch given
        if branch:
            if directory is None:
                c = Branch.open_containing(u'.')[0].get_config_stack()
            else:
                c = Branch.open(directory).get_config_stack()
        else:
            c = _mod_config.GlobalStack()
        c.set('email', name)


class cmd_nick(Command):
    __doc__ = """Print or set the branch nickname.

    If unset, the tree root directory name is used as the nickname.
    To print the current nickname, execute with no argument.

    Bound branches use the nickname of its master branch unless it is set
    locally.
    """

    _see_also = ['info']
    takes_args = ['nickname?']
    takes_options = ['directory']
    def run(self, nickname=None, directory=u'.'):
        branch = Branch.open_containing(directory)[0]
        if nickname is None:
            self.printme(branch)
        else:
            branch.nick = nickname

    @display_command
    def printme(self, branch):
        self.outf.write('%s\n' % branch.nick)


class cmd_alias(Command):
    __doc__ = """Set/unset and display aliases.

    :Examples:
        Show the current aliases::

            bzr alias

        Show the alias specified for 'll'::

            bzr alias ll

        Set an alias for 'll'::

            bzr alias ll="log --line -r-10..-1"

        To remove an alias for 'll'::

            bzr alias --remove ll

    """
    takes_args = ['name?']
    takes_options = [
        Option('remove', help='Remove the alias.'),
        ]

    def run(self, name=None, remove=False):
        if remove:
            self.remove_alias(name)
        elif name is None:
            self.print_aliases()
        else:
            equal_pos = name.find('=')
            if equal_pos == -1:
                self.print_alias(name)
            else:
                self.set_alias(name[:equal_pos], name[equal_pos+1:])

    def remove_alias(self, alias_name):
        if alias_name is None:
            raise errors.BzrCommandError(gettext(
                'bzr alias --remove expects an alias to remove.'))
        # If alias is not found, print something like:
        # unalias: foo: not found
        c = _mod_config.GlobalConfig()
        c.unset_alias(alias_name)

    @display_command
    def print_aliases(self):
        """Print out the defined aliases in a similar format to bash."""
        aliases = _mod_config.GlobalConfig().get_aliases()
        for key, value in sorted(aliases.iteritems()):
            self.outf.write('bzr alias %s="%s"\n' % (key, value))

    @display_command
    def print_alias(self, alias_name):
        from bzrlib.commands import get_alias
        alias = get_alias(alias_name)
        if alias is None:
            self.outf.write("bzr alias: %s: not found\n" % alias_name)
        else:
            self.outf.write(
                'bzr alias %s="%s"\n' % (alias_name, ' '.join(alias)))

    def set_alias(self, alias_name, alias_command):
        """Save the alias in the global config."""
        c = _mod_config.GlobalConfig()
        c.set_alias(alias_name, alias_command)


class cmd_selftest(Command):
    __doc__ = """Run internal test suite.

    If arguments are given, they are regular expressions that say which tests
    should run.  Tests matching any expression are run, and other tests are
    not run.

    Alternatively if --first is given, matching tests are run first and then
    all other tests are run.  This is useful if you have been working in a
    particular area, but want to make sure nothing else was broken.

    If --exclude is given, tests that match that regular expression are
    excluded, regardless of whether they match --first or not.

    To help catch accidential dependencies between tests, the --randomize
    option is useful. In most cases, the argument used is the word 'now'.
    Note that the seed used for the random number generator is displayed
    when this option is used. The seed can be explicitly passed as the
    argument to this option if required. This enables reproduction of the
    actual ordering used if and when an order sensitive problem is encountered.

    If --list-only is given, the tests that would be run are listed. This is
    useful when combined with --first, --exclude and/or --randomize to
    understand their impact. The test harness reports "Listed nn tests in ..."
    instead of "Ran nn tests in ..." when list mode is enabled.

    If the global option '--no-plugins' is given, plugins are not loaded
    before running the selftests.  This has two effects: features provided or
    modified by plugins will not be tested, and tests provided by plugins will
    not be run.

    Tests that need working space on disk use a common temporary directory,
    typically inside $TMPDIR or /tmp.

    If you set BZR_TEST_PDB=1 when running selftest, failing tests will drop
    into a pdb postmortem session.

    The --coverage=DIRNAME global option produces a report with covered code
    indicated.

    :Examples:
        Run only tests relating to 'ignore'::

            bzr selftest ignore

        Disable plugins and list tests as they're run::

            bzr --no-plugins selftest -v
    """
    # NB: this is used from the class without creating an instance, which is
    # why it does not have a self parameter.
    def get_transport_type(typestring):
        """Parse and return a transport specifier."""
        if typestring == "sftp":
            from bzrlib.tests import stub_sftp
            return stub_sftp.SFTPAbsoluteServer
        elif typestring == "memory":
            from bzrlib.tests import test_server
            return memory.MemoryServer
        elif typestring == "fakenfs":
            from bzrlib.tests import test_server
            return test_server.FakeNFSServer
        msg = "No known transport type %s. Supported types are: sftp\n" %\
            (typestring)
        raise errors.BzrCommandError(msg)

    hidden = True
    takes_args = ['testspecs*']
    takes_options = ['verbose',
                     Option('one',
                             help='Stop when one test fails.',
                             short_name='1',
                             ),
                     Option('transport',
                            help='Use a different transport by default '
                                 'throughout the test suite.',
                            type=get_transport_type),
                     Option('benchmark',
                            help='Run the benchmarks rather than selftests.',
                            hidden=True),
                     Option('lsprof-timed',
                            help='Generate lsprof output for benchmarked'
                                 ' sections of code.'),
                     Option('lsprof-tests',
                            help='Generate lsprof output for each test.'),
                     Option('first',
                            help='Run all tests, but run specified tests first.',
                            short_name='f',
                            ),
                     Option('list-only',
                            help='List the tests instead of running them.'),
                     RegistryOption('parallel',
                        help="Run the test suite in parallel.",
                        lazy_registry=('bzrlib.tests', 'parallel_registry'),
                        value_switches=False,
                        ),
                     Option('randomize', type=str, argname="SEED",
                            help='Randomize the order of tests using the given'
                                 ' seed or "now" for the current time.'),
                     ListOption('exclude', type=str, argname="PATTERN",
                                short_name='x',
                                help='Exclude tests that match this regular'
                                ' expression.'),
                     Option('subunit',
                        help='Output test progress via subunit.'),
                     Option('strict', help='Fail on missing dependencies or '
                            'known failures.'),
                     Option('load-list', type=str, argname='TESTLISTFILE',
                            help='Load a test id list from a text file.'),
                     ListOption('debugflag', type=str, short_name='E',
                                help='Turn on a selftest debug flag.'),
                     ListOption('starting-with', type=str, argname='TESTID',
                                param_name='starting_with', short_name='s',
                                help=
                                'Load only the tests starting with TESTID.'),
                     Option('sync',
                            help="By default we disable fsync and fdatasync"
                                 " while running the test suite.")
                     ]
    encoding_type = 'replace'

    def __init__(self):
        Command.__init__(self)
        self.additional_selftest_args = {}

    def run(self, testspecs_list=None, verbose=False, one=False,
            transport=None, benchmark=None,
            lsprof_timed=None,
            first=False, list_only=False,
            randomize=None, exclude=None, strict=False,
            load_list=None, debugflag=None, starting_with=None, subunit=False,
            parallel=None, lsprof_tests=False,
            sync=False):

        # During selftest, disallow proxying, as it can cause severe
        # performance penalties and is only needed for thread
        # safety. The selftest command is assumed to not use threads
        # too heavily. The call should be as early as possible, as
        # error reporting for past duplicate imports won't have useful
        # backtraces.
        lazy_import.disallow_proxying()

        from bzrlib import tests

        if testspecs_list is not None:
            pattern = '|'.join(testspecs_list)
        else:
            pattern = ".*"
        if subunit:
            try:
                from bzrlib.tests import SubUnitBzrRunner
            except ImportError:
                raise errors.BzrCommandError(gettext("subunit not available. subunit "
                    "needs to be installed to use --subunit."))
            self.additional_selftest_args['runner_class'] = SubUnitBzrRunner
            # On Windows, disable automatic conversion of '\n' to '\r\n' in
            # stdout, which would corrupt the subunit stream. 
            # FIXME: This has been fixed in subunit trunk (>0.0.5) so the
            # following code can be deleted when it's sufficiently deployed
            # -- vila/mgz 20100514
            if (sys.platform == "win32"
                and getattr(sys.stdout, 'fileno', None) is not None):
                import msvcrt
                msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
        if parallel:
            self.additional_selftest_args.setdefault(
                'suite_decorators', []).append(parallel)
        if benchmark:
            raise errors.BzrCommandError(gettext(
                "--benchmark is no longer supported from bzr 2.2; "
                "use bzr-usertest instead"))
        test_suite_factory = None
        if not exclude:
            exclude_pattern = None
        else:
            exclude_pattern = '(' + '|'.join(exclude) + ')'
        if not sync:
            self._disable_fsync()
        selftest_kwargs = {"verbose": verbose,
                          "pattern": pattern,
                          "stop_on_failure": one,
                          "transport": transport,
                          "test_suite_factory": test_suite_factory,
                          "lsprof_timed": lsprof_timed,
                          "lsprof_tests": lsprof_tests,
                          "matching_tests_first": first,
                          "list_only": list_only,
                          "random_seed": randomize,
                          "exclude_pattern": exclude_pattern,
                          "strict": strict,
                          "load_list": load_list,
                          "debug_flags": debugflag,
                          "starting_with": starting_with
                          }
        selftest_kwargs.update(self.additional_selftest_args)

        # Make deprecation warnings visible, unless -Werror is set
        cleanup = symbol_versioning.activate_deprecation_warnings(
            override=False)
        try:
            result = tests.selftest(**selftest_kwargs)
        finally:
            cleanup()
        return int(not result)

    def _disable_fsync(self):
        """Change the 'os' functionality to not synchronize."""
        self._orig_fsync = getattr(os, 'fsync', None)
        if self._orig_fsync is not None:
            os.fsync = lambda filedes: None
        self._orig_fdatasync = getattr(os, 'fdatasync', None)
        if self._orig_fdatasync is not None:
            os.fdatasync = lambda filedes: None


class cmd_version(Command):
    __doc__ = """Show version of bzr."""

    encoding_type = 'replace'
    takes_options = [
        Option("short", help="Print just the version number."),
        ]

    @display_command
    def run(self, short=False):
        from bzrlib.version import show_version
        if short:
            self.outf.write(bzrlib.version_string + '\n')
        else:
            show_version(to_file=self.outf)


class cmd_rocks(Command):
    __doc__ = """Statement of optimism."""

    hidden = True

    @display_command
    def run(self):
        self.outf.write(gettext("It sure does!\n"))


class cmd_find_merge_base(Command):
    __doc__ = """Find and print a base revision for merging two branches."""
    # TODO: Options to specify revisions on either side, as if
    #       merging only part of the history.
    takes_args = ['branch', 'other']
    hidden = True

    @display_command
    def run(self, branch, other):
        from bzrlib.revision import ensure_null

        branch1 = Branch.open_containing(branch)[0]
        branch2 = Branch.open_containing(other)[0]
        self.add_cleanup(branch1.lock_read().unlock)
        self.add_cleanup(branch2.lock_read().unlock)
        last1 = ensure_null(branch1.last_revision())
        last2 = ensure_null(branch2.last_revision())

        graph = branch1.repository.get_graph(branch2.repository)
        base_rev_id = graph.find_unique_lca(last1, last2)

        self.outf.write(gettext('merge base is revision %s\n') % base_rev_id)


class cmd_merge(Command):
    __doc__ = """Perform a three-way merge.

    The source of the merge can be specified either in the form of a branch,
    or in the form of a path to a file containing a merge directive generated
    with bzr send. If neither is specified, the default is the upstream branch
    or the branch most recently merged using --remember.  The source of the
    merge may also be specified in the form of a path to a file in another
    branch:  in this case, only the modifications to that file are merged into
    the current working tree.

    When merging from a branch, by default bzr will try to merge in all new
    work from the other branch, automatically determining an appropriate base
    revision.  If this fails, you may need to give an explicit base.

    To pick a different ending revision, pass "--revision OTHER".  bzr will
    try to merge in all new work up to and including revision OTHER.

    If you specify two values, "--revision BASE..OTHER", only revisions BASE
    through OTHER, excluding BASE but including OTHER, will be merged.  If this
    causes some revisions to be skipped, i.e. if the destination branch does
    not already contain revision BASE, such a merge is commonly referred to as
    a "cherrypick". Unlike a normal merge, Bazaar does not currently track
    cherrypicks. The changes look like a normal commit, and the history of the
    changes from the other branch is not stored in the commit.

    Revision numbers are always relative to the source branch.

    Merge will do its best to combine the changes in two branches, but there
    are some kinds of problems only a human can fix.  When it encounters those,
    it will mark a conflict.  A conflict means that you need to fix something,
    before you should commit.

    Use bzr resolve when you have fixed a problem.  See also bzr conflicts.

    If there is no default branch set, the first merge will set it (use
    --no-remember to avoid setting it). After that, you can omit the branch
    to use the default.  To change the default, use --remember. The value will
    only be saved if the remote location can be accessed.

    The results of the merge are placed into the destination working
    directory, where they can be reviewed (with bzr diff), tested, and then
    committed to record the result of the merge.

    merge refuses to run if there are any uncommitted changes, unless
    --force is given.  If --force is given, then the changes from the source 
    will be merged with the current working tree, including any uncommitted
    changes in the tree.  The --force option can also be used to create a
    merge revision which has more than two parents.

    If one would like to merge changes from the working tree of the other
    branch without merging any committed revisions, the --uncommitted option
    can be given.

    To select only some changes to merge, use "merge -i", which will prompt
    you to apply each diff hunk and file change, similar to "shelve".

    :Examples:
        To merge all new revisions from bzr.dev::

            bzr merge ../bzr.dev

        To merge changes up to and including revision 82 from bzr.dev::

            bzr merge -r 82 ../bzr.dev

        To merge the changes introduced by 82, without previous changes::

            bzr merge -r 81..82 ../bzr.dev

        To apply a merge directive contained in /tmp/merge::

            bzr merge /tmp/merge

        To create a merge revision with three parents from two branches
        feature1a and feature1b:

            bzr merge ../feature1a
            bzr merge ../feature1b --force
            bzr commit -m 'revision with three parents'
    """

    encoding_type = 'exact'
    _see_also = ['update', 'remerge', 'status-flags', 'send']
    takes_args = ['location?']
    takes_options = [
        'change',
        'revision',
        Option('force',
               help='Merge even if the destination tree has uncommitted changes.'),
        'merge-type',
        'reprocess',
        'remember',
        Option('show-base', help="Show base revision text in "
               "conflicts."),
        Option('uncommitted', help='Apply uncommitted changes'
               ' from a working copy, instead of branch changes.'),
        Option('pull', help='If the destination is already'
                ' completely merged into the source, pull from the'
                ' source rather than merging.  When this happens,'
                ' you do not need to commit the result.'),
        custom_help('directory',
               help='Branch to merge into, '
                    'rather than the one containing the working directory.'),
        Option('preview', help='Instead of merging, show a diff of the'
               ' merge.'),
        Option('interactive', help='Select changes interactively.',
            short_name='i')
    ]

    def run(self, location=None, revision=None, force=False,
            merge_type=None, show_base=False, reprocess=None, remember=None,
            uncommitted=False, pull=False,
            directory=None,
            preview=False,
            interactive=False,
            ):
        if merge_type is None:
            merge_type = _mod_merge.Merge3Merger

        if directory is None: directory = u'.'
        possible_transports = []
        merger = None
        allow_pending = True
        verified = 'inapplicable'

        tree = WorkingTree.open_containing(directory)[0]
        if tree.branch.revno() == 0:
            raise errors.BzrCommandError(gettext('Merging into empty branches not currently supported, '
                                         'https://bugs.launchpad.net/bzr/+bug/308562'))

        try:
            basis_tree = tree.revision_tree(tree.last_revision())
        except errors.NoSuchRevision:
            basis_tree = tree.basis_tree()

        # die as quickly as possible if there are uncommitted changes
        if not force:
            if tree.has_changes():
                raise errors.UncommittedChanges(tree)

        view_info = _get_view_info_for_change_reporter(tree)
        change_reporter = delta._ChangeReporter(
            unversioned_filter=tree.is_ignored, view_info=view_info)
        pb = ui.ui_factory.nested_progress_bar()
        self.add_cleanup(pb.finished)
        self.add_cleanup(tree.lock_write().unlock)
        if location is not None:
            try:
                mergeable = bundle.read_mergeable_from_url(location,
                    possible_transports=possible_transports)
            except errors.NotABundle:
                mergeable = None
            else:
                if uncommitted:
                    raise errors.BzrCommandError(gettext('Cannot use --uncommitted'
                        ' with bundles or merge directives.'))

                if revision is not None:
                    raise errors.BzrCommandError(gettext(
                        'Cannot use -r with merge directives or bundles'))
                merger, verified = _mod_merge.Merger.from_mergeable(tree,
                   mergeable, None)

        if merger is None and uncommitted:
            if revision is not None and len(revision) > 0:
                raise errors.BzrCommandError(gettext('Cannot use --uncommitted and'
                    ' --revision at the same time.'))
            merger = self.get_merger_from_uncommitted(tree, location, None)
            allow_pending = False

        if merger is None:
            merger, allow_pending = self._get_merger_from_branch(tree,
                location, revision, remember, possible_transports, None)

        merger.merge_type = merge_type
        merger.reprocess = reprocess
        merger.show_base = show_base
        self.sanity_check_merger(merger)
        if (merger.base_rev_id == merger.other_rev_id and
            merger.other_rev_id is not None):
            # check if location is a nonexistent file (and not a branch) to
            # disambiguate the 'Nothing to do'
            if merger.interesting_files:
                if not merger.other_tree.has_filename(
                    merger.interesting_files[0]):
                    note(gettext("merger: ") + str(merger))
                    raise errors.PathsDoNotExist([location])
            note(gettext('Nothing to do.'))
            return 0
        if pull and not preview:
            if merger.interesting_files is not None:
                raise errors.BzrCommandError(gettext('Cannot pull individual files'))
            if (merger.base_rev_id == tree.last_revision()):
                result = tree.pull(merger.other_branch, False,
                                   merger.other_rev_id)
                result.report(self.outf)
                return 0
        if merger.this_basis is None:
            raise errors.BzrCommandError(gettext(
                "This branch has no commits."
                " (perhaps you would prefer 'bzr pull')"))
        if preview:
            return self._do_preview(merger)
        elif interactive:
            return self._do_interactive(merger)
        else:
            return self._do_merge(merger, change_reporter, allow_pending,
                                  verified)

    def _get_preview(self, merger):
        tree_merger = merger.make_merger()
        tt = tree_merger.make_preview_transform()
        self.add_cleanup(tt.finalize)
        result_tree = tt.get_preview_tree()
        return result_tree

    def _do_preview(self, merger):
        from bzrlib.diff import show_diff_trees
        result_tree = self._get_preview(merger)
        path_encoding = osutils.get_diff_header_encoding()
        show_diff_trees(merger.this_tree, result_tree, self.outf,
                        old_label='', new_label='',
                        path_encoding=path_encoding)

    def _do_merge(self, merger, change_reporter, allow_pending, verified):
        merger.change_reporter = change_reporter
        conflict_count = merger.do_merge()
        if allow_pending:
            merger.set_pending()
        if verified == 'failed':
            warning('Preview patch does not match changes')
        if conflict_count != 0:
            return 1
        else:
            return 0

    def _do_interactive(self, merger):
        """Perform an interactive merge.

        This works by generating a preview tree of the merge, then using
        Shelver to selectively remove the differences between the working tree
        and the preview tree.
        """
        from bzrlib import shelf_ui
        result_tree = self._get_preview(merger)
        writer = bzrlib.option.diff_writer_registry.get()
        shelver = shelf_ui.Shelver(merger.this_tree, result_tree, destroy=True,
                                   reporter=shelf_ui.ApplyReporter(),
                                   diff_writer=writer(sys.stdout))
        try:
            shelver.run()
        finally:
            shelver.finalize()

    def sanity_check_merger(self, merger):
        if (merger.show_base and
            not merger.merge_type is _mod_merge.Merge3Merger):
            raise errors.BzrCommandError(gettext("Show-base is not supported for this"
                                         " merge type. %s") % merger.merge_type)
        if merger.reprocess is None:
            if merger.show_base:
                merger.reprocess = False
            else:
                # Use reprocess if the merger supports it
                merger.reprocess = merger.merge_type.supports_reprocess
        if merger.reprocess and not merger.merge_type.supports_reprocess:
            raise errors.BzrCommandError(gettext("Conflict reduction is not supported"
                                         " for merge type %s.") %
                                         merger.merge_type)
        if merger.reprocess and merger.show_base:
            raise errors.BzrCommandError(gettext("Cannot do conflict reduction and"
                                         " show base."))

    def _get_merger_from_branch(self, tree, location, revision, remember,
                                possible_transports, pb):
        """Produce a merger from a location, assuming it refers to a branch."""
        from bzrlib.tag import _merge_tags_if_possible
        # find the branch locations
        other_loc, user_location = self._select_branch_location(tree, location,
            revision, -1)
        if revision is not None and len(revision) == 2:
            base_loc, _unused = self._select_branch_location(tree,
                location, revision, 0)
        else:
            base_loc = other_loc
        # Open the branches
        other_branch, other_path = Branch.open_containing(other_loc,
            possible_transports)
        if base_loc == other_loc:
            base_branch = other_branch
        else:
            base_branch, base_path = Branch.open_containing(base_loc,
                possible_transports)
        # Find the revision ids
        other_revision_id = None
        base_revision_id = None
        if revision is not None:
            if len(revision) >= 1:
                other_revision_id = revision[-1].as_revision_id(other_branch)
            if len(revision) == 2:
                base_revision_id = revision[0].as_revision_id(base_branch)
        if other_revision_id is None:
            other_revision_id = _mod_revision.ensure_null(
                other_branch.last_revision())
        # Remember where we merge from. We need to remember if:
        # - user specify a location (and we don't merge from the parent
        #   branch)
        # - user ask to remember or there is no previous location set to merge
        #   from and user didn't ask to *not* remember
        if (user_location is not None
            and ((remember
                  or (remember is None
                      and tree.branch.get_submit_branch() is None)))):
            tree.branch.set_submit_branch(other_branch.base)
        # Merge tags (but don't set them in the master branch yet, the user
        # might revert this merge).  Commit will propagate them.
        _merge_tags_if_possible(other_branch, tree.branch, ignore_master=True)
        merger = _mod_merge.Merger.from_revision_ids(pb, tree,
            other_revision_id, base_revision_id, other_branch, base_branch)
        if other_path != '':
            allow_pending = False
            merger.interesting_files = [other_path]
        else:
            allow_pending = True
        return merger, allow_pending

    def get_merger_from_uncommitted(self, tree, location, pb):
        """Get a merger for uncommitted changes.

        :param tree: The tree the merger should apply to.
        :param location: The location containing uncommitted changes.
        :param pb: The progress bar to use for showing progress.
        """
        location = self._select_branch_location(tree, location)[0]
        other_tree, other_path = WorkingTree.open_containing(location)
        merger = _mod_merge.Merger.from_uncommitted(tree, other_tree, pb)
        if other_path != '':
            merger.interesting_files = [other_path]
        return merger

    def _select_branch_location(self, tree, user_location, revision=None,
                                index=None):
        """Select a branch location, according to possible inputs.

        If provided, branches from ``revision`` are preferred.  (Both
        ``revision`` and ``index`` must be supplied.)

        Otherwise, the ``location`` parameter is used.  If it is None, then the
        ``submit`` or ``parent`` location is used, and a note is printed.

        :param tree: The working tree to select a branch for merging into
        :param location: The location entered by the user
        :param revision: The revision parameter to the command
        :param index: The index to use for the revision parameter.  Negative
            indices are permitted.
        :return: (selected_location, user_location).  The default location
            will be the user-entered location.
        """
        if (revision is not None and index is not None
            and revision[index] is not None):
            branch = revision[index].get_branch()
            if branch is not None:
                return branch, branch
        if user_location is None:
            location = self._get_remembered(tree, 'Merging from')
        else:
            location = user_location
        return location, user_location

    def _get_remembered(self, tree, verb_string):
        """Use tree.branch's parent if none was supplied.

        Report if the remembered location was used.
        """
        stored_location = tree.branch.get_submit_branch()
        stored_location_type = "submit"
        if stored_location is None:
            stored_location = tree.branch.get_parent()
            stored_location_type = "parent"
        mutter("%s", stored_location)
        if stored_location is None:
            raise errors.BzrCommandError(gettext("No location specified or remembered"))
        display_url = urlutils.unescape_for_display(stored_location, 'utf-8')
        note(gettext("{0} remembered {1} location {2}").format(verb_string,
                stored_location_type, display_url))
        return stored_location


class cmd_remerge(Command):
    __doc__ = """Redo a merge.

    Use this if you want to try a different merge technique while resolving
    conflicts.  Some merge techniques are better than others, and remerge
    lets you try different ones on different files.

    The options for remerge have the same meaning and defaults as the ones for
    merge.  The difference is that remerge can (only) be run when there is a
    pending merge, and it lets you specify particular files.

    :Examples:
        Re-do the merge of all conflicted files, and show the base text in
        conflict regions, in addition to the usual THIS and OTHER texts::

            bzr remerge --show-base

        Re-do the merge of "foobar", using the weave merge algorithm, with
        additional processing to reduce the size of conflict regions::

            bzr remerge --merge-type weave --reprocess foobar
    """
    takes_args = ['file*']
    takes_options = [
            'merge-type',
            'reprocess',
            Option('show-base',
                   help="Show base revision text in conflicts."),
            ]

    def run(self, file_list=None, merge_type=None, show_base=False,
            reprocess=False):
        from bzrlib.conflicts import restore
        if merge_type is None:
            merge_type = _mod_merge.Merge3Merger
        tree, file_list = WorkingTree.open_containing_paths(file_list)
        self.add_cleanup(tree.lock_write().unlock)
        parents = tree.get_parent_ids()
        if len(parents) != 2:
            raise errors.BzrCommandError(gettext("Sorry, remerge only works after normal"
                                         " merges.  Not cherrypicking or"
                                         " multi-merges."))
        repository = tree.branch.repository
        interesting_ids = None
        new_conflicts = []
        conflicts = tree.conflicts()
        if file_list is not None:
            interesting_ids = set()
            for filename in file_list:
                file_id = tree.path2id(filename)
                if file_id is None:
                    raise errors.NotVersionedError(filename)
                interesting_ids.add(file_id)
                if tree.kind(file_id) != "directory":
                    continue

                for name, ie in tree.inventory.iter_entries(file_id):
                    interesting_ids.add(ie.file_id)
            new_conflicts = conflicts.select_conflicts(tree, file_list)[0]
        else:
            # Remerge only supports resolving contents conflicts
            allowed_conflicts = ('text conflict', 'contents conflict')
            restore_files = [c.path for c in conflicts
                             if c.typestring in allowed_conflicts]
        _mod_merge.transform_tree(tree, tree.basis_tree(), interesting_ids)
        tree.set_conflicts(ConflictList(new_conflicts))
        if file_list is not None:
            restore_files = file_list
        for filename in restore_files:
            try:
                restore(tree.abspath(filename))
            except errors.NotConflicted:
                pass
        # Disable pending merges, because the file texts we are remerging
        # have not had those merges performed.  If we use the wrong parents
        # list, we imply that the working tree text has seen and rejected
        # all the changes from the other tree, when in fact those changes
        # have not yet been seen.
        tree.set_parent_ids(parents[:1])
        try:
            merger = _mod_merge.Merger.from_revision_ids(None, tree, parents[1])
            merger.interesting_ids = interesting_ids
            merger.merge_type = merge_type
            merger.show_base = show_base
            merger.reprocess = reprocess
            conflicts = merger.do_merge()
        finally:
            tree.set_parent_ids(parents)
        if conflicts > 0:
            return 1
        else:
            return 0


class cmd_revert(Command):
    __doc__ = """Revert files to a previous revision.

    Giving a list of files will revert only those files.  Otherwise, all files
    will be reverted.  If the revision is not specified with '--revision', the
    last committed revision is used.

    To remove only some changes, without reverting to a prior version, use
    merge instead.  For example, "merge . -r -2..-3" (don't forget the ".")
    will remove the changes introduced by the second last commit (-2), without
    affecting the changes introduced by the last commit (-1).  To remove
    certain changes on a hunk-by-hunk basis, see the shelve command.

    By default, any files that have been manually changed will be backed up
    first.  (Files changed only by merge are not backed up.)  Backup files have
    '.~#~' appended to their name, where # is a number.

    When you provide files, you can use their current pathname or the pathname
    from the target revision.  So you can use revert to "undelete" a file by
    name.  If you name a directory, all the contents of that directory will be
    reverted.

    If you have newly added files since the target revision, they will be
    removed.  If the files to be removed have been changed, backups will be
    created as above.  Directories containing unknown files will not be
    deleted.

    The working tree contains a list of revisions that have been merged but
    not yet committed. These revisions will be included as additional parents
    of the next commit.  Normally, using revert clears that list as well as
    reverting the files.  If any files are specified, revert leaves the list
    of uncommitted merges alone and reverts only the files.  Use ``bzr revert
    .`` in the tree root to revert all files but keep the recorded merges,
    and ``bzr revert --forget-merges`` to clear the pending merge list without
    reverting any files.

    Using "bzr revert --forget-merges", it is possible to apply all of the
    changes from a branch in a single revision.  To do this, perform the merge
    as desired.  Then doing revert with the "--forget-merges" option will keep
    the content of the tree as it was, but it will clear the list of pending
    merges.  The next commit will then contain all of the changes that are
    present in the other branch, but without any other parent revisions.
    Because this technique forgets where these changes originated, it may
    cause additional conflicts on later merges involving the same source and
    target branches.
    """

    _see_also = ['cat', 'export', 'merge', 'shelve']
    takes_options = [
        'revision',
        Option('no-backup', "Do not save backups of reverted files."),
        Option('forget-merges',
               'Remove pending merge marker, without changing any files.'),
        ]
    takes_args = ['file*']

    def run(self, revision=None, no_backup=False, file_list=None,
            forget_merges=None):
        tree, file_list = WorkingTree.open_containing_paths(file_list)
        self.add_cleanup(tree.lock_tree_write().unlock)
        if forget_merges:
            tree.set_parent_ids(tree.get_parent_ids()[:1])
        else:
            self._revert_tree_to_revision(tree, revision, file_list, no_backup)

    @staticmethod
    def _revert_tree_to_revision(tree, revision, file_list, no_backup):
        rev_tree = _get_one_revision_tree('revert', revision, tree=tree)
        tree.revert(file_list, rev_tree, not no_backup, None,
            report_changes=True)


class cmd_assert_fail(Command):
    __doc__ = """Test reporting of assertion failures"""
    # intended just for use in testing

    hidden = True

    def run(self):
        raise AssertionError("always fails")


class cmd_help(Command):
    __doc__ = """Show help on a command or other topic.
    """

    _see_also = ['topics']
    takes_options = [
            Option('long', 'Show help on all commands.'),
            ]
    takes_args = ['topic?']
    aliases = ['?', '--help', '-?', '-h']

    @display_command
    def run(self, topic=None, long=False):
        import bzrlib.help
        if topic is None and long:
            topic = "commands"
        bzrlib.help.help(topic)


class cmd_shell_complete(Command):
    __doc__ = """Show appropriate completions for context.

    For a list of all available commands, say 'bzr shell-complete'.
    """
    takes_args = ['context?']
    aliases = ['s-c']
    hidden = True

    @display_command
    def run(self, context=None):
        from bzrlib import shellcomplete
        shellcomplete.shellcomplete(context)


class cmd_missing(Command):
    __doc__ = """Show unmerged/unpulled revisions between two branches.

    OTHER_BRANCH may be local or remote.

    To filter on a range of revisions, you can use the command -r begin..end
    -r revision requests a specific revision, -r ..end or -r begin.. are
    also valid.
            
    :Exit values:
        1 - some missing revisions
        0 - no missing revisions

    :Examples:

        Determine the missing revisions between this and the branch at the
        remembered pull location::

            bzr missing

        Determine the missing revisions between this and another branch::

            bzr missing http://server/branch

        Determine the missing revisions up to a specific revision on the other
        branch::

            bzr missing -r ..-10

        Determine the missing revisions up to a specific revision on this
        branch::

            bzr missing --my-revision ..-10
    """

    _see_also = ['merge', 'pull']
    takes_args = ['other_branch?']
    takes_options = [
        'directory',
        Option('reverse', 'Reverse the order of revisions.'),
        Option('mine-only',
               'Display changes in the local branch only.'),
        Option('this' , 'Same as --mine-only.'),
        Option('theirs-only',
               'Display changes in the remote branch only.'),
        Option('other', 'Same as --theirs-only.'),
        'log-format',
        'show-ids',
        'verbose',
        custom_help('revision',
             help='Filter on other branch revisions (inclusive). '
                'See "help revisionspec" for details.'),
        Option('my-revision',
            type=_parse_revision_str,
            help='Filter on local branch revisions (inclusive). '
                'See "help revisionspec" for details.'),
        Option('include-merged',
               'Show all revisions in addition to the mainline ones.'),
        Option('include-merges', hidden=True,
               help='Historical alias for --include-merged.'),
        ]
    encoding_type = 'replace'

    @display_command
    def run(self, other_branch=None, reverse=False, mine_only=False,
            theirs_only=False,
            log_format=None, long=False, short=False, line=False,
            show_ids=False, verbose=False, this=False, other=False,
            include_merged=None, revision=None, my_revision=None,
            directory=u'.',
            include_merges=symbol_versioning.DEPRECATED_PARAMETER):
        from bzrlib.missing import find_unmerged, iter_log_revisions
        def message(s):
            if not is_quiet():
                self.outf.write(s)

        if symbol_versioning.deprecated_passed(include_merges):
            ui.ui_factory.show_user_warning(
                'deprecated_command_option',
                deprecated_name='--include-merges',
                recommended_name='--include-merged',
                deprecated_in_version='2.5',
                command=self.invoked_as)
            if include_merged is None:
                include_merged = include_merges
            else:
                raise errors.BzrCommandError(gettext(
                    '{0} and {1} are mutually exclusive').format(
                    '--include-merges', '--include-merged'))
        if include_merged is None:
            include_merged = False
        if this:
            mine_only = this
        if other:
            theirs_only = other
        # TODO: We should probably check that we don't have mine-only and
        #       theirs-only set, but it gets complicated because we also have
        #       this and other which could be used.
        restrict = 'all'
        if mine_only:
            restrict = 'local'
        elif theirs_only:
            restrict = 'remote'

        local_branch = Branch.open_containing(directory)[0]
        self.add_cleanup(local_branch.lock_read().unlock)

        parent = local_branch.get_parent()
        if other_branch is None:
            other_branch = parent
            if other_branch is None:
                raise errors.BzrCommandError(gettext("No peer location known"
                                             " or specified."))
            display_url = urlutils.unescape_for_display(parent,
                                                        self.outf.encoding)
            message(gettext("Using saved parent location: {0}\n").format(
                    display_url))

        remote_branch = Branch.open(other_branch)
        if remote_branch.base == local_branch.base:
            remote_branch = local_branch
        else:
            self.add_cleanup(remote_branch.lock_read().unlock)

        local_revid_range = _revision_range_to_revid_range(
            _get_revision_range(my_revision, local_branch,
                self.name()))

        remote_revid_range = _revision_range_to_revid_range(
            _get_revision_range(revision,
                remote_branch, self.name()))

        local_extra, remote_extra = find_unmerged(
            local_branch, remote_branch, restrict,
            backward=not reverse,
            include_merged=include_merged,
            local_revid_range=local_revid_range,
            remote_revid_range=remote_revid_range)

        if log_format is None:
            registry = log.log_formatter_registry
            log_format = registry.get_default(local_branch)
        lf = log_format(to_file=self.outf,
                        show_ids=show_ids,
                        show_timezone='original')

        status_code = 0
        if local_extra and not theirs_only:
            message(ngettext("You have %d extra revision:\n",
                             "You have %d extra revisions:\n", 
                             len(local_extra)) %
                len(local_extra))
            for revision in iter_log_revisions(local_extra,
                                local_branch.repository,
                                verbose):
                lf.log_revision(revision)
            printed_local = True
            status_code = 1
        else:
            printed_local = False

        if remote_extra and not mine_only:
            if printed_local is True:
                message("\n\n\n")
            message(ngettext("You are missing %d revision:\n",
                             "You are missing %d revisions:\n",
                             len(remote_extra)) %
                len(remote_extra))
            for revision in iter_log_revisions(remote_extra,
                                remote_branch.repository,
                                verbose):
                lf.log_revision(revision)
            status_code = 1

        if mine_only and not local_extra:
            # We checked local, and found nothing extra
            message(gettext('This branch has no new revisions.\n'))
        elif theirs_only and not remote_extra:
            # We checked remote, and found nothing extra
            message(gettext('Other branch has no new revisions.\n'))
        elif not (mine_only or theirs_only or local_extra or
                  remote_extra):
            # We checked both branches, and neither one had extra
            # revisions
            message(gettext("Branches are up to date.\n"))
        self.cleanup_now()
        if not status_code and parent is None and other_branch is not None:
            self.add_cleanup(local_branch.lock_write().unlock)
            # handle race conditions - a parent might be set while we run.
            if local_branch.get_parent() is None:
                local_branch.set_parent(remote_branch.base)
        return status_code


class cmd_pack(Command):
    __doc__ = """Compress the data within a repository.

    This operation compresses the data within a bazaar repository. As
    bazaar supports automatic packing of repository, this operation is
    normally not required to be done manually.

    During the pack operation, bazaar takes a backup of existing repository
    data, i.e. pack files. This backup is eventually removed by bazaar
    automatically when it is safe to do so. To save disk space by removing
    the backed up pack files, the --clean-obsolete-packs option may be
    used.

    Warning: If you use --clean-obsolete-packs and your machine crashes
    during or immediately after repacking, you may be left with a state
    where the deletion has been written to disk but the new packs have not
    been. In this case the repository may be unusable.
    """

    _see_also = ['repositories']
    takes_args = ['branch_or_repo?']
    takes_options = [
        Option('clean-obsolete-packs', 'Delete obsolete packs to save disk space.'),
        ]

    def run(self, branch_or_repo='.', clean_obsolete_packs=False):
        dir = controldir.ControlDir.open_containing(branch_or_repo)[0]
        try:
            branch = dir.open_branch()
            repository = branch.repository
        except errors.NotBranchError:
            repository = dir.open_repository()
        repository.pack(clean_obsolete_packs=clean_obsolete_packs)


class cmd_plugins(Command):
    __doc__ = """List the installed plugins.

    This command displays the list of installed plugins including
    version of plugin and a short description of each.

    --verbose shows the path where each plugin is located.

    A plugin is an external component for Bazaar that extends the
    revision control system, by adding or replacing code in Bazaar.
    Plugins can do a variety of things, including overriding commands,
    adding new commands, providing additional network transports and
    customizing log output.

    See the Bazaar Plugin Guide <http://doc.bazaar.canonical.com/plugins/en/>
    for further information on plugins including where to find them and how to
    install them. Instructions are also provided there on how to write new
    plugins using the Python programming language.
    """
    takes_options = ['verbose']

    @display_command
    def run(self, verbose=False):
        from bzrlib import plugin
        # Don't give writelines a generator as some codecs don't like that
        self.outf.writelines(
            list(plugin.describe_plugins(show_paths=verbose)))


class cmd_testament(Command):
    __doc__ = """Show testament (signing-form) of a revision."""
    takes_options = [
            'revision',
            Option('long', help='Produce long-format testament.'),
            Option('strict',
                   help='Produce a strict-format testament.')]
    takes_args = ['branch?']
    @display_command
    def run(self, branch=u'.', revision=None, long=False, strict=False):
        from bzrlib.testament import Testament, StrictTestament
        if strict is True:
            testament_class = StrictTestament
        else:
            testament_class = Testament
        if branch == '.':
            b = Branch.open_containing(branch)[0]
        else:
            b = Branch.open(branch)
        self.add_cleanup(b.lock_read().unlock)
        if revision is None:
            rev_id = b.last_revision()
        else:
            rev_id = revision[0].as_revision_id(b)
        t = testament_class.from_revision(b.repository, rev_id)
        if long:
            sys.stdout.writelines(t.as_text_lines())
        else:
            sys.stdout.write(t.as_short_text())


class cmd_annotate(Command):
    __doc__ = """Show the origin of each line in a file.

    This prints out the given file with an annotation on the left side
    indicating which revision, author and date introduced the change.

    If the origin is the same for a run of consecutive lines, it is
    shown only at the top, unless the --all option is given.
    """
    # TODO: annotate directories; showing when each file was last changed
    # TODO: if the working copy is modified, show annotations on that
    #       with new uncommitted lines marked
    aliases = ['ann', 'blame', 'praise']
    takes_args = ['filename']
    takes_options = [Option('all', help='Show annotations on all lines.'),
                     Option('long', help='Show commit date in annotations.'),
                     'revision',
                     'show-ids',
                     'directory',
                     ]
    encoding_type = 'exact'

    @display_command
    def run(self, filename, all=False, long=False, revision=None,
            show_ids=False, directory=None):
        from bzrlib.annotate import (
            annotate_file_tree,
            )
        wt, branch, relpath = \
            _open_directory_or_containing_tree_or_branch(filename, directory)
        if wt is not None:
            self.add_cleanup(wt.lock_read().unlock)
        else:
            self.add_cleanup(branch.lock_read().unlock)
        tree = _get_one_revision_tree('annotate', revision, branch=branch)
        self.add_cleanup(tree.lock_read().unlock)
        if wt is not None and revision is None:
            file_id = wt.path2id(relpath)
        else:
            file_id = tree.path2id(relpath)
        if file_id is None:
            raise errors.NotVersionedError(filename)
        if wt is not None and revision is None:
            # If there is a tree and we're not annotating historical
            # versions, annotate the working tree's content.
            annotate_file_tree(wt, file_id, self.outf, long, all,
                show_ids=show_ids)
        else:
            annotate_file_tree(tree, file_id, self.outf, long, all,
                show_ids=show_ids, branch=branch)


class cmd_re_sign(Command):
    __doc__ = """Create a digital signature for an existing revision."""
    # TODO be able to replace existing ones.

    hidden = True # is this right ?
    takes_args = ['revision_id*']
    takes_options = ['directory', 'revision']

    def run(self, revision_id_list=None, revision=None, directory=u'.'):
        if revision_id_list is not None and revision is not None:
            raise errors.BzrCommandError(gettext('You can only supply one of revision_id or --revision'))
        if revision_id_list is None and revision is None:
            raise errors.BzrCommandError(gettext('You must supply either --revision or a revision_id'))
        b = WorkingTree.open_containing(directory)[0].branch
        self.add_cleanup(b.lock_write().unlock)
        return self._run(b, revision_id_list, revision)

    def _run(self, b, revision_id_list, revision):
        import bzrlib.gpg as gpg
        gpg_strategy = gpg.GPGStrategy(b.get_config_stack())
        if revision_id_list is not None:
            b.repository.start_write_group()
            try:
                for revision_id in revision_id_list:
                    b.repository.sign_revision(revision_id, gpg_strategy)
            except:
                b.repository.abort_write_group()
                raise
            else:
                b.repository.commit_write_group()
        elif revision is not None:
            if len(revision) == 1:
                revno, rev_id = revision[0].in_history(b)
                b.repository.start_write_group()
                try:
                    b.repository.sign_revision(rev_id, gpg_strategy)
                except:
                    b.repository.abort_write_group()
                    raise
                else:
                    b.repository.commit_write_group()
            elif len(revision) == 2:
                # are they both on rh- if so we can walk between them
                # might be nice to have a range helper for arbitrary
                # revision paths. hmm.
                from_revno, from_revid = revision[0].in_history(b)
                to_revno, to_revid = revision[1].in_history(b)
                if to_revid is None:
                    to_revno = b.revno()
                if from_revno is None or to_revno is None:
                    raise errors.BzrCommandError(gettext('Cannot sign a range of non-revision-history revisions'))
                b.repository.start_write_group()
                try:
                    for revno in range(from_revno, to_revno + 1):
                        b.repository.sign_revision(b.get_rev_id(revno),
                                                   gpg_strategy)
                except:
                    b.repository.abort_write_group()
                    raise
                else:
                    b.repository.commit_write_group()
            else:
                raise errors.BzrCommandError(gettext('Please supply either one revision, or a range.'))


class cmd_bind(Command):
    __doc__ = """Convert the current branch into a checkout of the supplied branch.
    If no branch is supplied, rebind to the last bound location.

    Once converted into a checkout, commits must succeed on the master branch
    before they will be applied to the local branch.

    Bound branches use the nickname of its master branch unless it is set
    locally, in which case binding will update the local nickname to be
    that of the master.
    """

    _see_also = ['checkouts', 'unbind']
    takes_args = ['location?']
    takes_options = ['directory']

    def run(self, location=None, directory=u'.'):
        b, relpath = Branch.open_containing(directory)
        if location is None:
            try:
                location = b.get_old_bound_location()
            except errors.UpgradeRequired:
                raise errors.BzrCommandError(gettext('No location supplied.  '
                    'This format does not remember old locations.'))
            else:
                if location is None:
                    if b.get_bound_location() is not None:
                        raise errors.BzrCommandError(gettext('Branch is already bound'))
                    else:
                        raise errors.BzrCommandError(gettext('No location supplied '
                            'and no previous location known'))
        b_other = Branch.open(location)
        try:
            b.bind(b_other)
        except errors.DivergedBranches:
            raise errors.BzrCommandError(gettext('These branches have diverged.'
                                         ' Try merging, and then bind again.'))
        if b.get_config().has_explicit_nickname():
            b.nick = b_other.nick


class cmd_unbind(Command):
    __doc__ = """Convert the current checkout into a regular branch.

    After unbinding, the local branch is considered independent and subsequent
    commits will be local only.
    """

    _see_also = ['checkouts', 'bind']
    takes_args = []
    takes_options = ['directory']

    def run(self, directory=u'.'):
        b, relpath = Branch.open_containing(directory)
        if not b.unbind():
            raise errors.BzrCommandError(gettext('Local branch is not bound'))


class cmd_uncommit(Command):
    __doc__ = """Remove the last committed revision.

    --verbose will print out what is being removed.
    --dry-run will go through all the motions, but not actually
    remove anything.

    If --revision is specified, uncommit revisions to leave the branch at the
    specified revision.  For example, "bzr uncommit -r 15" will leave the
    branch at revision 15.

    Uncommit leaves the working tree ready for a new commit.  The only change
    it may make is to restore any pending merges that were present before
    the commit.
    """

    # TODO: jam 20060108 Add an option to allow uncommit to remove
    # unreferenced information in 'branch-as-repository' branches.
    # TODO: jam 20060108 Add the ability for uncommit to remove unreferenced
    # information in shared branches as well.
    _see_also = ['commit']
    takes_options = ['verbose', 'revision',
                    Option('dry-run', help='Don\'t actually make changes.'),
                    Option('force', help='Say yes to all questions.'),
                    Option('keep-tags',
                           help='Keep tags that point to removed revisions.'),
                    Option('local',
                           help="Only remove the commits from the local branch"
                                " when in a checkout."
                           ),
                    ]
    takes_args = ['location?']
    aliases = []
    encoding_type = 'replace'

    def run(self, location=None, dry_run=False, verbose=False,
            revision=None, force=False, local=False, keep_tags=False):
        if location is None:
            location = u'.'
        control, relpath = controldir.ControlDir.open_containing(location)
        try:
            tree = control.open_workingtree()
            b = tree.branch
        except (errors.NoWorkingTree, errors.NotLocalUrl):
            tree = None
            b = control.open_branch()

        if tree is not None:
            self.add_cleanup(tree.lock_write().unlock)
        else:
            self.add_cleanup(b.lock_write().unlock)
        return self._run(b, tree, dry_run, verbose, revision, force,
                         local, keep_tags)

    def _run(self, b, tree, dry_run, verbose, revision, force, local,
             keep_tags):
        from bzrlib.log import log_formatter, show_log
        from bzrlib.uncommit import uncommit

        last_revno, last_rev_id = b.last_revision_info()

        rev_id = None
        if revision is None:
            revno = last_revno
            rev_id = last_rev_id
        else:
            # 'bzr uncommit -r 10' actually means uncommit
            # so that the final tree is at revno 10.
            # but bzrlib.uncommit.uncommit() actually uncommits
            # the revisions that are supplied.
            # So we need to offset it by one
            revno = revision[0].in_history(b).revno + 1
            if revno <= last_revno:
                rev_id = b.get_rev_id(revno)

        if rev_id is None or _mod_revision.is_null(rev_id):
            self.outf.write(gettext('No revisions to uncommit.\n'))
            return 1

        lf = log_formatter('short',
                           to_file=self.outf,
                           show_timezone='original')

        show_log(b,
                 lf,
                 verbose=False,
                 direction='forward',
                 start_revision=revno,
                 end_revision=last_revno)

        if dry_run:
            self.outf.write(gettext('Dry-run, pretending to remove'
                            ' the above revisions.\n'))
        else:
            self.outf.write(gettext('The above revision(s) will be removed.\n'))

        if not force:
            if not ui.ui_factory.confirm_action(
                    gettext(u'Uncommit these revisions'),
                    'bzrlib.builtins.uncommit',
                    {}):
                self.outf.write(gettext('Canceled\n'))
                return 0

        mutter('Uncommitting from {%s} to {%s}',
               last_rev_id, rev_id)
        uncommit(b, tree=tree, dry_run=dry_run, verbose=verbose,
                 revno=revno, local=local, keep_tags=keep_tags)
        self.outf.write(gettext('You can restore the old tip by running:\n'
             '  bzr pull . -r revid:%s\n') % last_rev_id)


class cmd_break_lock(Command):
    __doc__ = """Break a dead lock.

    This command breaks a lock on a repository, branch, working directory or
    config file.

    CAUTION: Locks should only be broken when you are sure that the process
    holding the lock has been stopped.

    You can get information on what locks are open via the 'bzr info
    [location]' command.

    :Examples:
        bzr break-lock
        bzr break-lock bzr+ssh://example.com/bzr/foo
        bzr break-lock --conf ~/.bazaar
    """

    takes_args = ['location?']
    takes_options = [
        Option('config',
               help='LOCATION is the directory where the config lock is.'),
        Option('force',
            help='Do not ask for confirmation before breaking the lock.'),
        ]

    def run(self, location=None, config=False, force=False):
        if location is None:
            location = u'.'
        if force:
            ui.ui_factory = ui.ConfirmationUserInterfacePolicy(ui.ui_factory,
                None,
                {'bzrlib.lockdir.break': True})
        if config:
            conf = _mod_config.LockableConfig(file_name=location)
            conf.break_lock()
        else:
            control, relpath = controldir.ControlDir.open_containing(location)
            try:
                control.break_lock()
            except NotImplementedError:
                pass


class cmd_wait_until_signalled(Command):
    __doc__ = """Test helper for test_start_and_stop_bzr_subprocess_send_signal.

    This just prints a line to signal when it is ready, then blocks on stdin.
    """

    hidden = True

    def run(self):
        sys.stdout.write("running\n")
        sys.stdout.flush()
        sys.stdin.readline()


class cmd_serve(Command):
    __doc__ = """Run the bzr server."""

    aliases = ['server']

    takes_options = [
        Option('inet',
               help='Serve on stdin/out for use from inetd or sshd.'),
        RegistryOption('protocol',
               help="Protocol to serve.",
               lazy_registry=('bzrlib.transport', 'transport_server_registry'),
               value_switches=True),
        Option('port',
               help='Listen for connections on nominated port of the form '
                    '[hostname:]portnumber.  Passing 0 as the port number will '
                    'result in a dynamically allocated port.  The default port '
                    'depends on the protocol.',
               type=str),
        custom_help('directory',
               help='Serve contents of this directory.'),
        Option('allow-writes',
               help='By default the server is a readonly server.  Supplying '
                    '--allow-writes enables write access to the contents of '
                    'the served directory and below.  Note that ``bzr serve`` '
                    'does not perform authentication, so unless some form of '
                    'external authentication is arranged supplying this '
                    'option leads to global uncontrolled write access to your '
                    'file system.'
                ),
        Option('client-timeout', type=float,
               help='Override the default idle client timeout (5min).'),
        ]

    def get_host_and_port(self, port):
        """Return the host and port to run the smart server on.

        If 'port' is None, None will be returned for the host and port.

        If 'port' has a colon in it, the string before the colon will be
        interpreted as the host.

        :param port: A string of the port to run the server on.
        :return: A tuple of (host, port), where 'host' is a host name or IP,
            and port is an integer TCP/IP port.
        """
        host = None
        if port is not None:
            if ':' in port:
                host, port = port.split(':')
            port = int(port)
        return host, port

    def run(self, port=None, inet=False, directory=None, allow_writes=False,
            protocol=None, client_timeout=None):
        from bzrlib import transport
        if directory is None:
            directory = os.getcwd()
        if protocol is None:
            protocol = transport.transport_server_registry.get()
        host, port = self.get_host_and_port(port)
        url = transport.location_to_url(directory)
        if not allow_writes:
            url = 'readonly+' + url
        t = transport.get_transport_from_url(url)
        try:
            protocol(t, host, port, inet, client_timeout)
        except TypeError, e:
            # We use symbol_versioning.deprecated_in just so that people
            # grepping can find it here.
            # symbol_versioning.deprecated_in((2, 5, 0))
            symbol_versioning.warn(
                'Got TypeError(%s)\ntrying to call protocol: %s.%s\n'
                'Most likely it needs to be updated to support a'
                ' "timeout" parameter (added in bzr 2.5.0)'
                % (e, protocol.__module__, protocol),
                DeprecationWarning)
            protocol(t, host, port, inet)


class cmd_join(Command):
    __doc__ = """Combine a tree into its containing tree.

    This command requires the target tree to be in a rich-root format.

    The TREE argument should be an independent tree, inside another tree, but
    not part of it.  (Such trees can be produced by "bzr split", but also by
    running "bzr branch" with the target inside a tree.)

    The result is a combined tree, with the subtree no longer an independent
    part.  This is marked as a merge of the subtree into the containing tree,
    and all history is preserved.
    """

    _see_also = ['split']
    takes_args = ['tree']
    takes_options = [
            Option('reference', help='Join by reference.', hidden=True),
            ]

    def run(self, tree, reference=False):
        sub_tree = WorkingTree.open(tree)
        parent_dir = osutils.dirname(sub_tree.basedir)
        containing_tree = WorkingTree.open_containing(parent_dir)[0]
        repo = containing_tree.branch.repository
        if not repo.supports_rich_root():
            raise errors.BzrCommandError(gettext(
                "Can't join trees because %s doesn't support rich root data.\n"
                "You can use bzr upgrade on the repository.")
                % (repo,))
        if reference:
            try:
                containing_tree.add_reference(sub_tree)
            except errors.BadReferenceTarget, e:
                # XXX: Would be better to just raise a nicely printable
                # exception from the real origin.  Also below.  mbp 20070306
                raise errors.BzrCommandError(
                       gettext("Cannot join {0}.  {1}").format(tree, e.reason))
        else:
            try:
                containing_tree.subsume(sub_tree)
            except errors.BadSubsumeSource, e:
                raise errors.BzrCommandError(
                       gettext("Cannot join {0}.  {1}").format(tree, e.reason))


class cmd_split(Command):
    __doc__ = """Split a subdirectory of a tree into a separate tree.

    This command will produce a target tree in a format that supports
    rich roots, like 'rich-root' or 'rich-root-pack'.  These formats cannot be
    converted into earlier formats like 'dirstate-tags'.

    The TREE argument should be a subdirectory of a working tree.  That
    subdirectory will be converted into an independent tree, with its own
    branch.  Commits in the top-level tree will not apply to the new subtree.
    """

    _see_also = ['join']
    takes_args = ['tree']

    def run(self, tree):
        containing_tree, subdir = WorkingTree.open_containing(tree)
        sub_id = containing_tree.path2id(subdir)
        if sub_id is None:
            raise errors.NotVersionedError(subdir)
        try:
            containing_tree.extract(sub_id)
        except errors.RootNotRich:
            raise errors.RichRootUpgradeRequired(containing_tree.branch.base)


class cmd_merge_directive(Command):
    __doc__ = """Generate a merge directive for auto-merge tools.

    A directive requests a merge to be performed, and also provides all the
    information necessary to do so.  This means it must either include a
    revision bundle, or the location of a branch containing the desired
    revision.

    A submit branch (the location to merge into) must be supplied the first
    time the command is issued.  After it has been supplied once, it will
    be remembered as the default.

    A public branch is optional if a revision bundle is supplied, but required
    if --diff or --plain is specified.  It will be remembered as the default
    after the first use.
    """

    takes_args = ['submit_branch?', 'public_branch?']

    hidden = True

    _see_also = ['send']

    takes_options = [
        'directory',
        RegistryOption.from_kwargs('patch-type',
            'The type of patch to include in the directive.',
            title='Patch type',
            value_switches=True,
            enum_switch=False,
            bundle='Bazaar revision bundle (default).',
            diff='Normal unified diff.',
            plain='No patch, just directive.'),
        Option('sign', help='GPG-sign the directive.'), 'revision',
        Option('mail-to', type=str,
            help='Instead of printing the directive, email to this address.'),
        Option('message', type=str, short_name='m',
            help='Message to use when committing this merge.')
        ]

    encoding_type = 'exact'

    def run(self, submit_branch=None, public_branch=None, patch_type='bundle',
            sign=False, revision=None, mail_to=None, message=None,
            directory=u'.'):
        from bzrlib.revision import ensure_null, NULL_REVISION
        include_patch, include_bundle = {
            'plain': (False, False),
            'diff': (True, False),
            'bundle': (True, True),
            }[patch_type]
        branch = Branch.open(directory)
        stored_submit_branch = branch.get_submit_branch()
        if submit_branch is None:
            submit_branch = stored_submit_branch
        else:
            if stored_submit_branch is None:
                branch.set_submit_branch(submit_branch)
        if submit_branch is None:
            submit_branch = branch.get_parent()
        if submit_branch is None:
            raise errors.BzrCommandError(gettext('No submit branch specified or known'))

        stored_public_branch = branch.get_public_branch()
        if public_branch is None:
            public_branch = stored_public_branch
        elif stored_public_branch is None:
            branch.set_public_branch(public_branch)
        if not include_bundle and public_branch is None:
            raise errors.BzrCommandError(gettext('No public branch specified or'
                                         ' known'))
        base_revision_id = None
        if revision is not None:
            if len(revision) > 2:
                raise errors.BzrCommandError(gettext('bzr merge-directive takes '
                    'at most two one revision identifiers'))
            revision_id = revision[-1].as_revision_id(branch)
            if len(revision) == 2:
                base_revision_id = revision[0].as_revision_id(branch)
        else:
            revision_id = branch.last_revision()
        revision_id = ensure_null(revision_id)
        if revision_id == NULL_REVISION:
            raise errors.BzrCommandError(gettext('No revisions to bundle.'))
        directive = merge_directive.MergeDirective2.from_objects(
            branch.repository, revision_id, time.time(),
            osutils.local_time_offset(), submit_branch,
            public_branch=public_branch, include_patch=include_patch,
            include_bundle=include_bundle, message=message,
            base_revision_id=base_revision_id)
        if mail_to is None:
            if sign:
                self.outf.write(directive.to_signed(branch))
            else:
                self.outf.writelines(directive.to_lines())
        else:
            message = directive.to_email(mail_to, branch, sign)
            s = SMTPConnection(branch.get_config_stack())
            s.send_email(message)


class cmd_send(Command):
    __doc__ = """Mail or create a merge-directive for submitting changes.

    A merge directive provides many things needed for requesting merges:

    * A machine-readable description of the merge to perform

    * An optional patch that is a preview of the changes requested

    * An optional bundle of revision data, so that the changes can be applied
      directly from the merge directive, without retrieving data from a
      branch.

    `bzr send` creates a compact data set that, when applied using bzr
    merge, has the same effect as merging from the source branch.  
    
    By default the merge directive is self-contained and can be applied to any
    branch containing submit_branch in its ancestory without needing access to
    the source branch.
    
    If --no-bundle is specified, then Bazaar doesn't send the contents of the
    revisions, but only a structured request to merge from the
    public_location.  In that case the public_branch is needed and it must be
    up-to-date and accessible to the recipient.  The public_branch is always
    included if known, so that people can check it later.

    The submit branch defaults to the parent of the source branch, but can be
    overridden.  Both submit branch and public branch will be remembered in
    branch.conf the first time they are used for a particular branch.  The
    source branch defaults to that containing the working directory, but can
    be changed using --from.

    Both the submit branch and the public branch follow the usual behavior with
    respect to --remember: If there is no default location set, the first send
    will set it (use --no-remember to avoid setting it). After that, you can
    omit the location to use the default.  To change the default, use
    --remember. The value will only be saved if the location can be accessed.

    In order to calculate those changes, bzr must analyse the submit branch.
    Therefore it is most efficient for the submit branch to be a local mirror.
    If a public location is known for the submit_branch, that location is used
    in the merge directive.

    The default behaviour is to send the merge directive by mail, unless -o is
    given, in which case it is sent to a file.

    Mail is sent using your preferred mail program.  This should be transparent
    on Windows (it uses MAPI).  On Unix, it requires the xdg-email utility.
    If the preferred client can't be found (or used), your editor will be used.

    To use a specific mail program, set the mail_client configuration option.
    (For Thunderbird 1.5, this works around some bugs.)  Supported values for
    specific clients are "claws", "evolution", "kmail", "mail.app" (MacOS X's
    Mail.app), "mutt", and "thunderbird"; generic options are "default",
    "editor", "emacsclient", "mapi", and "xdg-email".  Plugins may also add
    supported clients.

    If mail is being sent, a to address is required.  This can be supplied
    either on the commandline, by setting the submit_to configuration
    option in the branch itself or the child_submit_to configuration option
    in the submit branch.

    Two formats are currently supported: "4" uses revision bundle format 4 and
    merge directive format 2.  It is significantly faster and smaller than
    older formats.  It is compatible with Bazaar 0.19 and later.  It is the
    default.  "0.9" uses revision bundle format 0.9 and merge directive
    format 1.  It is compatible with Bazaar 0.12 - 0.18.

    The merge directives created by bzr send may be applied using bzr merge or
    bzr pull by specifying a file containing a merge directive as the location.

    bzr send makes extensive use of public locations to map local locations into
    URLs that can be used by other people.  See `bzr help configuration` to
    set them, and use `bzr info` to display them.
    """

    encoding_type = 'exact'

    _see_also = ['merge', 'pull']

    takes_args = ['submit_branch?', 'public_branch?']

    takes_options = [
        Option('no-bundle',
               help='Do not include a bundle in the merge directive.'),
        Option('no-patch', help='Do not include a preview patch in the merge'
               ' directive.'),
        Option('remember',
               help='Remember submit and public branch.'),
        Option('from',
               help='Branch to generate the submission from, '
               'rather than the one containing the working directory.',
               short_name='f',
               type=unicode),
        Option('output', short_name='o',
               help='Write merge directive to this file or directory; '
                    'use - for stdout.',
               type=unicode),
        Option('strict',
               help='Refuse to send if there are uncommitted changes in'
               ' the working tree, --no-strict disables the check.'),
        Option('mail-to', help='Mail the request to this address.',
               type=unicode),
        'revision',
        'message',
        Option('body', help='Body for the email.', type=unicode),
        RegistryOption('format',
                       help='Use the specified output format.',
                       lazy_registry=('bzrlib.send', 'format_registry')),
        ]

    def run(self, submit_branch=None, public_branch=None, no_bundle=False,
            no_patch=False, revision=None, remember=None, output=None,
            format=None, mail_to=None, message=None, body=None,
            strict=None, **kwargs):
        from bzrlib.send import send
        return send(submit_branch, revision, public_branch, remember,
                    format, no_bundle, no_patch, output,
                    kwargs.get('from', '.'), mail_to, message, body,
                    self.outf,
                    strict=strict)


class cmd_bundle_revisions(cmd_send):
    __doc__ = """Create a merge-directive for submitting changes.

    A merge directive provides many things needed for requesting merges:

    * A machine-readable description of the merge to perform

    * An optional patch that is a preview of the changes requested

    * An optional bundle of revision data, so that the changes can be applied
      directly from the merge directive, without retrieving data from a
      branch.

    If --no-bundle is specified, then public_branch is needed (and must be
    up-to-date), so that the receiver can perform the merge using the
    public_branch.  The public_branch is always included if known, so that
    people can check it later.

    The submit branch defaults to the parent, but can be overridden.  Both
    submit branch and public branch will be remembered if supplied.

    If a public_branch is known for the submit_branch, that public submit
    branch is used in the merge instructions.  This means that a local mirror
    can be used as your actual submit branch, once you have set public_branch
    for that mirror.

    Two formats are currently supported: "4" uses revision bundle format 4 and
    merge directive format 2.  It is significantly faster and smaller than
    older formats.  It is compatible with Bazaar 0.19 and later.  It is the
    default.  "0.9" uses revision bundle format 0.9 and merge directive
    format 1.  It is compatible with Bazaar 0.12 - 0.18.
    """

    takes_options = [
        Option('no-bundle',
               help='Do not include a bundle in the merge directive.'),
        Option('no-patch', help='Do not include a preview patch in the merge'
               ' directive.'),
        Option('remember',
               help='Remember submit and public branch.'),
        Option('from',
               help='Branch to generate the submission from, '
               'rather than the one containing the working directory.',
               short_name='f',
               type=unicode),
        Option('output', short_name='o', help='Write directive to this file.',
               type=unicode),
        Option('strict',
               help='Refuse to bundle revisions if there are uncommitted'
               ' changes in the working tree, --no-strict disables the check.'),
        'revision',
        RegistryOption('format',
                       help='Use the specified output format.',
                       lazy_registry=('bzrlib.send', 'format_registry')),
        ]
    aliases = ['bundle']

    _see_also = ['send', 'merge']

    hidden = True

    def run(self, submit_branch=None, public_branch=None, no_bundle=False,
            no_patch=False, revision=None, remember=False, output=None,
            format=None, strict=None, **kwargs):
        if output is None:
            output = '-'
        from bzrlib.send import send
        return send(submit_branch, revision, public_branch, remember,
                         format, no_bundle, no_patch, output,
                         kwargs.get('from', '.'), None, None, None,
                         self.outf, strict=strict)


class cmd_tag(Command):
    __doc__ = """Create, remove or modify a tag naming a revision.

    Tags give human-meaningful names to revisions.  Commands that take a -r
    (--revision) option can be given -rtag:X, where X is any previously
    created tag.

    Tags are stored in the branch.  Tags are copied from one branch to another
    along when you branch, push, pull or merge.

    It is an error to give a tag name that already exists unless you pass
    --force, in which case the tag is moved to point to the new revision.

    To rename a tag (change the name but keep it on the same revsion), run ``bzr
    tag new-name -r tag:old-name`` and then ``bzr tag --delete oldname``.

    If no tag name is specified it will be determined through the 
    'automatic_tag_name' hook. This can e.g. be used to automatically tag
    upstream releases by reading configure.ac. See ``bzr help hooks`` for
    details.
    """

    _see_also = ['commit', 'tags']
    takes_args = ['tag_name?']
    takes_options = [
        Option('delete',
            help='Delete this tag rather than placing it.',
            ),
        custom_help('directory',
            help='Branch in which to place the tag.'),
        Option('force',
            help='Replace existing tags.',
            ),
        'revision',
        ]

    def run(self, tag_name=None,
            delete=None,
            directory='.',
            force=None,
            revision=None,
            ):
        branch, relpath = Branch.open_containing(directory)
        self.add_cleanup(branch.lock_write().unlock)
        if delete:
            if tag_name is None:
                raise errors.BzrCommandError(gettext("No tag specified to delete."))
            branch.tags.delete_tag(tag_name)
            note(gettext('Deleted tag %s.') % tag_name)
        else:
            if revision:
                if len(revision) != 1:
                    raise errors.BzrCommandError(gettext(
                        "Tags can only be placed on a single revision, "
                        "not on a range"))
                revision_id = revision[0].as_revision_id(branch)
            else:
                revision_id = branch.last_revision()
            if tag_name is None:
                tag_name = branch.automatic_tag_name(revision_id)
                if tag_name is None:
                    raise errors.BzrCommandError(gettext(
                        "Please specify a tag name."))
            try:
                existing_target = branch.tags.lookup_tag(tag_name)
            except errors.NoSuchTag:
                existing_target = None
            if not force and existing_target not in (None, revision_id):
                raise errors.TagAlreadyExists(tag_name)
            if existing_target == revision_id:
                note(gettext('Tag %s already exists for that revision.') % tag_name)
            else:
                branch.tags.set_tag(tag_name, revision_id)
                if existing_target is None:
                    note(gettext('Created tag %s.') % tag_name)
                else:
                    note(gettext('Updated tag %s.') % tag_name)


class cmd_tags(Command):
    __doc__ = """List tags.

    This command shows a table of tag names and the revisions they reference.
    """

    _see_also = ['tag']
    takes_options = [
        custom_help('directory',
            help='Branch whose tags should be displayed.'),
        RegistryOption('sort',
            'Sort tags by different criteria.', title='Sorting',
            lazy_registry=('bzrlib.tag', 'tag_sort_methods')
            ),
        'show-ids',
        'revision',
    ]

    @display_command
    def run(self, directory='.', sort=None, show_ids=False, revision=None):
        from bzrlib.tag import tag_sort_methods
        branch, relpath = Branch.open_containing(directory)

        tags = branch.tags.get_tag_dict().items()
        if not tags:
            return

        self.add_cleanup(branch.lock_read().unlock)
        if revision:
            # Restrict to the specified range
            tags = self._tags_for_range(branch, revision)
        if sort is None:
            sort = tag_sort_methods.get()
        sort(branch, tags)
        if not show_ids:
            # [ (tag, revid), ... ] -> [ (tag, dotted_revno), ... ]
            for index, (tag, revid) in enumerate(tags):
                try:
                    revno = branch.revision_id_to_dotted_revno(revid)
                    if isinstance(revno, tuple):
                        revno = '.'.join(map(str, revno))
                except (errors.NoSuchRevision,
                        errors.GhostRevisionsHaveNoRevno,
                        errors.UnsupportedOperation):
                    # Bad tag data/merges can lead to tagged revisions
                    # which are not in this branch. Fail gracefully ...
                    revno = '?'
                tags[index] = (tag, revno)
        self.cleanup_now()
        for tag, revspec in tags:
            self.outf.write('%-20s %s\n' % (tag, revspec))

    def _tags_for_range(self, branch, revision):
        range_valid = True
        rev1, rev2 = _get_revision_range(revision, branch, self.name())
        revid1, revid2 = rev1.rev_id, rev2.rev_id
        # _get_revision_range will always set revid2 if it's not specified.
        # If revid1 is None, it means we want to start from the branch
        # origin which is always a valid ancestor. If revid1 == revid2, the
        # ancestry check is useless.
        if revid1 and revid1 != revid2:
            # FIXME: We really want to use the same graph than
            # branch.iter_merge_sorted_revisions below, but this is not
            # easily available -- vila 2011-09-23
            if branch.repository.get_graph().is_ancestor(revid2, revid1):
                # We don't want to output anything in this case...
                return []
        # only show revisions between revid1 and revid2 (inclusive)
        tagged_revids = branch.tags.get_reverse_tag_dict()
        found = []
        for r in branch.iter_merge_sorted_revisions(
            start_revision_id=revid2, stop_revision_id=revid1,
            stop_rule='include'):
            revid_tags = tagged_revids.get(r[0], None)
            if revid_tags:
                found.extend([(tag, r[0]) for tag in revid_tags])
        return found


class cmd_reconfigure(Command):
    __doc__ = """Reconfigure the type of a bzr directory.

    A target configuration must be specified.

    For checkouts, the bind-to location will be auto-detected if not specified.
    The order of preference is
    1. For a lightweight checkout, the current bound location.
    2. For branches that used to be checkouts, the previously-bound location.
    3. The push location.
    4. The parent location.
    If none of these is available, --bind-to must be specified.
    """

    _see_also = ['branches', 'checkouts', 'standalone-trees', 'working-trees']
    takes_args = ['location?']
    takes_options = [
        RegistryOption.from_kwargs(
            'tree_type',
            title='Tree type',
            help='The relation between branch and tree.',
            value_switches=True, enum_switch=False,
            branch='Reconfigure to be an unbound branch with no working tree.',
            tree='Reconfigure to be an unbound branch with a working tree.',
            checkout='Reconfigure to be a bound branch with a working tree.',
            lightweight_checkout='Reconfigure to be a lightweight'
                ' checkout (with no local history).',
            ),
        RegistryOption.from_kwargs(
            'repository_type',
            title='Repository type',
            help='Location fo the repository.',
            value_switches=True, enum_switch=False,
            standalone='Reconfigure to be a standalone branch '
                '(i.e. stop using shared repository).',
            use_shared='Reconfigure to use a shared repository.',
            ),
        RegistryOption.from_kwargs(
            'repository_trees',
            title='Trees in Repository',
            help='Whether new branches in the repository have trees.',
            value_switches=True, enum_switch=False,
            with_trees='Reconfigure repository to create '
                'working trees on branches by default.',
            with_no_trees='Reconfigure repository to not create '
                'working trees on branches by default.'
            ),
        Option('bind-to', help='Branch to bind checkout to.', type=str),
        Option('force',
            help='Perform reconfiguration even if local changes'
            ' will be lost.'),
        Option('stacked-on',
            help='Reconfigure a branch to be stacked on another branch.',
            type=unicode,
            ),
        Option('unstacked',
            help='Reconfigure a branch to be unstacked.  This '
                'may require copying substantial data into it.',
            ),
        ]

    def run(self, location=None, bind_to=None, force=False,
            tree_type=None, repository_type=None, repository_trees=None,
            stacked_on=None, unstacked=None):
        directory = controldir.ControlDir.open(location)
        if stacked_on and unstacked:
            raise errors.BzrCommandError(gettext("Can't use both --stacked-on and --unstacked"))
        elif stacked_on is not None:
            reconfigure.ReconfigureStackedOn().apply(directory, stacked_on)
        elif unstacked:
            reconfigure.ReconfigureUnstacked().apply(directory)
        # At the moment you can use --stacked-on and a different
        # reconfiguration shape at the same time; there seems no good reason
        # to ban it.
        if (tree_type is None and
            repository_type is None and
            repository_trees is None):
            if stacked_on or unstacked:
                return
            else:
                raise errors.BzrCommandError(gettext('No target configuration '
                    'specified'))
        reconfiguration = None
        if tree_type == 'branch':
            reconfiguration = reconfigure.Reconfigure.to_branch(directory)
        elif tree_type == 'tree':
            reconfiguration = reconfigure.Reconfigure.to_tree(directory)
        elif tree_type == 'checkout':
            reconfiguration = reconfigure.Reconfigure.to_checkout(
                directory, bind_to)
        elif tree_type == 'lightweight-checkout':
            reconfiguration = reconfigure.Reconfigure.to_lightweight_checkout(
                directory, bind_to)
        if reconfiguration:
            reconfiguration.apply(force)
            reconfiguration = None
        if repository_type == 'use-shared':
            reconfiguration = reconfigure.Reconfigure.to_use_shared(directory)
        elif repository_type == 'standalone':
            reconfiguration = reconfigure.Reconfigure.to_standalone(directory)
        if reconfiguration:
            reconfiguration.apply(force)
            reconfiguration = None
        if repository_trees == 'with-trees':
            reconfiguration = reconfigure.Reconfigure.set_repository_trees(
                directory, True)
        elif repository_trees == 'with-no-trees':
            reconfiguration = reconfigure.Reconfigure.set_repository_trees(
                directory, False)
        if reconfiguration:
            reconfiguration.apply(force)
            reconfiguration = None


class cmd_switch(Command):
    __doc__ = """Set the branch of a checkout and update.

    For lightweight checkouts, this changes the branch being referenced.
    For heavyweight checkouts, this checks that there are no local commits
    versus the current bound branch, then it makes the local branch a mirror
    of the new location and binds to it.

    In both cases, the working tree is updated and uncommitted changes
    are merged. The user can commit or revert these as they desire.

    Pending merges need to be committed or reverted before using switch.

    The path to the branch to switch to can be specified relative to the parent
    directory of the current branch. For example, if you are currently in a
    checkout of /path/to/branch, specifying 'newbranch' will find a branch at
    /path/to/newbranch.

    Bound branches use the nickname of its master branch unless it is set
    locally, in which case switching will update the local nickname to be
    that of the master.
    """

    takes_args = ['to_location?']
    takes_options = ['directory',
                     Option('force',
                        help='Switch even if local commits will be lost.'),
                     'revision',
                     Option('create-branch', short_name='b',
                        help='Create the target branch from this one before'
                             ' switching to it.'),
                    ]

    def run(self, to_location=None, force=False, create_branch=False,
            revision=None, directory=u'.'):
        from bzrlib import switch
        tree_location = directory
        revision = _get_one_revision('switch', revision)
        control_dir = controldir.ControlDir.open_containing(tree_location)[0]
        if to_location is None:
            if revision is None:
                raise errors.BzrCommandError(gettext('You must supply either a'
                                             ' revision or a location'))
            to_location = tree_location
        try:
            branch = control_dir.open_branch()
            had_explicit_nick = branch.get_config().has_explicit_nickname()
        except errors.NotBranchError:
            branch = None
            had_explicit_nick = False
        if create_branch:
            if branch is None:
                raise errors.BzrCommandError(
                    gettext('cannot create branch without source branch'))
            to_location = lookup_new_sibling_branch(control_dir, to_location)
            to_branch = branch.bzrdir.sprout(to_location,
                 possible_transports=[branch.bzrdir.root_transport],
                 source_branch=branch).open_branch()
        else:
            to_branch = lookup_sibling_branch(control_dir, to_location)
        if revision is not None:
            revision = revision.as_revision_id(to_branch)
        switch.switch(control_dir, to_branch, force, revision_id=revision)
        if had_explicit_nick:
            branch = control_dir.open_branch() #get the new branch!
            branch.nick = to_branch.nick
        note(gettext('Switched to branch: %s'),
            urlutils.unescape_for_display(to_branch.base, 'utf-8'))



class cmd_view(Command):
    __doc__ = """Manage filtered views.

    Views provide a mask over the tree so that users can focus on
    a subset of a tree when doing their work. After creating a view,
    commands that support a list of files - status, diff, commit, etc -
    effectively have that list of files implicitly given each time.
    An explicit list of files can still be given but those files
    must be within the current view.

    In most cases, a view has a short life-span: it is created to make
    a selected change and is deleted once that change is committed.
    At other times, you may wish to create one or more named views
    and switch between them.

    To disable the current view without deleting it, you can switch to
    the pseudo view called ``off``. This can be useful when you need
    to see the whole tree for an operation or two (e.g. merge) but
    want to switch back to your view after that.

    :Examples:
      To define the current view::

        bzr view file1 dir1 ...

      To list the current view::

        bzr view

      To delete the current view::

        bzr view --delete

      To disable the current view without deleting it::

        bzr view --switch off

      To define a named view and switch to it::

        bzr view --name view-name file1 dir1 ...

      To list a named view::

        bzr view --name view-name

      To delete a named view::

        bzr view --name view-name --delete

      To switch to a named view::

        bzr view --switch view-name

      To list all views defined::

        bzr view --all

      To delete all views::

        bzr view --delete --all
    """

    _see_also = []
    takes_args = ['file*']
    takes_options = [
        Option('all',
            help='Apply list or delete action to all views.',
            ),
        Option('delete',
            help='Delete the view.',
            ),
        Option('name',
            help='Name of the view to define, list or delete.',
            type=unicode,
            ),
        Option('switch',
            help='Name of the view to switch to.',
            type=unicode,
            ),
        ]

    def run(self, file_list,
            all=False,
            delete=False,
            name=None,
            switch=None,
            ):
        tree, file_list = WorkingTree.open_containing_paths(file_list,
            apply_view=False)
        current_view, view_dict = tree.views.get_view_info()
        if name is None:
            name = current_view
        if delete:
            if file_list:
                raise errors.BzrCommandError(gettext(
                    "Both --delete and a file list specified"))
            elif switch:
                raise errors.BzrCommandError(gettext(
                    "Both --delete and --switch specified"))
            elif all:
                tree.views.set_view_info(None, {})
                self.outf.write(gettext("Deleted all views.\n"))
            elif name is None:
                raise errors.BzrCommandError(gettext("No current view to delete"))
            else:
                tree.views.delete_view(name)
                self.outf.write(gettext("Deleted '%s' view.\n") % name)
        elif switch:
            if file_list:
                raise errors.BzrCommandError(gettext(
                    "Both --switch and a file list specified"))
            elif all:
                raise errors.BzrCommandError(gettext(
                    "Both --switch and --all specified"))
            elif switch == 'off':
                if current_view is None:
                    raise errors.BzrCommandError(gettext("No current view to disable"))
                tree.views.set_view_info(None, view_dict)
                self.outf.write(gettext("Disabled '%s' view.\n") % (current_view))
            else:
                tree.views.set_view_info(switch, view_dict)
                view_str = views.view_display_str(tree.views.lookup_view())
                self.outf.write(gettext("Using '{0}' view: {1}\n").format(switch, view_str))
        elif all:
            if view_dict:
                self.outf.write(gettext('Views defined:\n'))
                for view in sorted(view_dict):
                    if view == current_view:
                        active = "=>"
                    else:
                        active = "  "
                    view_str = views.view_display_str(view_dict[view])
                    self.outf.write('%s %-20s %s\n' % (active, view, view_str))
            else:
                self.outf.write(gettext('No views defined.\n'))
        elif file_list:
            if name is None:
                # No name given and no current view set
                name = 'my'
            elif name == 'off':
                raise errors.BzrCommandError(gettext(
                    "Cannot change the 'off' pseudo view"))
            tree.views.set_view(name, sorted(file_list))
            view_str = views.view_display_str(tree.views.lookup_view())
            self.outf.write(gettext("Using '{0}' view: {1}\n").format(name, view_str))
        else:
            # list the files
            if name is None:
                # No name given and no current view set
                self.outf.write(gettext('No current view.\n'))
            else:
                view_str = views.view_display_str(tree.views.lookup_view(name))
                self.outf.write(gettext("'{0}' view is: {1}\n").format(name, view_str))


class cmd_hooks(Command):
    __doc__ = """Show hooks."""

    hidden = True

    def run(self):
        for hook_key in sorted(hooks.known_hooks.keys()):
            some_hooks = hooks.known_hooks_key_to_object(hook_key)
            self.outf.write("%s:\n" % type(some_hooks).__name__)
            for hook_name, hook_point in sorted(some_hooks.items()):
                self.outf.write("  %s:\n" % (hook_name,))
                found_hooks = list(hook_point)
                if found_hooks:
                    for hook in found_hooks:
                        self.outf.write("    %s\n" %
                                        (some_hooks.get_hook_name(hook),))
                else:
                    self.outf.write(gettext("    <no hooks installed>\n"))


class cmd_remove_branch(Command):
    __doc__ = """Remove a branch.

    This will remove the branch from the specified location but 
    will keep any working tree or repository in place.

    :Examples:

      Remove the branch at repo/trunk::

        bzr remove-branch repo/trunk

    """

    takes_args = ["location?"]

    aliases = ["rmbranch"]

    def run(self, location=None):
        if location is None:
            location = "."
        branch = Branch.open_containing(location)[0]
        branch.bzrdir.destroy_branch()


class cmd_shelve(Command):
    __doc__ = """Temporarily set aside some changes from the current tree.

    Shelve allows you to temporarily put changes you've made "on the shelf",
    ie. out of the way, until a later time when you can bring them back from
    the shelf with the 'unshelve' command.  The changes are stored alongside
    your working tree, and so they aren't propagated along with your branch nor
    will they survive its deletion.

    If shelve --list is specified, previously-shelved changes are listed.

    Shelve is intended to help separate several sets of changes that have
    been inappropriately mingled.  If you just want to get rid of all changes
    and you don't need to restore them later, use revert.  If you want to
    shelve all text changes at once, use shelve --all.

    If filenames are specified, only the changes to those files will be
    shelved. Other files will be left untouched.

    If a revision is specified, changes since that revision will be shelved.

    You can put multiple items on the shelf, and by default, 'unshelve' will
    restore the most recently shelved changes.

    For complicated changes, it is possible to edit the changes in a separate
    editor program to decide what the file remaining in the working copy
    should look like.  To do this, add the configuration option

        change_editor = PROGRAM @new_path @old_path

    where @new_path is replaced with the path of the new version of the 
    file and @old_path is replaced with the path of the old version of 
    the file.  The PROGRAM should save the new file with the desired 
    contents of the file in the working tree.
        
    """

    takes_args = ['file*']

    takes_options = [
        'directory',
        'revision',
        Option('all', help='Shelve all changes.'),
        'message',
        RegistryOption('writer', 'Method to use for writing diffs.',
                       bzrlib.option.diff_writer_registry,
                       value_switches=True, enum_switch=False),

        Option('list', help='List shelved changes.'),
        Option('destroy',
               help='Destroy removed changes instead of shelving them.'),
    ]
    _see_also = ['unshelve', 'configuration']

    def run(self, revision=None, all=False, file_list=None, message=None,
            writer=None, list=False, destroy=False, directory=None):
        if list:
            return self.run_for_list(directory=directory)
        from bzrlib.shelf_ui import Shelver
        if writer is None:
            writer = bzrlib.option.diff_writer_registry.get()
        try:
            shelver = Shelver.from_args(writer(sys.stdout), revision, all,
                file_list, message, destroy=destroy, directory=directory)
            try:
                shelver.run()
            finally:
                shelver.finalize()
        except errors.UserAbort:
            return 0

    def run_for_list(self, directory=None):
        if directory is None:
            directory = u'.'
        tree = WorkingTree.open_containing(directory)[0]
        self.add_cleanup(tree.lock_read().unlock)
        manager = tree.get_shelf_manager()
        shelves = manager.active_shelves()
        if len(shelves) == 0:
            note(gettext('No shelved changes.'))
            return 0
        for shelf_id in reversed(shelves):
            message = manager.get_metadata(shelf_id).get('message')
            if message is None:
                message = '<no message>'
            self.outf.write('%3d: %s\n' % (shelf_id, message))
        return 1


class cmd_unshelve(Command):
    __doc__ = """Restore shelved changes.

    By default, the most recently shelved changes are restored. However if you
    specify a shelf by id those changes will be restored instead.  This works
    best when the changes don't depend on each other.
    """

    takes_args = ['shelf_id?']
    takes_options = [
        'directory',
        RegistryOption.from_kwargs(
            'action', help="The action to perform.",
            enum_switch=False, value_switches=True,
            apply="Apply changes and remove from the shelf.",
            dry_run="Show changes, but do not apply or remove them.",
            preview="Instead of unshelving the changes, show the diff that "
                    "would result from unshelving.",
            delete_only="Delete changes without applying them.",
            keep="Apply changes but don't delete them.",
        )
    ]
    _see_also = ['shelve']

    def run(self, shelf_id=None, action='apply', directory=u'.'):
        from bzrlib.shelf_ui import Unshelver
        unshelver = Unshelver.from_args(shelf_id, action, directory=directory)
        try:
            unshelver.run()
        finally:
            unshelver.tree.unlock()


class cmd_clean_tree(Command):
    __doc__ = """Remove unwanted files from working tree.

    By default, only unknown files, not ignored files, are deleted.  Versioned
    files are never deleted.

    Another class is 'detritus', which includes files emitted by bzr during
    normal operations and selftests.  (The value of these files decreases with
    time.)

    If no options are specified, unknown files are deleted.  Otherwise, option
    flags are respected, and may be combined.

    To check what clean-tree will do, use --dry-run.
    """
    takes_options = ['directory',
                     Option('ignored', help='Delete all ignored files.'),
                     Option('detritus', help='Delete conflict files, merge and revert'
                            ' backups, and failed selftest dirs.'),
                     Option('unknown',
                            help='Delete files unknown to bzr (default).'),
                     Option('dry-run', help='Show files to delete instead of'
                            ' deleting them.'),
                     Option('force', help='Do not prompt before deleting.')]
    def run(self, unknown=False, ignored=False, detritus=False, dry_run=False,
            force=False, directory=u'.'):
        from bzrlib.clean_tree import clean_tree
        if not (unknown or ignored or detritus):
            unknown = True
        if dry_run:
            force = True
        clean_tree(directory, unknown=unknown, ignored=ignored,
                   detritus=detritus, dry_run=dry_run, no_prompt=force)


class cmd_reference(Command):
    __doc__ = """list, view and set branch locations for nested trees.

    If no arguments are provided, lists the branch locations for nested trees.
    If one argument is provided, display the branch location for that tree.
    If two arguments are provided, set the branch location for that tree.
    """

    hidden = True

    takes_args = ['path?', 'location?']

    def run(self, path=None, location=None):
        branchdir = '.'
        if path is not None:
            branchdir = path
        tree, branch, relpath =(
            controldir.ControlDir.open_containing_tree_or_branch(branchdir))
        if path is not None:
            path = relpath
        if tree is None:
            tree = branch.basis_tree()
        if path is None:
            info = branch._get_all_reference_info().iteritems()
            self._display_reference_info(tree, branch, info)
        else:
            file_id = tree.path2id(path)
            if file_id is None:
                raise errors.NotVersionedError(path)
            if location is None:
                info = [(file_id, branch.get_reference_info(file_id))]
                self._display_reference_info(tree, branch, info)
            else:
                branch.set_reference_info(file_id, path, location)

    def _display_reference_info(self, tree, branch, info):
        ref_list = []
        for file_id, (path, location) in info:
            try:
                path = tree.id2path(file_id)
            except errors.NoSuchId:
                pass
            ref_list.append((path, location))
        for path, location in sorted(ref_list):
            self.outf.write('%s %s\n' % (path, location))


class cmd_export_pot(Command):
    __doc__ = """Export command helps and error messages in po format."""

    hidden = True
    takes_options = [Option('plugin', 
                            help='Export help text from named command '\
                                 '(defaults to all built in commands).',
                            type=str),
                     Option('include-duplicates',
                            help='Output multiple copies of the same msgid '
                                 'string if it appears more than once.'),
                            ]

    def run(self, plugin=None, include_duplicates=False):
        from bzrlib.export_pot import export_pot
        export_pot(self.outf, plugin, include_duplicates)


def _register_lazy_builtins():
    # register lazy builtins from other modules; called at startup and should
    # be only called once.
    for (name, aliases, module_name) in [
        ('cmd_bundle_info', [], 'bzrlib.bundle.commands'),
        ('cmd_config', [], 'bzrlib.config'),
        ('cmd_dpush', [], 'bzrlib.foreign'),
        ('cmd_version_info', [], 'bzrlib.cmd_version_info'),
        ('cmd_resolve', ['resolved'], 'bzrlib.conflicts'),
        ('cmd_conflicts', [], 'bzrlib.conflicts'),
        ('cmd_sign_my_commits', [], 'bzrlib.commit_signature_commands'),
        ('cmd_verify_signatures', [],
                                        'bzrlib.commit_signature_commands'),
        ('cmd_test_script', [], 'bzrlib.cmd_test_script'),
        ]:
        builtin_command_registry.register_lazy(name, aliases, module_name)
