# (C) 2005 Canonical Ltd

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



def show_status(branch, show_unchanged=False,
                specific_files=None,
                show_ids=False):
    """Display single-line status for non-ignored working files.

    show_all
        If true, show unmodified files too.

    specific_files
        If set, only show the status of files in this list.
    """
    import sys
    from bzrlib.diff import compare_trees
    
    branch.lock('r')
    try:

        old = branch.basis_tree()
        new = branch.working_tree()

        delta = compare_trees(old, new, want_unchanged=show_unchanged,
                              specific_files=specific_files)

        delta.show(sys.stdout, show_ids=show_ids,
                   show_unchanged=show_unchanged)

        unknowns = new.unknowns()
        done_header = False
        for path in unknowns:
            # FIXME: Should also match if the unknown file is within a
            # specified directory.
            if specific_files:
                if path not in specific_files:
                    continue
            if not done_header:
                print 'unknown:'
                done_header = True
            print ' ', path
    finally:
        branch.unlock()
        
