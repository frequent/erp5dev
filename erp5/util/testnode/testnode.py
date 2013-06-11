##############################################################################
#
# Copyright (c) 2011 Nexedi SA and Contributors. All Rights Reserved.
#
# WARNING: This program as such is intended to be used by professional
# programmers who take the whole responsibility of assessing all potential
# consequences resulting from its eventual inadequacies and bugs
# End users who are looking for a ready-to-use solution with commercial
# guarantees and support are strongly advised to contract a Free Software
# Service Company
#
# This program is Free Software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 3
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
#
##############################################################################
from datetime import datetime,timedelta
import os
import subprocess
import sys
import time
import glob
import SlapOSControler
import json
import time
import shutil
import logging
import string
import random
from ProcessManager import SubprocessError, ProcessManager, CancellationError
from subprocess import CalledProcessError
from Updater import Updater
from NodeTestSuite import NodeTestSuite, SlapOSInstance
from ScalabilityTestRunner import ScalabilityTestRunner
from UnitTestRunner import UnitTestRunner
from erp5.util import taskdistribution

DEFAULT_SLEEP_TIMEOUT = 120 # time in seconds to sleep
MAX_LOG_TIME = 15 # time in days we should keep logs that we can see through
                  # httd
MAX_TEMP_TIME = 0.01 # time in days we should keep temp files
supervisord_pid_file = None

PROFILE_PATH_KEY = 'profile_path'

class DummyLogger(object):
  def __init__(self, func):
    for name in ('trace', 'debug', 'info', 'warn', 'warning', 'error',
      'critical', 'fatal'):
       setattr(self, name, func)


def deunicodeData(data):
  if isinstance(data, list):
    new_data = []
    for sub_data in data:
      new_data.append(deunicodeData(sub_data))
  elif isinstance(data, unicode):
    new_data = data.encode('utf8')
  elif isinstance(data, dict):
    new_data = {}
    for key, value in data.iteritems():
      key = deunicodeData(key)
      value = deunicodeData(value)
      new_data[key] = value
  elif isinstance(data, int):
    new_data = data
  return new_data



