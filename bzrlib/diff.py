# -*- coding: UTF-8 -*-

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

from bzrlib.trace import mutter
from bzrlib.errors import BzrError
from bzrlib.delta import compare_trees

# TODO: Rather than building a changeset object, we should probably
# invoke callbacks on an object.  That object can either accumulate a
# list, write them out directly, etc etc.

def internal_diff(old_filename, oldlines, new_filename, newlines, to_file):
    import difflib
    
    # FIXME: difflib is wrong if there is no trailing newline.
    # The syntax used by patch seems to be "\ No newline at
    # end of file" following the last diff line from that
    # file.  This is not trivial to insert into the
    # unified_diff output and it might be better to just fix
    # or replace that function.

    # In the meantime we at least make sure the patch isn't
    # mangled.


    # Special workaround for Python2.3, where difflib fails if
    # both sequences are empty.
    if not oldlines and not newlines:
        return

    ud = difflib.unified_diff(oldlines, newlines,
                              fromfile=old_filename+'\t', 
                              tofile=new_filename+'\t')

    ud = list(ud)
    # work-around for difflib being too smart for its own good
    # if /dev/null is "1,0", patch won't recognize it as /dev/null
    if not oldlines:
        ud[2] = ud[2].replace('-1,0', '-0,0')
    elif not newlines:
        ud[2] = ud[2].replace('+1,0', '+0,0')
    # work around for difflib emitting random spaces after the label
    ud[0] = ud[0][:-2] + '\n'
    ud[1] = ud[1][:-2] + '\n'

    for line in ud:
        to_file.write(line)
        if not line.endswith('\n'):
            to_file.write("\n\\ No newline at end of file\n")
    print >>to_file


def external_diff(old_filename, oldlines, new_filename, newlines, to_file,
                  diff_opts):
    """Display a diff by calling out to the external diff program."""
    import sys
    
    if to_file != sys.stdout:
        raise NotImplementedError("sorry, can't send external diff other than to stdout yet",
                                  to_file)

    # make sure our own output is properly ordered before the diff
    to_file.flush()

    from tempfile import NamedTemporaryFile
    import os

    oldtmpf = NamedTemporaryFile()
    newtmpf = NamedTemporaryFile()

    try:
        # TODO: perhaps a special case for comparing to or from the empty
        # sequence; can just use /dev/null on Unix

        # TODO: if either of the files being compared already exists as a
        # regular named file (e.g. in the working directory) then we can
        # compare directly to that, rather than copying it.

        oldtmpf.writelines(oldlines)
        newtmpf.writelines(newlines)

        oldtmpf.flush()
        newtmpf.flush()

        if not diff_opts:
            diff_opts = []
        diffcmd = ['diff',
                   '--label', old_filename+'\t',
                   oldtmpf.name,
                   '--label', new_filename+'\t',
                   newtmpf.name]

        # diff only allows one style to be specified; they don't override.
        # note that some of these take optargs, and the optargs can be
        # directly appended to the options.
        # this is only an approximate parser; it doesn't properly understand
        # the grammar.
        for s in ['-c', '-u', '-C', '-U',
                  '-e', '--ed',
                  '-q', '--brief',
                  '--normal',
                  '-n', '--rcs',
                  '-y', '--side-by-side',
                  '-D', '--ifdef']:
            for j in diff_opts:
                if j.startswith(s):
                    break
            else:
                continue
            break
        else:
            diffcmd.append('-u')
                  
        if diff_opts:
            diffcmd.extend(diff_opts)

        rc = os.spawnvp(os.P_WAIT, 'diff', diffcmd)
        
        if rc != 0 and rc != 1:
            # returns 1 if files differ; that's OK
            if rc < 0:
                msg = 'signal %d' % (-rc)
            else:
                msg = 'exit code %d' % rc
                
            raise BzrError('external diff failed with %s; command: %r' % (rc, diffcmd))
    finally:
        oldtmpf.close()                 # and delete
        newtmpf.close()

