import os
import tempfile

import pantheon

from fabric.api import *

def restore_siteurl(site_archive, project, environment = 'dev'):
    filename = pantheon.getfrom_url(url)
    restore_site(filename, project, environment)

def restore_site(archive_file, project='pantheon', environment = 'dev'):
    working_dir = tempfile.mkdtemp() + '/'
    pantheon.unarchive(archive_file, working_dir)
    server = pantheon.PantheonServer()

    archive = pantheon.SiteImport(working_dir, server.webroot, project, environment)

    _setup_databases(archive)
    _setup_site_files(archive)
    _setup_permissions(archive)
    _run_on_sites(archive.sites, 'cc all')
    server.restart_services()

    local("rm -rf " + working_dir)
    #TODO: create vhosts for new project/env on restore
    #TODO: create solr index for new project/env on restore

def _setup_databases(archive):
    pantheon.import_data(archive.sites)

def _setup_site_files(archive):
    if os.path.exists(archive.destination):
        local('rm -r ' + archive.destination)

    with cd(archive.destination):
        local("rsync -avz " + archive.location + " " + archive.destination)
        local("git add .")
        local("git commit -a -m 'Site Restore'")

def _setup_permissions(server, archive):
    local("chown -R %s:%s %s" % (server.owner, server.group, archive.destination))
    for site in archive.sites:
        site.set_site_perms(archive.destination)

def _run_on_sites(sites, cmd):
    for site in sites:
        site.drush(cmd)

    