class TestNode(object):

  def __init__(self, log, config, max_log_time=MAX_LOG_TIME,
               max_temp_time=MAX_TEMP_TIME):
    self.testnode_log = log
    self.log = log
    self.config = config or {}
    self.process_manager = ProcessManager(log)
    self.working_directory = config['working_directory']
    self.node_test_suite_dict = {}
    self.file_handler = None
    self.max_log_time = max_log_time
    self.max_temp_time = max_temp_time
    self.url_access = "https://[0::0]:0123" # Ipv6 + port of the node


  def checkOldTestSuite(self,test_suite_data):
    config = self.config
    installed_reference_set = set(os.listdir(self.working_directory))
    wished_reference_set = set([x['test_suite_reference'] for x in test_suite_data])
    to_remove_reference_set = installed_reference_set.difference(
                                 wished_reference_set)
    for y in to_remove_reference_set:
      fpath = os.path.join(self.working_directory,y)
      self.delNodeTestSuite(y)
      self.log("testnode.checkOldTestSuite, DELETING : %r" % (fpath,))
      if os.path.isdir(fpath):
       shutil.rmtree(fpath)
      else:
       os.remove(fpath)
  
  def getNodeTestSuite(self, reference):
    node_test_suite = self.node_test_suite_dict.get(reference)
    if node_test_suite is None:
      node_test_suite = NodeTestSuite(reference)
      self.node_test_suite_dict[reference] = node_test_suite
    return node_test_suite

  def delNodeTestSuite(self, reference):
    if self.node_test_suite_dict.has_key(reference):
      self.node_test_suite_dict.pop(reference)

  def _dealShebang(self,run_test_suite_path):
    line = open(run_test_suite_path, 'r').readline()
    invocation_list = []
    if line[:2] == '#!':
      invocation_list = line[2:].split()
    return invocation_list

  def constructProfile(self, node_test_suite, use_relative_path=False):
    config = self.config
    profile_content = ''
    assert len(node_test_suite.vcs_repository_list), "we must have at least one repository"
    profile_path_count = 0
    profile_content_list = []
    for vcs_repository in node_test_suite.vcs_repository_list:
      url = vcs_repository['url']
      buildout_section_id = vcs_repository.get('buildout_section_id', None)
      repository_path = vcs_repository['repository_path']
      try:
        profile_path = vcs_repository[PROFILE_PATH_KEY]
      except KeyError:
        pass
      else:
        profile_path_count += 1
        if profile_path_count > 1:
          raise ValueError(PROFILE_PATH_KEY + ' defined more than once')

        # Absolute path to relative path
        software_config_path = os.path.join(repository_path, profile_path)
        if use_relative_path :
          from_path = os.path.join(self.working_directory,
                                    node_test_suite.reference)
          software_config_path = os.path.relpath(software_config_path, from_path)


        profile_content_list.append("""
[buildout]
extends = %(software_config_path)s
""" %  {'software_config_path': software_config_path})

      # Construct sections
      if not(buildout_section_id is None):
        # Absolute path to relative
        if use_relative_path:
          from_path = os.path.join(self.working_directory,
                                    node_test_suite.reference)
          repository_path = os.path.relpath(repository_path, from_path)

        profile_content_list.append("""
[%(buildout_section_id)s]
repository = %(repository_path)s
branch = %(branch)s
""" %  {'buildout_section_id': buildout_section_id,
   'repository_path' : repository_path,
   'branch' : vcs_repository.get('branch','master')})
    if not profile_path_count:
      raise ValueError(PROFILE_PATH_KEY + ' not defined')
    # Write file
    custom_profile = open(node_test_suite.custom_profile_path, 'w')
    # sort to have buildout section first
    profile_content_list.sort(key=lambda x: [x, ''][x.startswith('\n[buildout]')])
    custom_profile.write(''.join(profile_content_list))
    custom_profile.close()
    sys.path.append(repository_path)

  def getAndUpdateFullRevisionList(self, node_test_suite):
    full_revision_list = []
    config = self.config
    log = self.log
    for vcs_repository in node_test_suite.vcs_repository_list:
      repository_path = vcs_repository['repository_path']
      repository_id = vcs_repository['repository_id']
      branch = vcs_repository.get('branch')
      if not os.path.exists(repository_path):
        parameter_list = [config['git_binary'], 'clone',
                          vcs_repository['url']]
        if branch is not None:
          parameter_list.extend(['-b', branch])
        parameter_list.append(repository_path)
        log(subprocess.check_output(parameter_list, stderr=subprocess.STDOUT))
      # Make sure we have local repository
      updater = Updater(repository_path, git_binary=config['git_binary'],
         branch=branch, log=log, process_manager=self.process_manager)
      updater.checkout()
      revision = "-".join(updater.getRevision())
      full_revision_list.append('%s=%s' % (repository_id, revision))
    node_test_suite.revision = ','.join(full_revision_list)
    return full_revision_list

  def registerSuiteLog(self, test_result, node_test_suite):
    """
      Create a log dedicated for the test suite,
      and register the url to master node.
    """
    suite_log_path, folder_id = node_test_suite.createSuiteLog()
    self._initializeSuiteLog(suite_log_path)
    # TODO make the path into url
    test_result.reportStatus('LOG url', "%s/%s" % (self.config.get('httpd_url'),
                             folder_id), '')
    self.log("going to switch to log %r" % suite_log_path)
    self.process_manager.log = self.log = self.getSuiteLog()
    return suite_log_path

  def getSuiteLog(self):
    return self.suite_log

  def _initializeSuiteLog(self, suite_log_path):
    # remove previous handlers
    logger = logging.getLogger('testsuite')
    if self.file_handler is not None:
      logger.removeHandler(self.file_handler)
    # and replace it with new handler
    logger_format = '%(asctime)s %(name)-13s: %(levelname)-8s %(message)s'
    formatter = logging.Formatter(logger_format)
    logging.basicConfig(level=logging.INFO, format=logger_format)
    self.file_handler = logging.FileHandler(filename=suite_log_path)
    self.file_handler.setFormatter(formatter)
    logger.addHandler(self.file_handler)
    logger.info('Activated logfile %r output' % suite_log_path)
    self.suite_log = logger.info

  def checkRevision(self, test_result, node_test_suite):
    config = self.config
    log = self.log
    if log is None:
      log = self.log
    if node_test_suite.revision != test_result.revision:
     log('Disagreement on tested revision, checking out: %r' % (
          (node_test_suite.revision,test_result.revision),))
     for i, repository_revision in enumerate(test_result.revision.split(',')):
      vcs_repository = node_test_suite.vcs_repository_list[i]
      repository_path = vcs_repository['repository_path']
      revision = repository_revision.rsplit('-', 1)[1]
      # other testnodes on other boxes are already ready to test another
      # revision
      log('  %s at %s' % (repository_path, node_test_suite.revision))
      updater = Updater(repository_path, git_binary=config['git_binary'],
                        revision=revision, log=log,
                        process_manager=self.process_manager)
      updater.checkout()
      node_test_suite.revision = test_result.revision

  def _cleanupLog(self):
    config = self.config
    log_directory = self.config['log_directory']
    now = time.time()
    for log_folder in os.listdir(log_directory):
      folder_path = os.path.join(log_directory, log_folder)
      if os.path.isdir(folder_path):
        if (now - os.stat(folder_path).st_mtime)/86400 > self.max_log_time:
          self.log("deleting log directory %r" % (folder_path,))
          shutil.rmtree(folder_path)

  def _cleanupTemporaryFiles(self):
    """
    buildout seems letting files under /tmp. To avoid regular error of
    missing disk space, remove old logs
    """
    temp_directory = self.config["system_temp_folder"]
    now = time.time()
    user_id = os.geteuid()
    for temp_folder in os.listdir(temp_directory):
      folder_path = os.path.join(temp_directory, temp_folder)
      if (temp_folder.startswith("tmp") or
          temp_folder.startswith("buildout")):
        try:
          stat = os.stat(folder_path)
          if stat.st_uid == user_id and \
              (now - stat.st_mtime)/86400 > self.max_temp_time:
            self.log("deleting temp directory %r" % (folder_path,))
            if os.path.isdir(folder_path):
              shutil.rmtree(folder_path)
            else:
              os.remove(folder_path)
        except OSError:
          self.log("_cleanupTemporaryFiles exception", exc_info=sys.exc_info())

  def cleanUp(self,test_result):
    log = self.log
    log('Testnode.cleanUp')
    self.process_manager.killPreviousRun()
    self._cleanupLog()
    self._cleanupTemporaryFiles()

  def run(self, my_type_test=None):
    
    ## BLOCK OK
    log = self.log
    config = self.config
    slapgrid = None
    previous_revision_dict = {}
    revision_dict = {}
    test_result = None
    test_node_slapos = SlapOSInstance()
    test_node_slapos.edit(working_directory=self.config['slapos_directory'])
    ## /BLOCK OK

    try:
      while True:
        try:
          
          ##BLOCK OK
          node_test_suite = None
          self.log = self.process_manager.log = self.testnode_log
          self.cleanUp(None)
          remote_test_result_needs_cleanup = False
          begin = time.time()
          portal_url = config['test_suite_master_url']
          portal = taskdistribution.TaskDistributionTool(portal_url, logger=DummyLogger(log))
          test_suite_portal = taskdistribution.TaskDistributor(portal_url, logger=DummyLogger(log))
          self.test_suite_portal = test_suite_portal
          test_suite_portal.subscribeNode(config['test_node_title'], config['computer_id'])        
          test_suite_json =  test_suite_portal.startTestSuite(config['test_node_title'])
          test_suite_data = deunicodeData(json.loads(test_suite_json))
          log("Got following test suite data from master : %r" % \
              (test_suite_data,))
          ##/BLOCK OK
          
          if my_type_test == None:
            # TODO : implement this method for each distributor
            # into nexedi/master-erp5..
            # (just UnitTestDistributor should be sufficient)
            try:
              my_type_test = test_suite_portal.getTestType()
            except:
              log("testnode, error during requesting getTestType() method \
from the distributor.")
              raise NotImplementedError

          # Select runner according to the test type
          if my_type_test == 'UnitTest':
            runner = UnitTestRunner(self)
          elif my_type_test == 'ScalabilityTest':
            runner = ScalabilityTestRunner(self)
          else:
            log("testnode, Runner type not implemented.", my_type_test)
            raise NotImplementedError
          log("Current test is a : %s" %(my_type_test,))
          
          
          # difference master/slave
          # master get test_suites, slave get nothing
          if len(test_suite_data) > 1:
            runner.prepareSlapOSForTestNode(test_node_slapos)
            #Clean-up test suites
            self.checkOldTestSuite(test_suite_data)
          
          for test_suite in test_suite_data:
                      
            ## BLOCK OK
            remote_test_result_needs_cleanup = False
            node_test_suite = self.getNodeTestSuite(
               test_suite["test_suite_reference"])
            node_test_suite.edit(
               working_directory=self.config['working_directory'],
               log_directory=self.config['log_directory'])
            node_test_suite.edit(**test_suite)
            run_software = True
            # Write our own software.cfg to use the local repository
            self.constructProfile(node_test_suite, runner.getRelativePathUsage())
            # kill processes from previous loop if any
            self.process_manager.killPreviousRun()
            self.getAndUpdateFullRevisionList(node_test_suite)
            # Make sure we have local repository
            test_result = portal.createTestResult(node_test_suite.revision, [],
                     config['test_node_title'], False,
                     node_test_suite.test_suite_title,
                     node_test_suite.project_title)
            remote_test_result_needs_cleanup = True
            log("testnode, test_result : %r" % (test_result, ))
            ## /BLOCK OK
            
            if test_result is not None:

              ## BLOCK OK
              self.registerSuiteLog(test_result, node_test_suite)
              self.checkRevision(test_result,node_test_suite)
              ## /BLOCK OK

              # Now prepare the installation of SlapOS and create instance
              status_dict = runner.prepareSlapOSForTestSuite(node_test_suite)
              
              # Give some time so computer partitions may start
              # as partitions can be of any kind we have and likely will never have
              # a reliable way to check if they are up or not ...
