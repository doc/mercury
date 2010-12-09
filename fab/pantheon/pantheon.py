# vim: tabstop=4 shiftwidth=4 softtabstop=4
import os
import random
import string
import tarfile
import tempfile
import time
import zipfile

import postback

from fabric.api import *

ENVIRONMENTS = set(['dev','test','live'])
TEMPLATE_DIR = '/opt/pantheon/fab/templates'

def get_environments():
    """ Return list of development environments.

    """
    return ENVIRONMENTS

def get_template(template):
    """Return full path to template file.
    template: template file name

    """
    return os.path.join(get_template_dir(), template)

def get_template_dir():
    """Return template directory.

    """
    return TEMPLATE_DIR

def copy_template(template, destination):
    """Copy template to destination.
    template: template file name
    destination: full path to destination

    """
    local('cp %s %s' % (get_template(template),
                        destination))

def build_template(template_file, values):
    """Return a template object of the template_file with substitued values.
    template_file: full path to template file
    values: dictionary of values to be substituted in template file

    """
    contents = local('cat %s' % template_file)
    template = string.Template(contents)
    template = template.safe_substitute(values)
    return template

def is_aws_server():
    # Check if aws.server file was created during configure.
    return os.path.isfile('/etc/pantheon/aws.server')

def is_ebs_server():
    # Check if ebs.server file was created during configure.
    return os.path.isfile('/etc/pantheon/ebs.server')

def is_private_server():
    # Check if private.server file was created during configure.
    return os.path.isfile('/etc/pantheon/private.server')

def random_string(length):
    """ Create random string of ascii letters & digits.
    length: Int. Character length of string to return.

    """
    return ''.join(['%s' % random.choice (string.ascii_letters + \
                                          string.digits) \
                                          for i in range(length)])

def export_data(project, environment, destination):
    """Export the database for a particular project/environment to destination.
    exported database will have a name in the form of: project_environment.sql
    project: project name
    environment: environment name
    destination: path where database file should be exported

    """
    filename = os.path.join(destination, '%s_%s.sql' % (project, environment))
    username, password, db_name = _get_database_vars(project, environment)
    local("mysqldump --single-transaction --user='%s' \
                                          --password='%s' \
                                            %s > %s" % (username,
                                                       password,
                                                       db_name,
                                                       filename))
    return filename

def import_data(project, environment, source):
    """Create database then import from source.
    project: project name
    environment: environment name
    source: full path to database dump file to import.

    """
    database = '%s_%s' % (project, environment)
    create_database(database)
    import_db_dump(source, database)

def create_database(database):
    local("mysql -u root -e 'DROP DATABASE IF EXISTS %s'" % database)
    local("mysql -u root -e 'CREATE DATABASE %s'" % database)

def set_database_grants(database, username, password):
    local("mysql -u root -e \"GRANT ALL ON %s.* TO '%s'@'localhost' \
           IDENTIFIED BY '%s';\"" % (database, username, password))

def import_db_dump(database_dump, database_name):
    #NOTE: saved the below for now. causes issues with dumps from phpmyadmin.
    #grep -v '^INSERT INTO `cache[_a-z]*`' | \
    #grep -v '^INSERT INTO `ctools_object_cache`' | \
    #grep -v '^INSERT INTO `watchdog`' | \
    #grep -v '^INSERT INTO `accesslog`' | \

    # Strip cache tables, convert MyISAM to InnoDB, and import.
    local("cat %s | grep -v '^USE `' | \
           sed 's/^[)] ENGINE=MyISAM/) ENGINE=InnoDB/' | \
           mysql -u root %s" % (database_dump, database_name))

def parse_vhost(path):
    """Helper method that returns environment variables from a vhost file.
    path: full path to vhost file.
    returns: dict of all vhost SetEnv variables.

    """
    env_vars = dict()
    with open(path, 'r') as f:
       vhost = f.readlines()
    for line in vhost:
        line = line.strip()
        if line.find('SetEnv') != -1:
            var = line.split()
            env_vars[var[1]] = var[2]
    return env_vars


def restart_bcfg2():
    local('/etc/init.d/bcfg2-server restart')
    server_running = False
    warn('Waiting for bcfg2 server to start')
    while not server_running:
        with settings(hide('warnings'), warn_only=True):
            server_running = (local('netstat -atn | grep :6789')).rstrip('\n')
        time.sleep(5)


def is_drupal_installed(project, environment):
    """Return True if the Drupal installation process has been completed.
       project: project name
       environment: environment name.

    """
    #TODO: Find better way of determining this than hitting the db.
    (username, password, db_name) = _get_database_vars(project, environment)
    with hide('running'):
        status = local("mysql -u %s -p%s %s -e 'show tables;' | \
                        awk '/system/'" % (username, password, db_name))
    # If any data is in status, assume site is installed.
    return bool(status)

