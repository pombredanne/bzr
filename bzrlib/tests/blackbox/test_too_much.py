# Copyright (C) 2005 Canonical Ltd
# -*- coding: utf-8 -*-
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

# Mr. Smoketoomuch: I'm sorry?
# Mr. Bounder: You'd better cut down a little then.
# Mr. Smoketoomuch: Oh, I see! Smoke too much so I'd better cut down a little
#                   then!

"""Black-box tests for bzr.

These check that it behaves properly when it's invoked through the regular
command-line interface. This doesn't actually run a new interpreter but 
rather starts again from the run_bzr function.
"""


# XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
# Note: Please don't add new tests here, it's too big and bulky.  Instead add
# them into small suites in bzrlib.tests.blackbox.test_FOO for the particular
# UI command/aspect that is being tested.


from cStringIO import StringIO
import os
import re
import sys

import bzrlib
from bzrlib import (
    osutils,
    )
from bzrlib.branch import Branch
from bzrlib.errors import BzrCommandError
from bzrlib.osutils import (
    has_symlinks,
    pathjoin,
    terminal_width,
    )
from bzrlib.tests.HTTPTestUtil import TestCaseWithWebserver
from bzrlib.tests.test_sftp_transport import TestCaseWithSFTPServer
from bzrlib.tests.blackbox import ExternalBase
from bzrlib.workingtree import WorkingTree