#              time.sleep(20)
              # For scalability test runTestSuite is a big part
              runner.runTestSuite(node_test_suite, portal_url)
              
              # break the loop to get latest priorities from master
              break
            self.cleanUp(test_result)
        except (SubprocessError, CalledProcessError) as e:
          log("SubprocessError", exc_info=sys.exc_info())
          if remote_test_result_needs_cleanup:
            status_dict = e.status_dict or {}
            test_result.reportFailure(
              command=status_dict.get('command'),
              stdout=status_dict.get('stdout'),
              stderr=status_dict.get('stderr'),
            )
          continue
        except ValueError as e:
          # This could at least happens if runTestSuite is not found
          log("ValueError", exc_info=sys.exc_info())
          if node_test_suite is not None:
            node_test_suite.retry_software_count += 1
        except CancellationError, e:
          log("CancellationError", exc_info=sys.exc_info())
          self.process_manager.under_cancellation = False
          node_test_suite.retry = True
          continue
        except:
            log("erp5testnode exception", exc_info=sys.exc_info())
            raise
        now = time.time()
        self.cleanUp(test_result)
        if (now-begin) < 120:
          sleep_time = 120 - (now-begin)
          log("End of processing, going to sleep %s" % sleep_time)
          time.sleep(sleep_time)
    except:
      log("Exception in error handling", exc_info=sys.exc_info())
    finally:
      # Nice way to kill *everything* generated by run process -- process
      # groups working only in POSIX compilant systems
      # Exceptions are swallowed during cleanup phase
      log("GENERAL EXCEPTION, QUITING")
      self.cleanUp(test_result)
      log("GENERAL EXCEPTION, QUITING, cleanup finished")
