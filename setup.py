#! /usr/bin/env python

"""Installation script for bzr.
Run it with
 './setup.py install', or
 './setup.py --help' for more options
"""

import os
import sys

import bzrlib

##
# META INFORMATION FOR SETUP

META_INFO = {'name':         'bzr',
             'version':      bzrlib.__version__,
             'author':       'Canonical Ltd',
             'author_email': 'bazaar@lists.canonical.com',
             'url':          'http://www.bazaar-vcs.org/',
             'description':  'Friendly distributed version control system',
             'license':      'GNU GPL v2',
            }

# The list of packages is automatically generated later. Add other things
# that are part of BZRLIB here.
BZRLIB = {}

PKG_DATA = {# install files from selftest suite
            'package_data': {'bzrlib': ['doc/api/*.txt',
                                        'tests/test_patches_data/*',
                                       ]},
           }

######################################################################
# Reinvocation stolen from bzr, we need python2.4 by virtue of bzr_man
# including bzrlib.help

try:
    version_info = sys.version_info
except AttributeError:
    version_info = 1, 5 # 1.5 or older

REINVOKE = "__BZR_REINVOKE"
NEED_VERS = (2, 4)
KNOWN_PYTHONS = ('python2.4',)

if version_info < NEED_VERS:
    if not os.environ.has_key(REINVOKE):
        # mutating os.environ doesn't work in old Pythons
        os.putenv(REINVOKE, "1")
        for python in KNOWN_PYTHONS:
            try:
                os.execvp(python, [python] + sys.argv)
            except OSError:
                pass
    print >>sys.stderr, "bzr: error: cannot find a suitable python interpreter"
    print >>sys.stderr, "  (need %d.%d or later)" % NEED_VERS
    sys.exit(1)
if getattr(os, "unsetenv", None) is not None:
    os.unsetenv(REINVOKE)


def get_bzrlib_packages():
    """Recurse through the bzrlib directory, and extract the package names"""

    packages = []
    base_path = os.path.dirname(os.path.abspath(bzrlib.__file__))
    for root, dirs, files in os.walk(base_path):
        if '__init__.py' in files:
            assert root.startswith(base_path)
            # Get just the path below bzrlib
            package_path = root[len(base_path):]
            # Remove leading and trailing slashes
            package_path = package_path.strip('\\/')
            if not package_path:
                package_name = 'bzrlib'
            else:
                package_name = ('bzrlib.' +
                            package_path.replace('/', '.').replace('\\', '.'))
            packages.append(package_name)
    return sorted(packages)


BZRLIB['packages'] = get_bzrlib_packages()


from distutils.core import setup
from distutils.command.install_scripts import install_scripts
from distutils.command.build import build

###############################
# Overridden distutils actions
###############################

class my_install_scripts(install_scripts):
    """ Customized install_scripts distutils action.
    Create bzr.bat for win32.
    """
    def run(self):
        install_scripts.run(self)   # standard action

        if sys.platform == "win32":
            try:
                scripts_dir = self.install_dir
                script_path = self._quoted_path(os.path.join(scripts_dir,
                                                             "bzr"))
                python_exe = self._quoted_path(sys.executable)
                args = self._win_batch_args()
                batch_str = "@%s %s %s" % (python_exe, script_path, args)
                batch_path = script_path + ".bat"
                f = file(batch_path, "w")
                f.write(batch_str)
                f.close()
                print "Created:", batch_path
            except Exception, e:
                print "ERROR: Unable to create %s: %s" % (batch_path, e)

    def _quoted_path(self, path):
        if ' ' in path:
            return '"' + path + '"'
        else:
            return path

    def _win_batch_args(self):
        from bzrlib.win32utils import winver
        if winver == 'Windows NT':
            return '%*'
        else:
            return '%1 %2 %3 %4 %5 %6 %7 %8 %9'
#/class my_install_scripts


