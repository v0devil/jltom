import csv
import json
import logging
import itertools
import os
import re
import sys
import zipfile
import tempfile
import threading
from collections import OrderedDict, defaultdict
from os.path import basename
from xml.etree.ElementTree import ElementTree

import pandas as pd
from pandas import DataFrame
from pylab import *

from ltc.analyzer.models import (
    Action, Server, ServerMonitoringData,
    TestActionAggregateData, TestActionData,
    TestData, TestDataResolution
)
from ltc.base.models import (
    Project, Test
)
from ltc.controller.models import TestRunning
logger = logging.getLogger('django')


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
    logger.info("Move results file " + file + " to zip archive")
    with zipfile.ZipFile(
            file + ".zip", "w", zipfile.ZIP_DEFLATED,
            allowZip64=True) as zip_file:
        zip_file.write(file, basename(file))
    os.remove(file)
    logger.info("File was packed, original file was deleted")


def zip_dir(dirPath, zipPath):
    zipf = zipfile.ZipFile(zipPath, mode='w', allowZip64=True)
    lenDirPath = len(dirPath)
    for root, _, files in os.walk(dirPath):
        for file in files:
            filePath = os.path.join(root, file)
            zipf.write(filePath, filePath[lenDirPath:])
    zipf.close()


dateconv = np.vectorize(datetime.datetime.fromtimestamp)


def parse_results_in_dir(results_dir):
    id = add_running_test(results_dir)
    generate_data(id)
    running_test = TestRunning.objects.get(id=id)
    running_test.delete()
    logger.info("Data was parsed, directory: {0}".format(results_dir))


def add_running_test(root):
    # Parse data from Jenkins Job folder
    build_xml = ElementTree()
    build_parameters = []
    display_name = "unknown"
    started_at = 0
    duration = 0
    project_id = 0
    jmeter_results_path = os.path.join(root, "jmeter.jtl")
    monitoring_data = os.path.join(root, "monitoring.data")
    build_xml_path = os.path.join(root, "build.xml")

    if os.path.isfile(build_xml_path):
        build_xml.parse(build_xml_path)
        build_tag = build_xml.getroot()

        for params in build_tag:
            if params.tag == 'actions':
                parameters = params.find('.//parameters')
                for parameter in parameters:
                    name = parameter.find('name')
                    value = parameter.find('value')
                    build_parameters.append([name.text, value.text])
            elif params.tag == 'startTime':
                started_at = int(params.text)
            elif params.tag == 'duration':
                duration = int(params.text)
            elif params.tag == 'displayName':
                display_name = params.text
    project_name = re.search('/([^/]+)/builds', root).group(1)
    if not Project.objects.filter(project_name=project_name).exists():
        project = Project(project_name=project_name, show=True)
        project.save()
        project_id = project.id
    build_number = int(re.search('/builds/(\d+)', root).group(1))
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
        end_time=started_at + duration, )
    running_test.save()
    return running_test.id


def unpack_test_results_data(test_id):
    '''Un-pack Jmeter result file if exists'''

    test_path = Test.objects.get(id=test_id).path
    jmeter_results_file_path = os.path.join(test_path, 'jmeter.jtl')
    if not os.path.exists(jmeter_results_file_path):
        logger.info("Results file does not exists, try to check archive")
        jmeter_results_zip = jmeter_results_file_path + ".zip"
        if os.path.exists(jmeter_results_zip):
            logger.info("Archive file was found: " + jmeter_results_zip)
            with zipfile.ZipFile(jmeter_results_zip, "r") as z:
                z.extractall(test_path)
    return jmeter_results_file_path


