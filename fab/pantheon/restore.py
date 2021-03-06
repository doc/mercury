import os
import re

import drupaltools
import project

from fabric.api import local
from fabric.api import cd

class RestoreTools(project.BuildTools):

    def __init__(self, project):
        """ Initialize Restore object. Inherits base methods from BuildTools.

        """
        super(RestoreTools, self).__init__()
        self.destination = os.path.join(self.server.webroot, self.project)

    def parse_backup(self, location):
        """ Get project name from extracted backup.

        """
        self.working_dir = location
        self.backup_project = os.listdir(self.working_dir)[0]
        self.version = drupaltools.get_drupal_version(os.path.join(
                                                          self.working_dir,
                                                          self.backup_project,
                                                          'dev'))[0]

    def setup_database(self):
        """ Restore databases from backup.

        """
        for env in self.environments:
            db_dump = os.path.join(self.working_dir,
                                   self.backup_project,
                                   env,
                                   'database.sql')
            # Create database and import from dumpfile.
            super(RestoreTools, self).setup_database(env,
                                                     self.db_password,
                                                     db_dump,
                                                     False)
            # Cleanup dump file before copying files over.
            local('rm -f %s' % db_dump)

    def restore_site_files(self):
        """ Restore code from backup.

        """
        for env in self.environments:
            if os.path.exists('%s/%s' % (self.destination, env)):
                local('rm -rf %s/%s' % (self.destination, env))
            with cd(os.path.join(self.working_dir, self.backup_project)):
                local('rsync -avz %s %s' % (env, self.destination))
            # It's possible that the backup is from a different project.
            # If so: rename branch, set remote, and set merge refs.
            with cd(os.path.join(self.destination, env)):
                self.old_branch = local('git name-rev --name-only HEAD').strip()
                if self.old_branch != self.project:
                    local('git branch -m %s %s' % (self.old_branch, self.project))
                    local('git remote set-url origin /var/git/projects/%s' % self.project)
                    local('git config branch.%s.remote origin' % self.project)
                    local('git config branch.%s.merge refs/heads/%s' % (self.project, self.project))

    def restore_repository(self):
        """ Restore GIT repo from backup.

        """
        project_repo = os.path.join('/var/git/projects', self.project)
        backup_repo = os.path.join(self.working_dir,
                                   self.backup_project,
                                   '%s.git' % self.backup_project)
        if os.path.exists(project_repo):
            local('rm -rf %s' % project_repo)
        local('rsync -avz %s/ %s/' % (backup_repo, project_repo))
        local('chmod -R g+w %s' % project_repo)

        # Enforce a specific origin remote
        with cd(project_repo):
            # Get version from existing origin. 
            # TODO: One day we can remove this, but this ensures restored sites
            #       will point to the correct origin.
            pattern = re.compile('^origin.*([6,7])\.git.*')
            remotes = local('git remote -v').split('\n')
            for remote in remotes:
                match = pattern.search(remote)
                if match and match.group(1) in ['6', '7']:
                    local('git remote rm origin')
                    local('git remote add --mirror origin ' + \
                          'git://git.getpantheon.com/pantheon/%s.git' % match.group(1))
                    break

            # If restoring into a new project name.    
            if self.old_branch != self.project:
                local('git branch -m %s %s' % (self.old_branch, self.project))

    def setup_permissions(self):
        """ Set permissions on project, and repo using the 'restore' handler.

        """
        super(RestoreTools, self).setup_permissions(handler='restore')

    def cleanup(self):
        """ Remove working_dir.

        """
        local('rm -rf %s' % self.working_dir)

