# Copyright (C) 2011  Codethink Limited
# 
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.


import json
import logging
import os
import shutil
import StringIO
import tarfile
import urlparse

import morphlib


class Builder(object):

    '''Build binary objects for Baserock.
    
    The objects may be chunks or strata.'''
    
    def __init__(self, tempdir, msg, settings):
        self.tempdir = tempdir
        self.msg = msg
        self.settings = settings
        self.cachedir = morphlib.cachedir.CacheDir(settings['cachedir'])

    def build(self, morph):
        '''Build a binary based on a morphology.'''
        if morph.kind == 'chunk':
            self.build_chunk(morph, self.settings['chunk-repo'], 
                             self.settings['chunk-ref'])
        elif morph.kind == 'stratum':
            self.build_stratum(morph)
        elif morph.kind == 'system':
            self.build_system(morph)
        else:
            raise Exception('Unknown kind of morphology: %s' % morph.kind)

    def build_chunk(self, morph, repo, ref):
        '''Build a chunk from a morphology.'''
        logging.debug('Building chunk')
        self.msg('Building chunk %s' % morph.name)
        filename = self.get_cached_name(morph.name, 'chunk', repo, ref)
        if os.path.exists(filename):
            self.msg('Chunk already exists: %s %s' % (repo, ref))
            self.msg('(chunk cached at %s)' % filename)
        else:
            self.ex = morphlib.execute.Execute(self._build, self.msg)
            self.ex.env['WORKAREA'] = self.tempdir.dirname
            self.ex.env['DESTDIR'] = self._inst + '/'
            self.create_build_tree(morph, repo, ref)
            self.ex.run(morph.configure_commands)
            self.ex.run(morph.build_commands)
            self.ex.run(morph.test_commands)
            os.mkdir(self._inst)
            self.ex.run(morph.install_commands, as_fakeroot=True)
            self.prepare_binary_metadata(morph, 
                    repo=repo, 
                    ref=morphlib.git.get_commit_id(repo, ref))

            morphlib.bins.create_chunk(self._inst, 
                self.get_cached_name(morph.name, 'chunk', repo, ref))

            self.tempdir.clear()
        
    def create_build_tree(self, morph, repo, ref):
        '''Export sources from git into the ``self._build`` directory.'''

        logging.debug('Creating build tree at %s' % self._build)
        tarball = self.tempdir.join('sources.tar.gz')
        morphlib.git.export_sources(repo, ref, tarball)
        os.mkdir(self._build)
        f = tarfile.open(tarball)
        f.extractall(path=self._build)
        f.close()
        os.remove(tarball)

    def build_stratum(self, morph):
        '''Build a stratum from a morphology.'''

        for chunk_name, source in morph.sources.iteritems():
            self.msg('Want chunk %s' % chunk_name)
            repo = source['repo']
            ref = source['ref']
            chunk_morph = self.get_morph_from_git(repo, ref)
            self.build_chunk(chunk_morph, repo, ref)

        self.msg('Creating stratum %s' % morph.name)
        os.mkdir(self._inst)
        self.ex = morphlib.execute.Execute(self.tempdir.dirname, self.msg)
        for chunk_name in morph.sources:
            self.msg('Unpacking chunk %s' % chunk_name)
            source = morph.sources[chunk_name]
            chunk_repo = source['repo']
            chunk_ref = source['ref']
            logging.debug('Looking for chunk at repo=%s ref=%s' %
                            (chunk_repo, chunk_ref))
            filename = self.get_cached_name(chunk_name, 'chunk', 
                                            chunk_repo, chunk_ref)
            morphlib.bins.unpack_chunk(filename, self._inst)
        self.prepare_binary_metadata(morph)
        stratum_filename = self.create_stratum(morph)
        self.tempdir.clear()
        return stratum_filename

    def create_stratum(self, morph):
        '''Create a Baserock stratum from the ``self._inst`` directory.
        
        The directory must be filled in with all the relevant files already.
        
        '''

        # FIXME: Should put in stratum's git repo and reference here.
        filename = self.get_cached_name(morph.name, 'stratum', '', '')
        self.msg('Creating stratum %s at %s' % (morph.name, filename))
        morphlib.bins.create_stratum(self._inst, filename)
        return filename

    @property
    def _build(self):
        return self.tempdir.join('build')

    @property
    def _inst(self):
        return self.tempdir.join('inst')

    def get_cached_name(self, name, kind, repo, ref):
        '''Return the cached name of a binary blob, if and when it exists.'''
        if repo and ref:
            abs_ref = morphlib.git.get_commit_id(repo, ref)
        else:
            abs_ref = ''
        dict_key = {
            'name': name,
            'kind': kind,
            'arch': morphlib.util.arch(),
            'repo': repo,
            'ref': abs_ref,
        }
        return self.cachedir.name(dict_key)

    def prepare_binary_metadata(self, morph, **kwargs):
        '''Add metadata to a binary about to be built.'''

        meta = {
            'name': morph.name,
            'kind': morph.kind,
            'description': morph.description,
        }
        for key, value in kwargs.iteritems():
            meta[key] = value
        
        dirname = os.path.join(self._inst, 'baserock')
        filename = os.path.join(dirname, '%s.meta' % morph.name)
        if not os.path.exists(dirname):
            os.mkdir(dirname)
        with open(filename, 'w') as f:
            json.dump(meta, f, indent=4)
            f.write('\n')


    def get_morph_from_git(self, repo, ref):
        morph_name, morph_text = morphlib.git.get_morph_text(repo, ref)    
        f = StringIO.StringIO(morph_text)
        f.name = morph_name
        morph = morphlib.morphology.Morphology(f, 
                                               self.settings['git-base-url'])
        return morph


    def build_system(self, morph):
        '''Build a system image.'''

        logging.debug('Building system image %s' % morph.name)
        self.msg('Building system %s' % morph.name)

        # Build strata.
        stratum_filenames = []
        for stratum in morph.strata:
            self.msg('Want stratum %s' % stratum)
            dirname = os.path.dirname(morph.filename)
            stratum_filename = os.path.join(dirname, '%s.morph' % stratum)
            logging.debug('Morphology should be in %s' % stratum_filename)
            with open(stratum_filename) as f:
                stratum_morph = morphlib.morphology.Morphology(f,
                                    baseurl=self.settings['git-base-url'])
            filename = self.build_stratum(stratum_morph)
            stratum_filenames.append(filename)

        self.tempdir.clear()
        self.msg('Building system image %s' % morph.name)
        self.ex = morphlib.execute.Execute(self.tempdir.dirname, self.msg)
        
        image_name = self.tempdir.join('%s.img' % morph.name)
        
        # Create image.
        self.ex.runv(['qemu-img', 'create', '-f', 'raw', image_name,
                      morph.disk_size])

        # Partition it.
        self.ex.runv(['parted', '-s', image_name, 'mklabel', 'msdos'],
                     as_root=True)
        self.ex.runv(['parted', '-s', image_name, 'mkpart', 'primary', 
                      '0%', '100%'], as_root=True)
        self.ex.runv(['parted', '-s', image_name, 'set', '1', 'boot', 'on'],
                     as_root=True)

        # Install first stage boot loader into MBR.
        self.ex.runv(['install-mbr', image_name], as_root=True)

        # Setup device mapper to access the partition.
        out = self.ex.runv(['kpartx', '-av', image_name], as_root=True)
        devices = [line.split()[2]
                   for line in out.splitlines()
                   if line.startswith('add map ')]
        partition = '/dev/mapper/%s' % devices[0]

        try:
            # Create filesystem.
            self.ex.runv(['mkfs', '-t', 'ext3', partition], as_root=True)
            
            # Mount it.
            mount_point = self.tempdir.join('mnt')
            os.mkdir(mount_point)
            self.ex.runv(['mount', partition, mount_point], as_root=True)

            # Unpack all strata into filesystem.
            for filename in stratum_filenames:
                morphlib.bins.unpack_stratum(filename, mount_point)

            # Create fstab.
            fstab = self.tempdir.join('mnt/etc/fstab')
            with open(fstab, 'w') as f:
                f.write('proc /proc proc defaults 0 0\n')
                f.write('sysfs /sys sysfs defaults 0 0\n')
                f.write('/dev/sda1 / ext4 errors=remount-ro 0 1\n')

            # Install extlinux bootloader.
            conf = os.path.join(mount_point, 'extlinux.conf')
            logging.debug('configure extlinux %s' % conf)
            f = open(conf, 'w')
            f.write('''
default linux
timeout 1

label linux
kernel /vmlinuz
append root=/dev/sda1 init=/bin/sh quiet
''')
            f.close()

            self.ex.runv(['extlinux', '--install', mount_point], as_root=True)
            
            # Weird hack that makes extlinux work. There is a bug somewhere.
            self.ex.runv(['sync'])
            import time; time.sleep(2)

            # Unmount.
            self.ex.runv(['umount', mount_point], as_root=True)
        except BaseException, e:
            # Unmount.
            try:
                self.ex.runv(['umount', mount_point], as_root=True)
            except Exception:
                pass

            # Undo device mapping.
            try:
                self.ex.runv(['kpartx', '-d', image_name], as_root=True)
            except Exception:
                pass
            raise

        # Undo device mapping.
        self.ex.runv(['kpartx', '-d', image_name], as_root=True)

        # Copy image file to cache.
        filename = self.get_cached_name(morph.name, 'system', '', '')
        self.ex.runv(['cp', '-a', image_name, filename])