def generate_test_results_data(test_id,
                               project_id,
                               jmeter_results_file_path='',
                               monitoring_results_file_path='',
                               jmeter_results_file_fields=[],
                               monitoring_results_file_fields=[],
                               data_resolution='1Min',
                               mode=''):

    data_resolution_id = TestDataResolution.objects.get(
        frequency=data_resolution).id
    if not jmeter_results_file_fields:
        jmeter_results_file_fields = [
            'response_time', 'url', 'responseCode', 'success', 'threadName',
            'failureMessage', 'grpThreads', 'allThreads'
        ]
    if not monitoring_results_file_fields:
        monitoring_results_file_fields = [
            'server_name', 'Memory_used', 'Memory_free', 'Memory_buff',
            'Memory_cached', 'Net_recv', 'Net_send', 'Disk_read', 'Disk_write',
            'System_la1', 'CPU_user', 'CPU_system', 'CPU_iowait'
        ]
    jmeter_results_file = jmeter_results_file_path
    if os.path.exists(jmeter_results_file):
        df = pd.DataFrame()
        if os.stat(jmeter_results_file).st_size > 1000007777:
            logger.debug("Executing a parse for a huge file")
            chunks = pd.read_table(
                jmeter_results_file, sep=',', index_col=0, chunksize=3000000)
            for chunk in chunks:
                chunk.columns = jmeter_results_file_fields.split(',')
                chunk = chunk[~chunk['URL'].str.contains('exclude_')]
                df = df.append(chunk)
        else:
            df = pd.read_csv(
                jmeter_results_file, index_col=0, low_memory=False)
            df.columns = jmeter_results_file_fields
            df = df[~df['url'].str.contains('exclude_', na=False)]

        # If gather data "online" just clean result file
        zip_results_file(jmeter_results_file)

        df.columns = jmeter_results_file_fields
        df.index = pd.to_datetime(dateconv((df.index.values / 1000)))
        num_lines = df['response_time'].count()
        logger.debug('Number of lines in file: {}'.format(num_lines))
        unique_urls = df['url'].unique()
        for url in unique_urls:
            url = str(url)
            if not Action.objects.filter(
                    url=url, project_id=project_id).exists():
                logger.debug("Adding new action: " + str(url) + " project_id: "
                             + str(project_id))
                a = Action(url=url, project_id=project_id)
                a.save()
            a = Action.objects.get(url=url, project_id=project_id)
            action_id = a.id
            if not TestActionData.objects.filter(
                    action_id=action_id,
                    test_id=test_id,
                    data_resolution_id=data_resolution_id).exists():
                logger.debug("Adding action data: {}".format(url))
                df_url = df[(df.url == url)]
                url_data = pd.DataFrame()
                df_url_gr_by_ts = df_url.groupby(
                    pd.Grouper(freq=data_resolution))
                url_data['avg'] = df_url_gr_by_ts.response_time.mean()
                url_data['median'] = df_url_gr_by_ts.response_time.median()
                url_data['count'] = df_url_gr_by_ts.success.count()
                df_url_gr_by_ts_only_errors = df_url[(
                    df_url.success == False
                )].groupby(pd.Grouper(freq=data_resolution))
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
                        data_resolution_id=data_resolution_id,
                        data=data)
                    test_action_data.save()
                if not TestActionAggregateData.objects.filter(
                        action_id=action_id, test_id=test_id).exists():
                    url_agg_data = dict(
                        json.loads(df_url['response_time'].describe()
                                   .to_json()))
                    url_agg_data['99%'] = float(df_url['response_time'].quantile(.99))
                    url_agg_data['90%'] = float(df_url['response_time'].quantile(.90))
                    url_agg_data['weight'] = float(
                        df_url['response_time'].sum())
                    url_agg_data['errors'] = float(df_url[(
                        df_url['success'] == False)]['success'].count())
                    print(url_agg_data)
                    test_action_aggregate_data = TestActionAggregateData(
                        test_id=test_id,
                        action_id=action_id,
                        data=url_agg_data)
                    test_action_aggregate_data.save()

        if not TestData.objects.filter(
                test_id=test_id,
                data_resolution_id=data_resolution_id).exists():
            test_overall_data = pd.DataFrame()
            df_gr_by_ts = df.groupby(pd.Grouper(freq=data_resolution))
            test_overall_data['avg'] = df_gr_by_ts.response_time.mean()
            test_overall_data['median'] = df_gr_by_ts.response_time.median()
            test_overall_data['count'] = df_gr_by_ts.response_time.count()
            test_overall_data['test_id'] = test_id
            output_json = json.loads(
                test_overall_data.to_json(orient='index',
                                          date_format='iso'),
                object_pairs_hook=OrderedDict)
            for row in output_json:
                data = {
                    'timestamp': row,
                    'avg': output_json[row]['avg'],
                    'median': output_json[row]['median'],
                    'count': output_json[row]['count']
                }
                test_data = TestData(
                    test_id=output_json[row]['test_id'],
                    data_resolution_id=data_resolution_id,
                    data=data
                )
                test_data.save()
    monitoring_results_file = monitoring_results_file_path
    if os.path.exists(monitoring_results_file):
        f = open(monitoring_results_file, "r")
        lines = f.readlines()
        f.close()
        f = open(monitoring_results_file, "w")
        for line in lines:
            if not ('start' in line):
                f.write(line)

        f.close()
        monitoring_df = pd.read_csv(
            monitoring_results_file, index_col=1, sep=";")
        monitoring_df.columns = monitoring_results_file_fields
        monitoring_df.index = pd.to_datetime(
            dateconv((monitoring_df.index.values)))
        monitoring_df.index.names = ['timestamp']

        unique_servers = monitoring_df['server_name'].unique()
        for server_ in unique_servers:
            if not Server.objects.filter(server_name=server_).exists():
                s = Server(server_name=server_)
                s.save()
            server_id = s.id
            if not ServerMonitoringData.objects.filter(
                    server_id=server_id,
                    test_id=test_id,
                    data_resolution_id=data_resolution_id).exists():
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
                        test_id=test_id,
                        data_resolution_id=data_resolution_id,
                        server_id=server_id,
                        data=data)
                    server_monitoring_data.save()
    else:
        logger.info("Result file does not exist")


