from __future__ import print_function

import sys

if sys.hexversion < 0x02070000:
    print(70 * "*")
    print("ERROR: {0} requires python >= 2.7.x. ".format(sys.argv[0]))
    print("It appears that you are running python {0}".format(
        ".".join(str(x) for x in sys.version_info[0:3])))
    print(70 * "*")
    sys.exit(1)

# import core python modules
import errno
import glob
import itertools
import os
import re
import shutil
import traceback

# import modules installed by pip into virtualenv
import jinja2

# import the helper utility module
from cesm_utils import cesmEnvLib
from diag_utils import diagUtilsLib

# import the MPI related modules
from asaptools import partition, simplecomm, vprinter, timekeeper

# import the diag baseclass module
from ocn_diags_bc import OceanDiagnostic

# import the plot classes
from diagnostics.ocn.Plots import ocn_diags_plot_bc
from diagnostics.ocn.Plots import ocn_diags_plot_factory

class modelTimeseries(OceanDiagnostic):
    """model timeserieservations ocean diagnostics setup
    """
    def __init__(self):
        """ initialize
        """
        super(modelTimeseries, self).__init__()

        self._name = 'MODEL_TIMESERIES'
        self._title = 'Model Timeseries'

    def check_prerequisites(self, env):
        """ check prerequisites
        """
        print("  Checking prerequisites for : {0}".format(self.__class__.__name__))
        super(modelTimeseries, self).check_prerequisites(env)

        # clean out the old working plot files from the workdir
        if env['CLEANUP_FILES'].upper() in ['T','TRUE']:
            cesmEnvLib.purge(env['WORKDIR'], '.*\.pro')
            cesmEnvLib.purge(env['WORKDIR'], '.*\.gif')
            cesmEnvLib.purge(env['WORKDIR'], '.*\.dat')
            cesmEnvLib.purge(env['WORKDIR'], '.*\.ps')
            cesmEnvLib.purge(env['WORKDIR'], '.*\.png')
            cesmEnvLib.purge(env['WORKDIR'], '.*\.html')
            cesmEnvLib.purge(env['WORKDIR'], '.*\.log\.*')
            cesmEnvLib.purge(env['WORKDIR'], '.*\.pop\.d.\.*')

        # create the plot.dat file in the workdir used by all NCL plotting routines
        diagUtilsLib.create_plot_dat(env['WORKDIR'], env['XYRANGE'], env['DEPTHS'])

        # set the OBSROOT 
        env['OBSROOT'] = env['OBSROOTPATH']

        # check the resolution and decide if some plot modules should be turned off
        if env['RESOLUTION'] == 'tx0.1v2' :
            env['MTS_PM_MOCANN'] = os.environ['PM_MOCANN'] = 'FALSE'
            env['MTS_PM_MOCMON'] = os.environ['PM_MOCMON'] = 'FALSE'

        # check if cpl log file path is defined
        if len(env['CPLLOGFILEPATH']) == 0:
            # print a message that the cpl log path isn't defined and turn off a couple of plot mods
            print('model timeseries - CPLLOGFILEPATH is undefined. Disabling MTS_PM_CPLLOG module')
            env['MTS_PM_CPLLOG'] = os.environ['PM_CPLLOG'] = 'FALSE'

        else:
            # check that cpl log files exist
            cpllogs = list()
            cpllogs = glob.glob('{0}/cpl.log.*'.format(env['CPLLOGFILEPATH']))
            if len(cpllogs) > 0:
                for cpllog in cpllogs:
                    shutil.copy2('{0}/{1}'.format(env['CPLLOGFILEPATH'], cpllog), '{0}/{1}'.format(env['WORKDIR'], cpllog))
                    # gunzip the cpllog
                    if cpllog.lower().find('.gz') != -1:
                        cpllog_gunzip = cpllog[:-3]
                        inFile = gzip.open(cpllog, 'rb')
                        outFile = open('{0}/{1}'.format(env['WORKDIR'],cpllog_gunzip), 'wb')
                        outFile.write( inFile.read() )
                        inFile.close()
                        outFile.close()
            else:
                print('model timeseries - Coupler logs do not exist. Disabling MTS_PM_CPLLOG module')
                env['MTS_PM_CPLLOG'] = os.environ['PM_CPLLOG'] = 'FALSE'

            
        return env

    def run_diagnostics(self, env, scomm):
        """ call the necessary plotting routines to generate diagnostics plots
        """
        super(modelTimeseries, self).run_diagnostics(env, scomm)
        scomm.sync()

        # setup some global variables
        requested_plots = list()
        local_requested_plots = list()
        local_html_list = list()

        # define the templatePath for all tasks
        templatePath = '{0}/diagnostics/diagnostics/ocn/Templates'.format(env['POSTPROCESS_PATH']) 

        # all the plot module XML vars start with MVO_PM_  need to strip that off
        for key, value in env.iteritems():
            if (re.search("\AMTS_PM_", key) and value.upper() in ['T','TRUE']):
                k = key[4:]                
                requested_plots.append(k)

        scomm.sync()
        print('model timeseries - after scomm.sync requested_plots = {0}'.format(requested_plots))

        if scomm.is_manager():
            print('model timeseries - User requested plot modules:')
            for plot in requested_plots:
                print('  {0}'.format(plot))

            if env['DOWEB'].upper() in ['T','TRUE']:
                
                print('model timeseries - Creating plot html header')
                templateLoader = jinja2.FileSystemLoader( searchpath=templatePath )
                templateEnv = jinja2.Environment( loader=templateLoader )
                
                template_file = 'model_timeseries.tmpl'
                template = templateEnv.get_template( template_file )
    
                # test the template variables
                templateVars = { 'casename' : env['CASE'],
                                 'tagname' : env['CCSM_REPOTAG'],
                                 'start_year' : env['YEAR0'],
                                 'stop_year' : env['YEAR1']
                                 }

                print('model timeseries - Rendering plot html header')
                plot_html = template.render( templateVars )

        scomm.sync()

        print('model timeseries - Partition requested plots')
        # partition requested plots to all tasks
        local_requested_plots = scomm.partition(requested_plots, func=partition.EqualStride(), involved=True)
        scomm.sync()

        for requested_plot in local_requested_plots:
            try:
                plot = ocn_diags_plot_factory.oceanDiagnosticPlotFactory('timeseries', requested_plot)

                print('model timeseries - Checking prerequisite for {0} on rank {1}'.format(plot.__class__.__name__, scomm.get_rank()))
                plot.check_prerequisites(env)

                print('model timeseries - Generating plots for {0} on rank {1}'.format(plot.__class__.__name__, scomm.get_rank()))
                plot.generate_plots(env)

                print('model timeseries - Converting plots for {0} on rank {1}'.format(plot.__class__.__name__, scomm.get_rank()))
                plot.convert_plots(env['WORKDIR'], env['IMAGEFORMAT'])

                print('model timeseries - Creating HTML for {0} on rank {1}'.format(plot.__class__.__name__, scomm.get_rank()))
                html = plot.get_html(env['WORKDIR'], templatePath, env['IMAGEFORMAT'])
            
                local_html_list.append(str(html))
                #print('local_html_list = {0}'.format(local_html_list))

            except ocn_diags_plot_bc.RecoverableError as e:
                # catch all recoverable errors, print a message and continue.
                print(e)
                print("model timeseries - Skipped '{0}' and continuing!".format(request_plot))
            except RuntimeError as e:
                # unrecoverable error, bail!
                print(e)
                return 1

        scomm.sync()

        # define a tag for the MPI collection of all local_html_list variables
        html_msg_tag = 1

        all_html = list()
        all_html = [local_html_list]
        if scomm.get_size() > 1:
            if scomm.is_manager():
                all_html  = [local_html_list]
                
                for n in range(1,scomm.get_size()):
                    rank, temp_html = scomm.collect(tag=html_msg_tag)
                    all_html.append(temp_html)

                #print('all_html = {0}'.format(all_html))
            else:
                return_code = scomm.collect(data=local_html_list, tag=html_msg_tag)

        scomm.sync()
        
        if scomm.is_manager():

            # merge the all_html list of lists into a single list
            all_html = list(itertools.chain.from_iterable(all_html))
            for each_html in all_html:
                #print('each_html = {0}'.format(each_html))
                plot_html += each_html

            print('model timeseries - Adding footer html')
            with open('{0}/footer.tmpl'.format(templatePath), 'r+') as tmpl:
                plot_html += tmpl.read()

            print('model timeseries - Writing plot index.html')
            with open( '{0}/index.html'.format(env['WORKDIR']), 'w') as index:
                index.write(plot_html)

            if len(env['WEBDIR']) > 0 and len(env['WEBHOST']) > 0 and len(env['WEBLOGIN']) > 0:
                # copy over the files to a remote web server and webdir 
                diagUtilsLib.copy_html_files(env, 'model_vs_obs')
            else:
                print('model timeseries - Web files successfully created in directory:')
                print('{0}'.format(env['WORKDIR']))
                print('The env_diags_ocn.xml variable WEBDIR, WEBHOST, and WEBLOGIN were not set.')
                print('You will need to manually copy the web files to a remote web server.')

            print('**************************************************************************')
            print('Successfully completed generating ocean diagnostics model timeseries plots')
            print('**************************************************************************')