def download(url):
    """Download url to temporary directory and return path to file.
    url: fully qualified url of file to download.
    returns: full path to downloaded file.

    """
    download_dir = tempfile.mkdtemp()
    filebase = os.path.basename(url)
    filename = os.path.join(download_dir, filebase)

    curl(url, filename)
    return filename

def curl(url, destination):
    """Use curl to save url to destination.
    url: url to download
    destination: full path/ filename to save curl output.

    """
    local('curl "%s" -o "%s"' % (url, destination))

def _get_database_vars(project, environment):
    """Helper method that returns database variables for a project/environment.
    project: project name
    environment: environment name.
    returns: Tuple: (username, password, db_name)

    """
    vhost = PantheonServer().get_vhost_file(project, environment)
    env_vars = parse_vhost(vhost)
    return (env_vars.get('db_username'),
            env_vars.get('db_password'),
            env_vars.get('db_name'))

def configure_root_certificate(pki_server):
    """Helper function that connects to pki.getpantheon.com and configures the
    root certificate used throughout the infrastructure."""
    
    # Download and install the root CA.
    local('curl %s | sudo tee /usr/share/ca-certificates/pantheon.crt' % pki_server)
    local('echo "pantheon.crt" | sudo tee -a /etc/ca-certificates.conf')
    #local('cat /etc/ca-certificates.conf | sort | uniq | sudo tee /etc/ca-certificates.conf') # Remove duplicates.
    local('sudo update-ca-certificates')


class PantheonServer:

    def __init__(self):
        # Ubuntu / Debian
        if os.path.exists('/etc/debian_version'):
            self.distro = 'ubuntu'
            self.mysql = 'mysql'
            self.owner = 'root'
            self.web_group = 'www-data'
            self.hudson_group = 'nogroup'
            self.tomcat_owner = 'tomcat6'
            self.tomcat_version = '6'
            self.webroot = '/var/www/'
            self.ftproot = '/srv/ftp/pantheon/'
            self.vhost_dir = '/etc/apache2/sites-available/'
        # Centos
        elif os.path.exists('/etc/redhat-release'):
            self.distro = 'centos'
            self.mysql = 'mysqld'
            self.owner = 'root'
            self.web_group = 'apache'
            self.hudson_group = 'hudson'
            self.tomcat_owner = 'tomcat'
            self.tomcat_version = '5'
            self.webroot = '/var/www/html/'
            self.ftproot = '/var/ftp/pantheon/'
            self.vhost_dir = '/etc/httpd/conf/vhosts/'
        #global
        self.template_dir = get_template_dir()

    def get_hostname(self):
        if os.path.exists("/usr/local/bin/ec2-metadata"):
            return local('/usr/local/bin/ec2-metadata -p | sed "s/public-hostname: //"').rstrip('\n')
        else:
            return local('hostname').rstrip('\n')

    def update_packages(self):
        if (self.distro == "centos"):
            local('yum clean all')
            local('yum -y update')
        else:
            local('apt-get -y update')
            local('apt-get -y dist-upgrade')

    def restart_services(self):
        if self.distro == 'ubuntu':
            local('/etc/init.d/apache2 restart')
            local('/etc/init.d/memcached restart')
            local('/etc/init.d/tomcat6 restart')
            local('/etc/init.d/varnish restart')
            local('/etc/init.d/mysql restart')
        elif self.distro == 'centos':
            local('/etc/init.d/httpd restart')
            local('/etc/init.d/memcached restart')
            local('/etc/init.d/tomcat5 restart')
            local('/etc/init.d/varnish restart')
            local('/etc/init.d/mysqld restart')

    def setup_iptables(self, file):
        local('/sbin/iptables-restore < ' + file)
        local('/sbin/iptables-save > /etc/iptables.rules')

    def create_drush_alias(self, drush_dict):
        """ Create an alias.drushrc.php file.
        drush_dict: project:
                    environment:
                    vhost_path: full path to vhost file
                    root: full path to drupal installation

        """
        alias_template = get_template('drush.alias.drushrc.php')
        alias_file = '/opt/drush/aliases/%s_%s.alias.drushrc.php' % (
                                            drush_dict.get('project'),
                                            drush_dict.get('environment'))
        template = build_template(alias_template, drush_dict)
        with open(alias_file, 'w') as f:
            f.write(template)

    def create_vhost(self, filename, vhost_dict):
        """
        filename:  vhost filename
        vhost_dict: server_name:
                    server_alias:
                    project:
                    environment:
                    db_name:
                    db_username:
                    db_password:
                    db_solr_path:
                    memcache_prefix:

        """
        vhost_template = get_template('vhost.template.%s' % self.distro)
        template = build_template(vhost_template, vhost_dict)
        vhost = os.path.join(self.vhost_dir, filename)
        with open(vhost, 'w') as f:
            f.write(template)
        local('chmod 640 %s' % vhost)

    def create_solr_index(self, project, environment):
        """ Create solr index in: /var/solr/project/environment.
        project: project name
        environment: development environment

        """

        # Create project directory
        project_dir = '/var/solr/%s/' % project
        if not os.path.exists(project_dir):
            local('mkdir %s' % project_dir)
        local('chown %s:%s %s' % (self.tomcat_owner,
                                  self.tomcat_owner,
                                  project_dir))

        # Create data directory from sample solr data.
        data_dir = os.path.join(project_dir, environment)
        if os.path.exists(data_dir):
            local('rm -rf ' + data_dir)
        data_dir_template = os.path.join(get_template_dir(), 'solr')
        local('cp -R %s %s' % (data_dir_template, data_dir))
        local('chown -R %s:%s %s' % (self.tomcat_owner,
                                     self.tomcat_owner,
                                     data_dir))

        # Tell Tomcat where indexes are located.
        tomcat_template = get_template('tomcat_solr_home.xml')
        values = {'solr_path': '%s/%s' % (project, environment)}
        template = build_template(tomcat_template, values)
        tomcat_file = "/etc/tomcat%s/Catalina/localhost/%s_%s.xml" % (
                                                      self.tomcat_version,
                                                      project,
                                                      environment)
        with open(tomcat_file, 'w') as f:
            f.write(template)
        local('chown %s:%s %s' % (self.tomcat_owner,
                                  self.tomcat_owner,
                                  tomcat_file))


    def create_drupal_cron(self, project, environment):
        """ Create Hudson drupal cron job.
        project: project name
        environment: development environment

        """
        # Create job directory
        jobdir = '/var/lib/hudson/jobs/cron_%s_%s/' % (project, environment)
        if not os.path.exists(jobdir):
            local('mkdir -p ' + jobdir)

        # Create job from template
        values = {'drush_alias':'@%s_%s' % (project, environment)}
        cron_template = get_template('hudson.drupal.cron')
        template = build_template(cron_template, values)
        with open(jobdir + 'config.xml', 'w') as f:
            f.write(template)

        # Set Perms
        local('chown -R %s:%s %s' % ('hudson', self.hudson_group, jobdir))


    def get_vhost_file(self, project, environment):
        """Helper method that returns the full path to the vhost file for a
        particular project/environment.
        project: project name
        environment: environment name.

        """
        filename = '%s_%s' % (project, environment)
        if environment == 'live':
            filename = '000_' + filename
        if self.distro == 'ubuntu':
            return '/etc/apache2/sites-available/%s' % filename
        elif self.distro == 'centos':
            return '/etc/httpd/conf/vhosts/%s' % filename

    def get_ldap_group(self):
        """Helper method to pull the ldap group we authorize.
        Helpful in keeping filesystem permissions correct.

        /etc/pantheon/ldapgroup is written as part of the configure_ldap job.

        """
        with open('/etc/pantheon/ldapgroup', 'r') as f:
            return f.readline().rstrip("\n")

    def set_ldap_group(self, require_group):
        """Helper method to pull the ldap group we authorize.
        Helpful in keeping filesystem permissions correct.

        /etc/pantheon/ldapgroup is written as part of the configure_ldap job.

        """
        with open('/etc/pantheon/ldapgroup', 'w') as f:
            f.write('%s' % require_group)