def generate_data(t_id, mode=''):
    logger.info("Parse and generate test data: {}".format(t_id))
    test_running = TestRunning.objects.get(id=t_id)
    if not Test.objects.filter(path=test_running.build_path).exists():
        end_time = test_running.started_at + 1
        build_number = 0
        display_name = ''
        if test_running.duration:
            end_time = test_running.started_at + test_running.duration
        if test_running.build_number:
            build_number = test_running.build_number
        test = Test(
            project_id=test_running.project_id,
            path=test_running.build_path,
            display_name=test_running.display_name,
            started_at=test_running.started_at,
            end_time=end_time,
            build_number=build_number,
            show=True)
        test.save()
    else:
        test = Test.objects.get(path=test_running.build_path)
    project_id = test.project_id
    jmeter_results_file_path = test_running.result_file_dest
    monitoring_results_file_path = test_running.monitoring_file_dest
    logger.info('[DAEMON] Starting generate function.')
    if mode == 'online':
        daemon_generate_data(test,
                                    project_id,
                                    jmeter_results_file_path,
                                    monitoring_results_file_path,
                                    mode=mode,)
    else:
        generate_test_results_data(test_id,
                                project_id,
                                jmeter_results_file_path,
                                monitoring_results_file_path)
    return True


def daemon_generate_data(test,
                                  project_id,
                                  jmeter_results_file_path='',
                                  monitoring_results_file_path='',
                                  jmeter_results_file_fields=[],
                                  monitoring_results_file_fields=[],
                                  data_resolution='1Min',
                                  mode=''):
    test_id = test.id
    if not jmeter_results_file_fields:
        jmeter_results_file_fields = [
            'timestamp', 'response_time', 'url', 'responseCode', 'success', 'threadName',
            'failureMessage', 'grpThreads', 'allThreads'
        ]
    if not monitoring_results_file_fields:
        monitoring_results_file_fields = [
            'server_name', 'Memory_used', 'Memory_free', 'Memory_buff',
            'Memory_cached', 'Net_recv', 'Net_send', 'Disk_read', 'Disk_write',
            'System_la1', 'CPU_user', 'CPU_system', 'CPU_iowait'
        ]
    jmeter_results_file = jmeter_results_file_path
    check_read = True
    temp_path = os.path.join('/tmp/', str(test.project), str(test_id))
    temp_to_parse_path = os.path.join(temp_path, 'to_parse')
    try:
        os.makedirs(temp_path)
        os.makedirs(temp_to_parse_path)
    except OSError:
        logger.info('[DAEMON] Dirs are already exists.')
    rows = []
    logger.info('[DAEMON] Reading data from main result file {}.'.format(jmeter_results_file))
    with open(jmeter_results_file, 'r+') as f:
        rows = f.readlines()
        logger.info('[DAEMON] Cleaning main result file.')
        f.seek(0)
        f.truncate(0)
        f.writelines(rows[-1])
        logger.info('[DAEMON] Cleaned.')
    rows_num = len(rows)
    logger.info('[DAEMON] Rows {}.'.format(rows_num))
    fd, temp_result_filename = tempfile.mkstemp('.jtl',
                            '{}_main_'.format(test.project), temp_path)
    open(temp_result_filename, 'w').writelines(rows[0:rows_num]) # avoid last line
    logger.info('[DAEMON] Check file {}.'.format(temp_result_filename))
    if os.path.exists(temp_result_filename):
        logger.info('[DAEMON] Starting work with {}.'.format(temp_result_filename))
        unique_urls = []
        df = pd.DataFrame()
        n = 0
        minutes_data = {}
        with open(temp_result_filename) as f:
            for r in f.readlines():
                row = r.split(',')
                if len(row[0]) == 13:
                    ts_c = int(row[0])
                    dt_c = datetime.datetime.fromtimestamp(ts_c/1000)
                    minutes_data.setdefault(dt_c.strftime('%Y_%m_%d_%H_%M'), []).append(r)
                n += 1
        logger.info('[DAEMON] Removing temp result file {}.'.format(temp_result_filename))
        os.remove(temp_result_filename)
        for key, value in minutes_data.iteritems():
            temp_ts_file = os.path.join(temp_to_parse_path, key)
            logger.info('[DAEMON] Writing temp data {}.'.format(temp_ts_file))
            open(temp_ts_file, 'a+').writelines(value)
        # Iterate files and check if they were not modified last minute,
        # parse it and insert to DB
        logger.info('[DAEMON] Check modification time of result files.')
        files_to_parse = []
        threads = []
        for filename in os.listdir(temp_to_parse_path):
            data_file = os.path.join(temp_to_parse_path, filename)
            file_mod_time = os.stat(data_file).st_mtime
            last_time = (time.time() - file_mod_time)
            if last_time > 60:
                logger.info('[DAEMON] File {} was not modified since 1min, adding to parse list.'.format(data_file))
                files_to_parse.append(data_file)
        for f in files_to_parse:
            logger.info('[DAEMON THREAD] Parse {}.'.format(f))
            t = threading.Thread(
            target=parse_csv_data, args=(
                f,
                jmeter_results_file_fields, test, data_resolution))
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
    else:
        logger.info("Result file does not exist")