class TestCommands(ExternalBase):

    def test_invalid_commands(self):
        self.runbzr("pants", retcode=3)
        self.runbzr("--pants off", retcode=3)
        self.runbzr("diff --message foo", retcode=3)

    def test_revert(self):
        self.runbzr('init')

        file('hello', 'wt').write('foo')
        self.runbzr('add hello')
        self.runbzr('commit -m setup hello')

        file('goodbye', 'wt').write('baz')
        self.runbzr('add goodbye')
        self.runbzr('commit -m setup goodbye')

        file('hello', 'wt').write('bar')
        file('goodbye', 'wt').write('qux')
        self.runbzr('revert hello')
        self.check_file_contents('hello', 'foo')
        self.check_file_contents('goodbye', 'qux')
        self.runbzr('revert')
        self.check_file_contents('goodbye', 'baz')

        os.mkdir('revertdir')
        self.runbzr('add revertdir')
        self.runbzr('commit -m f')
        os.rmdir('revertdir')
        self.runbzr('revert')

        if has_symlinks():
            os.symlink('/unlikely/to/exist', 'symlink')
            self.runbzr('add symlink')
            self.runbzr('commit -m f')
            os.unlink('symlink')
            self.runbzr('revert')
            self.failUnlessExists('symlink')
            os.unlink('symlink')
            os.symlink('a-different-path', 'symlink')
            self.runbzr('revert')
            self.assertEqual('/unlikely/to/exist',
                             os.readlink('symlink'))
        else:
            self.log("skipping revert symlink tests")
        
        file('hello', 'wt').write('xyz')
        self.runbzr('commit -m xyz hello')
        self.runbzr('revert -r 1 hello')
        self.check_file_contents('hello', 'foo')
        self.runbzr('revert hello')
        self.check_file_contents('hello', 'xyz')
        os.chdir('revertdir')
        self.runbzr('revert')
        os.chdir('..')

    def test_main_version(self):
        """Check output from version command and master option is reasonable"""
        # output is intentionally passed through to stdout so that we
        # can see the version being tested
        output = self.runbzr('version', backtick=1)
        self.log('bzr version output:')
        self.log(output)
        self.assert_(output.startswith('Bazaar (bzr) '))
        self.assertNotEqual(output.index('Canonical'), -1)
        # make sure --version is consistent
        tmp_output = self.runbzr('--version', backtick=1)
        self.log('bzr --version output:')
        self.log(tmp_output)
        self.assertEquals(output, tmp_output)

    def example_branch(test):
        test.runbzr('init')
        file('hello', 'wt').write('foo')
        test.runbzr('add hello')
        test.runbzr('commit -m setup hello')
        file('goodbye', 'wt').write('baz')
        test.runbzr('add goodbye')
        test.runbzr('commit -m setup goodbye')

    def test_pull_verbose(self):
        """Pull changes from one branch to another and watch the output."""

        os.mkdir('a')
        os.chdir('a')

        bzr = self.runbzr
        self.example_branch()

        os.chdir('..')
        bzr('branch a b')
        os.chdir('b')
        open('b', 'wb').write('else\n')
        bzr('add b')
        bzr(['commit', '-m', 'added b'])

        os.chdir('../a')
        out = bzr('pull --verbose ../b', backtick=True)
        self.failIfEqual(out.find('Added Revisions:'), -1)
        self.failIfEqual(out.find('message:\n  added b'), -1)
        self.failIfEqual(out.find('added b'), -1)

        # Check that --overwrite --verbose prints out the removed entries
        bzr('commit -m foo --unchanged')
        os.chdir('../b')
        bzr('commit -m baz --unchanged')
        bzr('pull ../a', retcode=3)
        out = bzr('pull --overwrite --verbose ../a', backtick=1)

        remove_loc = out.find('Removed Revisions:')
        self.failIfEqual(remove_loc, -1)
        added_loc = out.find('Added Revisions:')
        self.failIfEqual(added_loc, -1)

        removed_message = out.find('message:\n  baz')
        self.failIfEqual(removed_message, -1)
        self.failUnless(remove_loc < removed_message < added_loc)

        added_message = out.find('message:\n  foo')
        self.failIfEqual(added_message, -1)
        self.failUnless(added_loc < added_message)
        
    def test_locations(self):
        """Using and remembering different locations"""
        os.mkdir('a')
        os.chdir('a')
        self.runbzr('init')
        self.runbzr('commit -m unchanged --unchanged')
        self.runbzr('pull', retcode=3)
        self.runbzr('merge', retcode=3)
        self.runbzr('branch . ../b')
        os.chdir('../b')
        self.runbzr('pull')
        self.runbzr('branch . ../c')
        self.runbzr('pull ../c')
        self.runbzr('merge')
        os.chdir('../a')
        self.runbzr('pull ../b')
        self.runbzr('pull')
        self.runbzr('pull ../c')
        self.runbzr('branch ../c ../d')
        osutils.rmtree('../c')
        self.runbzr('pull')
        os.chdir('../b')
        self.runbzr('pull')
        os.chdir('../d')
        self.runbzr('pull', retcode=3)
        self.runbzr('pull ../a --remember')
        self.runbzr('pull')
        
    def test_unknown_command(self):
        """Handling of unknown command."""
        out, err = self.run_bzr_captured(['fluffy-badger'],
                                         retcode=3)
        self.assertEquals(out, '')
        err.index('unknown command')

    def create_conflicts(self):
        """Create a conflicted tree"""
        os.mkdir('base')
        os.chdir('base')
        file('hello', 'wb').write("hi world")
        file('answer', 'wb').write("42")
        self.runbzr('init')
        self.runbzr('add')
        self.runbzr('commit -m base')
        self.runbzr('branch . ../other')
        self.runbzr('branch . ../this')
        os.chdir('../other')
        file('hello', 'wb').write("Hello.")
        file('answer', 'wb').write("Is anyone there?")
        self.runbzr('commit -m other')
        os.chdir('../this')
        file('hello', 'wb').write("Hello, world")
        self.runbzr('mv answer question')
        file('question', 'wb').write("What do you get when you multiply six"
                                   "times nine?")
        self.runbzr('commit -m this')

    def test_status(self):
        os.mkdir('branch1')
        os.chdir('branch1')
        self.runbzr('init')
        self.runbzr('commit --unchanged --message f')
        self.runbzr('branch . ../branch2')
        self.runbzr('branch . ../branch3')
        self.runbzr('commit --unchanged --message peter')
        os.chdir('../branch2')
        self.runbzr('merge ../branch1')
        self.runbzr('commit --unchanged --message pumpkin')
        os.chdir('../branch3')
        self.runbzr('merge ../branch2')
        message = self.capture('status')


    def test_conflicts(self):
        """Handling of merge conflicts"""
        self.create_conflicts()
        self.runbzr('merge ../other --show-base', retcode=1)
        conflict_text = file('hello').read()
        self.assert_('<<<<<<<' in conflict_text)
        self.assert_('>>>>>>>' in conflict_text)
        self.assert_('=======' in conflict_text)
        self.assert_('|||||||' in conflict_text)
        self.assert_('hi world' in conflict_text)
        self.runbzr('revert')
        self.runbzr('resolve --all')
        self.runbzr('merge ../other', retcode=1)
        conflict_text = file('hello').read()
        self.assert_('|||||||' not in conflict_text)
        self.assert_('hi world' not in conflict_text)
        result = self.runbzr('conflicts', backtick=1)
        self.assertEquals(result, "Text conflict in hello\nText conflict in"
                                  " question\n")
        result = self.runbzr('status', backtick=1)
        self.assert_("conflicts:\n  Text conflict in hello\n"
                     "  Text conflict in question\n" in result, result)
        self.runbzr('resolve hello')
        result = self.runbzr('conflicts', backtick=1)
        self.assertEquals(result, "Text conflict in question\n")
        self.runbzr('commit -m conflicts', retcode=3)
        self.runbzr('resolve --all')
        result = self.runbzr('conflicts', backtick=1)
        self.runbzr('commit -m conflicts')
        self.assertEquals(result, "")

    def test_push(self):
        # create a source branch
        os.mkdir('my-branch')
        os.chdir('my-branch')
        self.example_branch()

        # with no push target, fail
        self.runbzr('push', retcode=3)
        # with an explicit target work
        self.runbzr('push ../output-branch')
        # with an implicit target work
        self.runbzr('push')
        # nothing missing
        self.runbzr('missing ../output-branch')
        # advance this branch
        self.runbzr('commit --unchanged -m unchanged')

        os.chdir('../output-branch')
        # There is no longer a difference as long as we have
        # access to the working tree
        self.runbzr('diff')

        # But we should be missing a revision
        self.runbzr('missing ../my-branch', retcode=1)

        # diverge the branches
        self.runbzr('commit --unchanged -m unchanged')
        os.chdir('../my-branch')
        # cannot push now
        self.runbzr('push', retcode=3)
        # and there are difference
        self.runbzr('missing ../output-branch', retcode=1)
        self.runbzr('missing --verbose ../output-branch', retcode=1)
        # but we can force a push
        self.runbzr('push --overwrite')
        # nothing missing
        self.runbzr('missing ../output-branch')
        
        # pushing to a new dir with no parent should fail
        self.runbzr('push ../missing/new-branch', retcode=3)
        # unless we provide --create-prefix
        self.runbzr('push --create-prefix ../missing/new-branch')
        # nothing missing
        self.runbzr('missing ../missing/new-branch')

    def test_external_command(self):
        """Test that external commands can be run by setting the path
        """
        # We don't at present run bzr in a subprocess for blackbox tests, and so 
        # don't really capture stdout, only the internal python stream.
        # Therefore we don't use a subcommand that produces any output or does
        # anything -- we just check that it can be run successfully.  
        cmd_name = 'test-command'
        if sys.platform == 'win32':
            cmd_name += '.bat'
        oldpath = os.environ.get('BZRPATH', None)
        bzr = self.capture
        try:
            if 'BZRPATH' in os.environ:
                del os.environ['BZRPATH']

            f = file(cmd_name, 'wb')
            if sys.platform == 'win32':
                f.write('@echo off\n')
            else:
                f.write('#!/bin/sh\n')
            # f.write('echo Hello from test-command')
            f.close()
            os.chmod(cmd_name, 0755)

            # It should not find the command in the local 
            # directory by default, since it is not in my path
            bzr(cmd_name, retcode=3)

            # Now put it into my path
            os.environ['BZRPATH'] = '.'

            bzr(cmd_name)

            # Make sure empty path elements are ignored
            os.environ['BZRPATH'] = os.pathsep

            bzr(cmd_name, retcode=3)

        finally:
            if oldpath:
                os.environ['BZRPATH'] = oldpath


