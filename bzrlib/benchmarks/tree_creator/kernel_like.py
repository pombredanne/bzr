# Copyright (C) 2006 by Canonical Ltd
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

"""Tree creators for kernel-like trees"""

import os

from bzrlib import (
    add,
    bzrdir,
    osutils,
    )

from bzrlib.benchmarks.tree_creator import TreeCreator


class KernelLikeTreeCreator(TreeCreator):
    """Create a basic tree with ~10k unversioned files""" 

    def __init__(self, test, link_working=False, url=None):
        super(KernelLikeTreeCreator, self).__init__(test,
            tree_name='kernel_like_tree',
            link_working=link_working,
            link_bzr=False)

        self._url = url

    def create(self, root):
        """Create all the kernel files in the given location.

        This is overloaded for compatibility reasons.
        """
        if self._url is not None:
            b = bzrdir.BzrDir.create_branch_convenience(self._url)
            d = bzrdir.BzrDir.create(root)
            bzrlib.branch.BranchReferenceFormat().initialize(d, b)
            tree = d.create_workingtree()
        else:
            tree = bzrdir.BzrDir.create_standalone_workingtree(root)

        if not self._link_working or not self.is_caching_enabled():
            # Turns out that 'shutil.copytree()' is no faster than
            # just creating them. Probably the python overhead.
            # Plain _make_kernel_files takes 3-5s
            # cp -a takes 3s
            # using hardlinks takes < 1s.
            self._create_tree(root=root, in_cache=False)
            return tree

        self.ensure_cached()
        cache_dir = self._get_cache_dir()
        osutils.copy_tree(cache_dir, root,
                          handlers={'file':os.link})
        return tree

    def _create_tree(self, root, in_cache=False):
        # a kernel tree has ~10000 and 500 directory, with most files around 
        # 3-4 levels deep. 
        # we simulate this by three levels of dirs named 0-7, givin 512 dirs,
        # and 20 files each.
        files = []
        for outer in range(8):
            files.append("%s/" % outer)
            for middle in range(8):
                files.append("%s/%s/" % (outer, middle))
                for inner in range(8):
                    prefix = "%s/%s/%s/" % (outer, middle, inner)
                    files.append(prefix)
                    files.extend([prefix + str(foo) for foo in range(20)])
        cwd = osutils.getcwd()
        os.chdir(root)
        self._test.build_tree(files)
        os.chdir(cwd)
        if in_cache:
            self._protect_files(root)


class KernelLikeAddedTreeCreator(TreeCreator):

    def __init__(self, test, link_working=False, hot_cache=True):
        super(KernelLikeAddedTreeCreator, self).__init__(test,
            tree_name='kernel_like_added_tree',
            link_working=link_working,
            link_bzr=False,
            hot_cache=hot_cache)

    def _create_tree(self, root, in_cache=False):
        """Create a kernel-like tree with the all files added

        :param root: The root directory to create the files
        :param in_cache: Is this being created in the cache dir?
        """
        kernel_creator = KernelLikeTreeCreator(self._test,
                                               link_working=in_cache)
        tree = kernel_creator.create(root=root)

        # Add everything to it
        tree.lock_write()
        try:
            add.smart_add_tree(tree, [root], recurse=True, save=True)
            if in_cache:
                self._protect_files(root+'/.bzr')
        finally:
            tree.unlock()
        return tree


class KernelLikeCommittedTreeCreator(TreeCreator):
    """Create a tree with ~10K files, and a single commit adding all of them"""

    def __init__(self, test, link_working=False, link_bzr=False,
                 hot_cache=True):
        super(KernelLikeCommittedTreeCreator, self).__init__(test,
            tree_name='kernel_like_committed_tree',
            link_working=link_working,
            link_bzr=link_bzr,
            hot_cache=hot_cache)

    def _create_tree(self, root, in_cache=False):
        """Create a kernel-like tree with all files committed

        :param root: The root directory to create the files
        :param in_cache: Is this being created in the cache dir?
        """
        kernel_creator = KernelLikeAddedTreeCreator(self._test,
                                                    link_working=in_cache,
                                                    hot_cache=(not in_cache))
        tree = kernel_creator.create(root=root)
        tree.commit('first post', rev_id='r1')

        if in_cache:
            self._protect_files(root+'/.bzr')
        return tree


# Helper functions to change the above classes into a single function call

def make_kernel_like_tree(test, root, link_working=True):
    """Setup a temporary tree roughly like a kernel tree.
    
    :param url: Creat the kernel like tree as a lightweight checkout
    of a new branch created at url.
    :param link_working: instead of creating a new copy of all files
        just hardlink the working tree. Tests must request this, because
        they must break links if they want to change the files
    """
    creator = KernelLikeTreeCreator(test, link_working=link_working)
    return creator.create(root=root)


def make_kernel_like_added_tree(test, root,
                                link_working=True,
                                hot_cache=True):
    """Make a kernel like tree, with all files added

    :param root: Where to create the files
    :param link_working: Instead of copying all of the working tree
        files, just hardlink them to the cached files. Tests can unlink
        files that they will change.
    :param hot_cache: Run through the newly created tree and make sure
        the stat-cache is correct. The old way of creating a freshly
        added tree always had a hot cache.
    """
    creator = KernelLikeAddedTreeCreator(test, link_working=link_working,
                                         hot_cache=hot_cache)
    return creator.create(root=root)


def make_kernel_like_committed_tree(test, root='.',
                                    link_working=True,
                                    link_bzr=False,
                                    hot_cache=True):
    """Make a kernel like tree, with all files added and committed

    :param root: Where to create the files
    :param link_working: Instead of copying all of the working tree
        files, just hardlink them to the cached files. Tests can unlink
        files that they will change.
    :param link_bzr: Hardlink the .bzr directory. For readonly 
        operations this is safe, and shaves off a lot of setup time
    """
    creator = KernelLikeCommittedTreeCreator(test,
                                             link_working=link_working,
                                             link_bzr=link_bzr,
                                             hot_cache=hot_cache)
    return creator.create(root=root)