def parse_csv_data(data_file, csv_file_fields, test, data_resolution):
    df = pd.read_csv(
        data_file,
        index_col=0,
        low_memory=False,
        names=csv_file_fields,
    )
    project_id = test.project.id
    test_id = test.id
    data_resolution_id = TestDataResolution.objects.get(frequency=data_resolution).id
    logger.info('[DAEMON] Removing {}.'.format(data_file))
    os.remove(data_file)
    logger.info('[DAEMON] File {} was removed.'.format(data_file))
    df.dropna(inplace=True)
    df = df[~df['url'].str.contains('exclude_', na=False)]
    df.index = pd.to_datetime(dateconv((df.index.values / 1000)))
    unique_urls = df['url'].unique()
    for url in unique_urls:
        url = str(url)
        if not Action.objects.filter(
                url=url, project_id=project_id).exists():
            a = Action(url=url, project_id=project_id)
            a.save()
        logger.info("[DAEMON] Adding action data: {}".format(url))
        a = Action.objects.get(url=url, project_id=project_id)
        action_id = a.id
        df_url = df[(df.url == url)]
        url_data = pd.DataFrame()
        df_url_gr_by_ts = df_url.groupby(
            pd.Grouper(freq=data_resolution))
        url_data['avg'] = df_url_gr_by_ts.response_time.mean()
        url_data['median'] = df_url_gr_by_ts.response_time.median()
        url_data['count'] = df_url_gr_by_ts.success.count()
        df_url_gr_by_ts_only_errors = df_url[(
            df_url.success == False
        )].groupby(pd.Grouper(freq=data_resolution))
        url_data[
            'errors'] = df_url_gr_by_ts_only_errors.success.count()
        url_data['test_id'] = test_id
        url_data['url'] = url
        output_json = json.loads(
            url_data.to_json(orient='index', date_format='iso'),
            object_pairs_hook=OrderedDict)
        for row in output_json:
            logger.info('[DAEMON] {} {}'.format(url, row))
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
                data_resolution_id=data_resolution_id,
                data=data)
            test_action_data.save()
        logger.info('[DAEMON] Check aggregate data: {}'.format(url))
        url_agg_data = dict(
        json.loads(
            df_url['response_time'].describe().to_json()))
        url_agg_data['99%'] = df_url['response_time'].quantile(.99)
        url_agg_data['90%'] = df_url['response_time'].quantile(.90)
        url_agg_data['weight'] = float(
            df_url['response_time'].sum())
        url_agg_data['errors'] = float(df_url[(
            df_url['success'] == False)]['success'].count())
        if not TestActionAggregateData.objects.filter(action_id=action_id,
                                                        test_id=test_id).exists():
            logger.info('[DAEMON] Adding new aggregate data.')
            test_action_aggregate_data = TestActionAggregateData(
                                                test_id=test_id,
                                                action_id=action_id,
                                                data=url_agg_data
                                                ).save()
        else:
            logger.info('[DAEMON] Refreshing aggregate data.')
            data = {}
            d = TestActionAggregateData.objects.get(action_id=action_id,
                                                        test_id=test_id)
            old_data = d.data
            new_data = url_agg_data
            maximum = new_data['max'] if new_data['max'] > old_data['max'] else old_data['max']
            minimum = new_data[ 'min'] if new_data['min'] < old_data['min'] else old_data['min']
            p50 = new_data['50%'] if new_data['50%'] > old_data['50%'] else old_data['50%']
            p75 = new_data['75%'] if new_data['75%'] > old_data['75%'] else old_data['75%']
            p90 = new_data['90%'] if new_data['90%'] > old_data['90%'] else old_data['90%']
            p99 = new_data['99%'] if new_data['99%'] > old_data['99%'] else old_data['99%']
            std = new_data['std']
            old_data = {
                'mean':
                (old_data['weight'] + new_data['weight'])
                /
                (old_data['count'] + new_data['count']),
                'max':
                maximum,
                'min':
                minimum,
                'count':
                old_data['count'] + new_data['count'],
                'errors':
                old_data['errors'] + new_data['errors'],
                'weight':
                old_data['weight'] + new_data['weight'],
                '50%':
                p50,
                '75%':
                p75,
                '90%':
                p90,
                '99%':
                p99,
                'std':
                std,
            }
            d.data = old_data
            d.save()
    logger.info("[DAEMON] Adding test overall data.".format(url))
    test_overall_data = pd.DataFrame()
    df_gr_by_ts = df.groupby(pd.Grouper(freq=data_resolution))
    test_overall_data['avg'] = df_gr_by_ts.response_time.mean()
    test_overall_data['median'] = df_gr_by_ts.response_time.median()
    test_overall_data['count'] = df_gr_by_ts.response_time.count()
    test_overall_data['test_id'] = test_id
    output_json = json.loads(
        test_overall_data.to_json(orient='index',
                                date_format='iso'),
        object_pairs_hook=OrderedDict)
    for row in output_json:
        data = {
            'timestamp': row,
            'avg': output_json[row]['avg'],
            'median': output_json[row]['median'],
            'count': output_json[row]['count']
        }
        test_data = TestData(
            test_id=output_json[row]['test_id'],
            data_resolution_id=data_resolution_id,
            data=data
        )
        test_data.save()