class PantheonArchive(object):

    def __init__(self, path):
        self.path = path
        self.filetype = self._get_archive_type()
        self.archive = self._open_archive()

    def extract(self):
        """Extract a tar/tar.gz/zip archive into a temporary directory.

        """
        destination = tempfile.mkdtemp()
        self.archive.extractall(destination)
        return destination

    def close(self):
        """Close the archive file object.

        """
        self.archive.close()

    def get_drupal_tld(self):
        """Return the relative path to the drupal install within an archive.

        Example: archive contains: ./www/mysite/install.php with 'mysite' being
                 a valid drupal installation. This would return 'www/mysite'.

        """
        if self.filetype == 'tar':
            for member in self.archive.getmembers():
                head, tail = os.path.split(member.name)
                if tail == 'install.php':
                    return head
        elif self.filetype == 'zip':
            for member in self.archive.infolist():
                head, tail = os.path.split(member.filename)
                if tail == 'install.php':
                    return head
        postback.build_error('Error: Cannot locate drupal install in archive.')

    def _get_archive_type(self):
        """Return the generic type of archive (tar/zip).

        """
        if tarfile.is_tarfile(self.path):
            return 'tar'
        elif zipfile.is_zipfile(self.path):
            return 'zip'
        else:
            postback.build_error('Error: Not a valid tar/zip archive.')

    def _open_archive(self):
        """Return an opened archive file object.

        """
        if self.filetype == 'tar':
            return tarfile.open(self.path, 'r')
        elif self.filetype == 'zip':
            return zipfile.ZipFile(self.path, 'r')

