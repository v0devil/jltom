from collections import defaultdict, OrderedDict
import json

from pandas import DataFrame
from pylab import *
import pandas as pd
import re
import sys
import os
import zipfile
from xml.etree.ElementTree import ElementTree
from os.path import basename
from controller.models import TestRunning
from analyzer.models import Project, Test, Action, \
    TestActionData, TestAggregate, TestData, \
    Server, ServerMonitoringData
reload(sys)
sys.setdefaultencoding('utf-8')


def percentile(n):
    def percentile_(x):
        return np.percentile(x, n)

    percentile_.__name__ = 'percentile_%s' % n
    return percentile_


def mask(df, f):
    return df[f(df)]


def ord_to_char(v, p=None):
    return chr(int(v))


def get_dir_size(path):
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            if not f == 'checksum':
                fp = os.path.join(dirpath, f)
                total_size += os.path.getsize(fp)
    return total_size


def zip_results_file(file):
    if os.path.exists(file + '.zip'):
        os.remove(file + '.zip')
    print "Move results file " + file + " to zip archive"
    with zipfile.ZipFile(
            file + ".zip", "w", zipfile.ZIP_DEFLATED,
            allowZip64=True) as zip_file:
        zip_file.write(file, basename(file))
    os.remove(file)
    print "File was packed, original file was deleted"


dateconv = np.vectorize(datetime.datetime.fromtimestamp)


def parse_results_in_dir(results_dir):
    id = add_running_test(results_dir)
    generate_data(id)
    running_test = TestRunning.objects.get(id=id)
    running_test.delete()
    print("Data was parsed, directory: {0}".format(results_dir))

def add_running_test(root):
    # Parse data from Jenkins Job folder
    build_xml = ElementTree()
    build_parameters = []
    display_name = "unknown"
    started_at = 0
    duration = 0
    project_id = 0
    jmeter_results_path = os.path.join(
        root, "jmeter.jtl")
    monitoring_data = os.path.join(
        root, "monitoring.data")
    build_xml_path = os.path.join(
        root, "build.xml")

    if os.path.isfile(build_xml_path):
        build_xml.parse(build_xml_path)
        build_tag = build_xml.getroot()

        for params in build_tag:
            if params.tag == 'actions':
                parameters = params.find('.//parameters')
                for parameter in parameters:
                    name = parameter.find('name')
                    value = parameter.find('value')
                    build_parameters.append(
                        [name.text, value.text])
            elif params.tag == 'startTime':
                started_at = int(params.text)
            elif params.tag == 'duration':
                duration = int(params.text)
            elif params.tag == 'displayName':
                display_name = params.text
    project_name = re.search('/([^/]+)/builds', root).group(1)
    if not Project.objects.filter(project_name=project_name).exists():
        print "Adding new project: " + project_name
        project = Project(
            project_name=project_name,
            show=True
        )
        project.save()
        project_id = project.id
    print "Project_id: " + str(project_id)
    build_number = int(
        re.search('/builds/(\d+)', root).group(1))
    running_test = TestRunning(
        project_id=project_id,
        build_number=build_number,
        result_file_dest=jmeter_results_path,
        monitoring_file_dest=monitoring_data,
        log_file_dest='',
        display_name=display_name,
        started_at=started_at,
        pid=0,
        jmeter_remote_instances=None,
        workspace=root,
        is_running=True,
        end_time=started_at+duration,
    )
    running_test.save()
    return running_test.id


