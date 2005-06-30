#! /usr/bin/python

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

"""Experiment in converting existing bzr branches to weaves."""


import sys
import bzrlib.branch
from weave import Weave
from weavefile import write_weave

import hotshot
import tempfile


def convert():
    WEAVE_NAME = "test.weave"

    wf = Weave()

    b = bzrlib.branch.find_branch('.')

    print 'converting...'

    fid = b.read_working_inventory().path2id(sys.argv[1])

    parents = set()
    revno = 1
    for rev_id in b.revision_history():
        print revno
        tree = b.revision_tree(rev_id)
        inv = tree.inventory

        if fid not in tree:
            print '  (not present)'
            continue

        text = tree.get_file(fid).readlines()
        weave_id = wf.add(parents, text)
        parents.add(weave_id)
        revno += 1

        print '  %4d lines' % len(text)

    write_weave(wf, file(WEAVE_NAME, 'wb'))


prof_f = tempfile.NamedTemporaryFile()

prof = hotshot.Profile(prof_f.name)

prof.runcall(convert) 
prof.close()

import hotshot.stats
stats = hotshot.stats.load(prof_f.name)
#stats.strip_dirs()
stats.sort_stats('time')
## XXX: Might like to write to stderr or the trace file instead but
## print_stats seems hardcoded to stdout
stats.print_stats(20)
            

