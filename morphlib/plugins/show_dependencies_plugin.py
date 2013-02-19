# Copyright (C) 2012-2013  Codethink Limited
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


import cliapp
import os

import morphlib


class ShowDependenciesPlugin(cliapp.Plugin):

    def enable(self):
        self.app.add_subcommand('show-dependencies',
                                self.show_dependencies,
                                arg_synopsis='(REPO REF MORPHOLOGY)...')

    def disable(self):
        pass

    def show_dependencies(self, args):
        '''Dumps the dependency tree of all input morphologies'''

        if not os.path.exists(self.app.settings['cachedir']):
            os.mkdir(self.app.settings['cachedir'])
        cachedir = os.path.join(self.app.settings['cachedir'], 'gits')
        tarball_base_url = self.app.settings['tarball-server']
        repo_resolver = morphlib.repoaliasresolver.RepoAliasResolver(
            self.app.settings['repo-alias'])
        lrc = morphlib.localrepocache.LocalRepoCache(
            self.app, cachedir, repo_resolver, tarball_base_url)
        if self.app.settings['cache-server']:
            rrc = morphlib.remoterepocache.RemoteRepoCache(
                self.app.settings['cache-server'], repo_resolver)
        else:
            rrc = None
            
        build_command = morphlib.buildcommand.BuildCommand(self.app)

        # traverse the morphs to list all the sources
        for repo, ref, filename in self.app.itertriplets(args):
            morph = filename[:-len('.morph')]
            self.app.output.write('dependency graph for %s|%s|%s:\n' %
                                  (repo, ref, morph))

            artifact = build_command.get_artifact_object(repo, ref, filename)

            for artifact in reversed(artifact.walk()):
                self.app.output.write('  %s\n' % artifact)
                for dependency in sorted(artifact.dependencies, key=str):
                    self.app.output.write('    -> %s\n' % dependency)