def listdir_sorted(dir):
    L = os.listdir(dir)
    L.sort()
    return L


class OldTests(ExternalBase):
    """old tests moved from ./testbzr."""

    def test_bzr(self):
        from os import chdir, mkdir
        from os.path import exists

        runbzr = self.runbzr
        capture = self.capture
        progress = self.log

        progress("basic branch creation")
        mkdir('branch1')
        chdir('branch1')
        runbzr('init')

        self.assertEquals(capture('root').rstrip(),
                          pathjoin(self.test_dir, 'branch1'))

        progress("status of new file")

        f = file('test.txt', 'wt')
        f.write('hello world!\n')
        f.close()

        self.assertEquals(capture('unknowns'), 'test.txt\n')

        out = capture("status")
        self.assertEquals(out, 'unknown:\n  test.txt\n')

        f = file('test2.txt', 'wt')
        f.write('goodbye cruel world...\n')
        f.close()

        out = capture("status test.txt")
        self.assertEquals(out, "unknown:\n  test.txt\n")

        out = capture("status")
        self.assertEquals(out, ("unknown:\n" "  test.txt\n" "  test2.txt\n"))

        os.unlink('test2.txt')

        progress("command aliases")
        out = capture("st")
        self.assertEquals(out, ("unknown:\n" "  test.txt\n"))

        out = capture("stat")
        self.assertEquals(out, ("unknown:\n" "  test.txt\n"))

        progress("command help")
        runbzr("help st")
        runbzr("help")
        runbzr("help commands")
        runbzr("help slartibartfast", 3)

        out = capture("help ci")
        out.index('aliases: ')

        f = file('hello.txt', 'wt')
        f.write('some nice new content\n')
        f.close()

        runbzr("add hello.txt")
        
        f = file('msg.tmp', 'wt')
        f.write('this is my new commit\nand it has multiple lines, for fun')
        f.close()

        runbzr('commit -F msg.tmp')

        self.assertEquals(capture('revno'), '1\n')
        runbzr('export -r 1 export-1.tmp')
        runbzr('export export.tmp')

        runbzr('log')
        runbzr('log -v')
        runbzr('log -v --forward')
        runbzr('log -m', retcode=3)
        log_out = capture('log -m commit')
        self.assert_("this is my new commit\n  and" in log_out)
        self.assert_("rename nested" not in log_out)
        self.assert_('revision-id' not in log_out)
        self.assert_('revision-id' in capture('log --show-ids -m commit'))

        log_out = capture('log --line')
        # determine the widest line we want
        max_width = terminal_width() - 1
        for line in log_out.splitlines():
            self.assert_(len(line) <= max_width, len(line))
        self.assert_("this is my new commit and" not in log_out)
        self.assert_("this is my new commit" in log_out)

        progress("file with spaces in name")
        mkdir('sub directory')
        file('sub directory/file with spaces ', 'wt').write('see how this works\n')
        runbzr('add .')
        runbzr('diff', retcode=1)
        runbzr('commit -m add-spaces')
        runbzr('check')

        runbzr('log')
        runbzr('log --forward')

        runbzr('info')

        if has_symlinks():
            progress("symlinks")
            mkdir('symlinks')
            chdir('symlinks')
            runbzr('init')
            os.symlink("NOWHERE1", "link1")
            runbzr('add link1')
            self.assertEquals(self.capture('unknowns'), '')
            runbzr(['commit', '-m', '1: added symlink link1'])
    
            mkdir('d1')
            runbzr('add d1')
            self.assertEquals(self.capture('unknowns'), '')
            os.symlink("NOWHERE2", "d1/link2")
            self.assertEquals(self.capture('unknowns'), 'd1/link2\n')
            # is d1/link2 found when adding d1
            runbzr('add d1')
            self.assertEquals(self.capture('unknowns'), '')
            os.symlink("NOWHERE3", "d1/link3")
            self.assertEquals(self.capture('unknowns'), 'd1/link3\n')
            runbzr(['commit', '-m', '2: added dir, symlink'])
    
            runbzr('rename d1 d2')
            runbzr('move d2/link2 .')
            runbzr('move link1 d2')
            self.assertEquals(os.readlink("./link2"), "NOWHERE2")
            self.assertEquals(os.readlink("d2/link1"), "NOWHERE1")
            runbzr('add d2/link3')
            runbzr('diff', retcode=1)
            runbzr(['commit', '-m', '3: rename of dir, move symlinks, add link3'])
    
            os.unlink("link2")
            os.symlink("TARGET 2", "link2")
            os.unlink("d2/link1")
            os.symlink("TARGET 1", "d2/link1")
            runbzr('diff', retcode=1)
            self.assertEquals(self.capture("relpath d2/link1"), "d2/link1\n")
            runbzr(['commit', '-m', '4: retarget of two links'])

            # unversion
            runbzr('remove --keep d2/link1')
            self.assertEquals(self.capture('unknowns'), 'd2/link1\n')
            runbzr(['commit', '-m', '5: remove --keep d2/link1'])
            self.assertEquals(self.capture('unknowns'), 'd2/link1\n')

            # remove
            runbzr('add d2/link1')
            runbzr(['commit', '-m', '6: add d2/link1'])
            runbzr('remove d2/link1')
            self.assertEquals(self.capture('unknowns'), '')
            self.assertTrue(self.capture('status --short d2/link1').find(
                'd2/link1') >= 0)
            runbzr(['commit', '-m', '7: remove d2/link1'])

            # try with the rm alias
            os.symlink("TARGET 1", "d2/link1")
            runbzr('add d2/link1')
            runbzr(['commit', '-m', '8: add d2/link1'])
            runbzr('rm d2/link1')
            self.assertEquals(self.capture('unknowns'), '')
            self.assertTrue(self.capture('status --short d2/link1').find(
                'd2/link1') >= 0)
            runbzr(['commit', '-m', '9: unknown d2/link1'])

            os.mkdir("d1")
            runbzr('add d1')
            runbzr('rename d2/link3 d1/link3new')
            runbzr(['commit', '-m', '10: add d1, move/rename link3'])
            
            runbzr(['check'])
            
            runbzr(['export', '-r', '1', 'exp1.tmp'])
            chdir("exp1.tmp")
            self.assertEquals(listdir_sorted("."), [ "link1" ])
            self.assertEquals(os.readlink("link1"), "NOWHERE1")
            chdir("..")
            
            runbzr(['export', '-r', '2', 'exp2.tmp'])
            chdir("exp2.tmp")
            self.assertEquals(listdir_sorted("."), [ "d1", "link1" ])
            chdir("..")
            
            runbzr(['export', '-r', '3', 'exp3.tmp'])
            chdir("exp3.tmp")
            self.assertEquals(listdir_sorted("."), [ "d2", "link2" ])
            self.assertEquals(listdir_sorted("d2"), [ "link1", "link3" ])
            self.assertEquals(os.readlink("d2/link1"), "NOWHERE1")
            self.assertEquals(os.readlink("link2")   , "NOWHERE2")
            chdir("..")
            
            runbzr(['export', '-r', '4', 'exp4.tmp'])
            chdir("exp4.tmp")
            self.assertEquals(listdir_sorted("."), [ "d2", "link2" ])
            self.assertEquals(os.readlink("d2/link1"), "TARGET 1")
            self.assertEquals(os.readlink("link2")   , "TARGET 2")
            self.assertEquals(listdir_sorted("d2"), [ "link1", "link3" ])
            chdir("..")
            
            runbzr(['export', '-r', '5', 'exp5.tmp'])
            chdir("exp5.tmp")
            self.assertEquals(listdir_sorted("."), [ "d2", "link2" ])
            self.assert_(os.path.islink("link2"))
            self.assert_(listdir_sorted("d2")== [ "link3" ])
            chdir("..")
            
            runbzr(['export', '-r', '10', 'exp6.tmp'])
            chdir("exp6.tmp")
            self.assertEqual(listdir_sorted("."), [ "d1", "d2", "link2"])
            self.assertEquals(listdir_sorted("d1"), [ "link3new" ])
            self.assertEquals(listdir_sorted("d2"), [])
            self.assertEquals(os.readlink("d1/link3new"), "NOWHERE3")
            chdir("..")
        else:
            progress("skipping symlink tests")