class bzr_build(build):
    """Customized build distutils action.
    Generate bzr.1.
    """
    def run(self):
        build.run(self)

        import generate_docs
        generate_docs.main(argv=["bzr", "man"])


########################
## Setup
########################

command_classes = {'install_scripts': my_install_scripts,
                   'build': bzr_build}
ext_modules = []
try:
    from Pyrex.Distutils import build_ext
except ImportError:
    # try to build the extension from the prior generated source.
    print ("Pyrex not available, while bzr will build, "
           "you cannot modify the C extensions.")
    from distutils.command.build_ext import build_ext
    from distutils.extension import Extension
    ext_modules.extend([
        Extension("bzrlib._dirstate_helpers_c",
                  ["bzrlib/_dirstate_helpers_c.c"],
                  libraries=[],
                  ),
    ])
else:
    from distutils.extension import Extension
    ext_modules.extend([
        Extension("bzrlib._dirstate_helpers_c",
                  ["bzrlib/_dirstate_helpers_c.pyx"],
                  libraries=[],
                  ),
    ])
command_classes['build_ext'] = build_ext

if 'bdist_wininst' in sys.argv:
    import glob
    # doc files
    docs = glob.glob('doc/*.htm') + ['doc/default.css']
    dev_docs = glob.glob('doc/developers/*.htm')
    # python's distutils-based win32 installer
    ARGS = {'scripts': ['bzr', 'tools/win32/bzr-win32-bdist-postinstall.py'],
            'ext_modules': ext_modules,
            # help pages
            'data_files': [('Doc/Bazaar', docs),
                           ('Doc/Bazaar/developers', dev_docs),
                          ],
            # for building pyrex extensions
            'cmdclass': {'build_ext': build_ext},
           }

    ARGS.update(META_INFO)
    ARGS.update(BZRLIB)
    ARGS.update(PKG_DATA)
    
    setup(**ARGS)

elif 'py2exe' in sys.argv:
    # py2exe setup
    import py2exe

    # pick real bzr version
    import bzrlib

    version_number = []
    for i in bzrlib.version_info[:4]:
        try:
            i = int(i)
        except ValueError:
            i = 0
        version_number.append(str(i))
    version_str = '.'.join(version_number)

    target = py2exe.build_exe.Target(script = "bzr",
                                     dest_base = "bzr",
                                     icon_resources = [(0,'bzr.ico')],
                                     name = META_INFO['name'],
                                     version = version_str,
                                     description = META_INFO['description'],
                                     author = META_INFO['author'],
                                     copyright = "(c) Canonical Ltd, 2005-2007",
                                     company_name = "Canonical Ltd.",
                                     comments = META_INFO['description'],
                                    )

    additional_packages =  []
    if sys.version.startswith('2.4'):
        # adding elementtree package
        additional_packages.append('elementtree')
    elif sys.version.startswith('2.5'):
        additional_packages.append('xml.etree')
    else:
        import warnings
        warnings.warn('Unknown Python version.\n'
                      'Please check setup.py script for compatibility.')
    # email package from std python library use lazy import,
    # so we need to explicitly add all package
    additional_packages.append('email')

    options_list = {"py2exe": {"packages": BZRLIB['packages'] +
                                           additional_packages,
                               "excludes": ["Tkinter", "medusa", "tools"],
                               "dist_dir": "win32_bzr.exe",
                              },
                   }
    setup(options=options_list,
          console=[target,
                   'tools/win32/bzr_postinstall.py',
                  ],
          zipfile='lib/library.zip')

else:
    # std setup
    ARGS = {'scripts': ['bzr'],
            'data_files': [('man/man1', ['bzr.1'])],
            'cmdclass': command_classes,
            'ext_modules': ext_modules,
           }
    
    ARGS.update(META_INFO)
    ARGS.update(BZRLIB)
    ARGS.update(PKG_DATA)

    setup(**ARGS)