def show_diff(b, from_spec, specific_files, external_diff_options=None,
              revision2=None, output=None, b2=None):
    """Shortcut for showing the diff to the working tree.

    b
        Branch.

    revision
        None for 'basis tree', or otherwise the old revision to compare against.
    
    The more general form is show_diff_trees(), where the caller
    supplies any two trees.
    """
    if output is None:
        import sys
        output = sys.stdout

    if from_spec is None:
        if b2 is None:
            old_tree = b.basis_tree()
        else:
            old_tree = b.working_tree()
    else:
        old_tree = b.repository.revision_tree(from_spec.in_history(b).rev_id)

    if revision2 is None:
        if b2 is None:
            new_tree = b.working_tree()
        else:
            new_tree = b2.working_tree()
    else:
        new_tree = b.repository.revision_tree(revision2.in_history(b).rev_id)

    return show_diff_trees(old_tree, new_tree, output, specific_files,
                           external_diff_options)



def show_diff_trees(old_tree, new_tree, to_file, specific_files=None,
                    external_diff_options=None):
    """Show in text form the changes from one tree to another.

    to_files
        If set, include only changes to these files.

    external_diff_options
        If set, use an external GNU diff and pass these options.
    """

    old_tree.lock_read()
    try:
        new_tree.lock_read()
        try:
            return _show_diff_trees(old_tree, new_tree, to_file,
                                    specific_files, external_diff_options)
        finally:
            new_tree.unlock()
    finally:
        old_tree.unlock()


def _show_diff_trees(old_tree, new_tree, to_file,
                     specific_files, external_diff_options):

    # TODO: Options to control putting on a prefix or suffix, perhaps as a format string
    old_label = ''
    new_label = ''

    DEVNULL = '/dev/null'
    # Windows users, don't panic about this filename -- it is a
    # special signal to GNU patch that the file should be created or
    # deleted respectively.

    # TODO: Generation of pseudo-diffs for added/deleted files could
    # be usefully made into a much faster special case.

    if external_diff_options:
        assert isinstance(external_diff_options, basestring)
        opts = external_diff_options.split()
        def diff_file(olab, olines, nlab, nlines, to_file):
            external_diff(olab, olines, nlab, nlines, to_file, opts)
    else:
        diff_file = internal_diff
    

    delta = compare_trees(old_tree, new_tree, want_unchanged=False,
                          specific_files=specific_files)

    has_changes = 0
    for path, file_id, kind in delta.removed:
        has_changes = 1
        print >>to_file, '=== removed %s %r' % (kind, path)
        old_tree.inventory[file_id].diff(diff_file, old_label + path, old_tree,
                                         DEVNULL, None, None, to_file)
    for path, file_id, kind in delta.added:
        has_changes = 1
        print >>to_file, '=== added %s %r' % (kind, path)
        new_tree.inventory[file_id].diff(diff_file, new_label + path, new_tree,
                                         DEVNULL, None, None, to_file, 
                                         reverse=True)
    for (old_path, new_path, file_id, kind,
         text_modified, meta_modified) in delta.renamed:
        has_changes = 1
        prop_str = get_prop_change(meta_modified)
        print >>to_file, '=== renamed %s %r => %r%s' % (
                          kind, old_path, new_path, prop_str)
        _maybe_diff_file_or_symlink(old_label, old_path, old_tree, file_id,
                                    new_label, new_path, new_tree,
                                    text_modified, kind, to_file, diff_file)
    for path, file_id, kind, text_modified, meta_modified in delta.modified:
        has_changes = 1
        prop_str = get_prop_change(meta_modified)
        print >>to_file, '=== modified %s %r%s' % (kind, path, prop_str)
        if text_modified:
            _maybe_diff_file_or_symlink(old_label, path, old_tree, file_id,
                                        new_label, path, new_tree,
                                        True, kind, to_file, diff_file)
    return has_changes
    

def get_prop_change(meta_modified):
    if meta_modified:
        return " (properties changed)"
    else:
        return  ""


def _maybe_diff_file_or_symlink(old_label, old_path, old_tree, file_id,
                                new_label, new_path, new_tree, text_modified,
                                kind, to_file, diff_file):
    if text_modified:
        new_entry = new_tree.inventory[file_id]
        old_tree.inventory[file_id].diff(diff_file,
                                         old_label + old_path, old_tree,
                                         new_label + new_path, new_entry, 
                                         new_tree, to_file)