def generate_data(t_id):
    print "Parse and generate test data: " + str(t_id)
    test_running = TestRunning.objects.get(id=t_id)
    if not Test.objects.filter(path=test_running.workspace).exists():
        test = Test(
            project_id=test_running.project_id,
            path=test_running.workspace,
            display_name=test_running.display_name,
            started_at=test_running.started_at,
            end_tiem=test_running.end_time,
            build_number=0,
            show=True)
        test.save()
    else:
        test = Test.objects.get(path=test_running.workspace)
    project_id = test.project_id
    test_id = test.id
    jmeter_results_file = test_running.result_file_dest
    if os.path.exists(jmeter_results_file):
        df = pd.DataFrame()
        if os.stat(jmeter_results_file).st_size > 1000007777:
            print "Executing a parse for a huge file"
            chunks = pd.read_table(
                jmeter_results_file, sep=',', index_col=0, chunksize=3000000)
            for chunk in chunks:
                chunk.columns = [
                    'response_time', 'url', 'responseCode', 'success', 'threadName',
                    'failureMessage', 'grpThreads', 'allThreads'
                ]
                chunk = chunk[~chunk['URL'].str.contains('exclude_')]
                df = df.append(chunk)
                print "Parsing a huge file,size: " + str(df.size)
        else:
            df = pd.read_csv(
                jmeter_results_file, index_col=0, low_memory=False)
            df.columns = [
                'response_time', 'url', 'responseCode', 'success', 'threadName',
                'failureMessage', 'grpThreads', 'allThreads'
            ]
            df = df[~df['url'].str.contains('exclude_', na=False)]

        df.columns = [
            'response_time', 'url', 'responseCode', 'success', 'threadName',
            'failureMessage', 'grpThreads', 'allThreads'
        ]

        #convert timestamps to normal date/time
        df.index = pd.to_datetime(dateconv((df.index.values / 1000)))
        num_lines = df['response_time'].count()
        print "Number of lines in filrue: %d." % num_lines
        unique_urls = df['url'].unique()
        for url in unique_urls:
            url = str(url)
            if not Action.objects.filter(
                    url=url, project_id=project_id).exists():
                print "Adding new action: " + str(url) + " project_id: " + str(
                    project_id)
                a = Action(url=url, project_id=project_id)
                a.save()
            a = Action.objects.get(url=url, project_id=project_id)
            action_id = a.id
            if not TestActionData.objects.filter(
                    action_id=action_id, test_id=test_id).exists():
                print "Adding action data: " + url
                df_url = df[(df.url == url)]
                url_data = pd.DataFrame()
                df_url_gr_by_ts = df_url.groupby(pd.Grouper(freq='1Min'))
                url_data['avg'] = df_url_gr_by_ts.response_time.mean()
                url_data['median'] = df_url_gr_by_ts.response_time.median()
                url_data['count'] = df_url_gr_by_ts.success.count()
                df_url_gr_by_ts_only_errors = df_url[(
                    df_url.success == False
                )].groupby(pd.Grouper(freq='1Min'))
                url_data[
                    'errors'] = df_url_gr_by_ts_only_errors.success.count()
                url_data['test_id'] = test_id
                url_data['url'] = url
                output_json = json.loads(
                    url_data.to_json(orient='index', date_format='iso'),
                    object_pairs_hook=OrderedDict)
                for row in output_json:
                    data = {
                        'timestamp': row,
                        'avg': output_json[row]['avg'],
                        'median': output_json[row]['median'],
                        'count': output_json[row]['count'],
                        'url': output_json[row]['url'],
                        'errors': output_json[row]['errors'],
                        'test_id': output_json[row]['test_id'],
                    }
                    test_action_data = TestActionData(
                        test_id=output_json[row]['test_id'],
                        action_id=action_id,
                        data=data)
                    test_action_data.save()

        zip_results_file(jmeter_results_file)

        test_overall_data = pd.DataFrame()
        df_gr_by_ts = df.groupby(pd.Grouper(freq='1Min'))
        test_overall_data['avg'] = df_gr_by_ts.response_time.mean()
        test_overall_data['median'] = df_gr_by_ts.response_time.median()
        test_overall_data['count'] = df_gr_by_ts.response_time.count()
        test_overall_data['test_id'] = test_id
        output_json = json.loads(
            test_overall_data.to_json(orient='index', date_format='iso'),
            object_pairs_hook=OrderedDict)
        for row in output_json:
            data = {
                'timestamp': row,
                'avg': output_json[row]['avg'],
                'median': output_json[row]['median'],
                'count': output_json[row]['count']
            }
            test_data = TestData(
                test_id=output_json[row]['test_id'], data=data)
            test_data.save()
    else:
        print "Result file does not exist"

    monitoring_results_file = test_running.monitoring_file_dest
    if os.path.exists(monitoring_results_file):
        f = open(monitoring_results_file, "r")
        lines = f.readlines()
        f.close()
        f = open(monitoring_results_file, "w")
        for line in lines:
            if not ('start' in line):
                f.write(line)

        f.close()
        monitoring_df = pd.read_csv(monitoring_results_file, index_col=1, sep=";")

        monitoring_df.columns = [
            'server_name', 'Memory_used', 'Memory_free', 'Memory_buff',
            'Memory_cached', 'Net_recv', 'Net_send', 'Disk_read', 'Disk_write',
            'System_la1', 'CPU_user', 'CPU_system', 'CPU_iowait'
        ]
        monitoring_df.index = pd.to_datetime(
            dateconv((monitoring_df.index.values)))
        monitoring_df.index.names = ['timestamp']

        unique_servers = monitoring_df['server_name'].unique()
        for server_ in unique_servers:
            if not Server.objects.filter(
                    server_name=server_).exists():
                print "Adding new server: " + server_
                s = Server(
                    server_name=server_
                )
                s.save()
            server_id = s.id
            if not ServerMonitoringData.objects.filter(
                    server_id=server_id,
                    test_id=test_id).exists():
                df_server = monitoring_df[(
                    monitoring_df.server_name == server_)]
                output_json = json.loads(
                    df_server.to_json(orient='index', date_format='iso'),
                    object_pairs_hook=OrderedDict)
                for row in output_json:
                    data = {
                        'timestamp': row,
                        'Memory_used': output_json[row]['Memory_used'],
                        'Memory_free': output_json[row]['Memory_free'],
                        'Memory_buff': output_json[row]['Memory_buff'],
                        'Memory_cached': output_json[row]['Memory_cached'],
                        'Net_recv': output_json[row]['Net_recv'],
                        'Net_send': output_json[row]['Net_send'],
                        'Disk_read': output_json[row]['Disk_read'],
                        'Disk_write': output_json[row]['Disk_write'],
                        'System_la1': output_json[row]['System_la1'],
                        'CPU_user': output_json[row]['CPU_user'],
                        'CPU_system': output_json[row]['CPU_system'],
                        'CPU_iowait': output_json[row]['CPU_iowait']
                    }
                    server_monitoring_data = ServerMonitoringData(
                        test_id=test_id, server_id=server_id, data=data
                    )
                    server_monitoring_data.save()
    else:
        print "Result file does not exist"

    return True