class RemoteTests(object):
    """Test bzr ui commands against remote branches."""

    def test_branch(self):
        os.mkdir('from')
        wt = self.make_branch_and_tree('from')
        branch = wt.branch
        wt.commit('empty commit for nonsense', allow_pointless=True)
        url = self.get_readonly_url('from')
        self.run_bzr('branch', url, 'to')
        branch = Branch.open('to')
        self.assertEqual(1, len(branch.revision_history()))
        # the branch should be set in to to from
        self.assertEqual(url + '/', branch.get_parent())

    def test_log(self):
        self.build_tree(['branch/', 'branch/file'])
        self.capture('init branch')
        self.capture('add branch/file')
        self.capture('commit -m foo branch')
        url = self.get_readonly_url('branch/file')
        output = self.capture('log %s' % url)
        self.assertEqual(8, len(output.split('\n')))
        
    def test_check(self):
        self.build_tree(['branch/', 'branch/file'])
        self.capture('init branch')
        self.capture('add branch/file')
        self.capture('commit -m foo branch')
        url = self.get_readonly_url('branch/')
        self.run_bzr('check', url)
    
    def test_push(self):
        # create a source branch
        os.mkdir('my-branch')
        os.chdir('my-branch')
        self.run_bzr('init')
        file('hello', 'wt').write('foo')
        self.run_bzr('add', 'hello')
        self.run_bzr('commit', '-m', 'setup')

        # with an explicit target work
        self.run_bzr('push', self.get_url('output-branch'))

    
class HTTPTests(TestCaseWithWebserver, RemoteTests):
    """Test various commands against a HTTP server."""
    
    
class SFTPTestsAbsolute(TestCaseWithSFTPServer, RemoteTests):
    """Test various commands against a SFTP server using abs paths."""

    
class SFTPTestsAbsoluteSibling(TestCaseWithSFTPServer, RemoteTests):
    """Test various commands against a SFTP server using abs paths."""

    def setUp(self):
        super(SFTPTestsAbsoluteSibling, self).setUp()
        self._override_home = '/dev/noone/runs/tests/here'

    
class SFTPTestsRelative(TestCaseWithSFTPServer, RemoteTests):
    """Test various commands against a SFTP server using homedir rel paths."""

    def setUp(self):
        super(SFTPTestsRelative, self).setUp()
        self._get_remote_is_absolute = False
