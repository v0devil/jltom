from collections import OrderedDict
import json
import logging
#from pylab import *
import numpy as np
import pandas as pd
import sys
import re
import os
import zipfile
import sqlalchemy
import shutil
import time
import datetime
import argparse
from xml.etree.ElementTree import ElementTree
from os.path import basename
from sqlalchemy import create_engine
from sqlalchemy.sql import select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.engine import reflection
from itertools import islice

logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
logger = logging.getLogger()

db_engine = create_engine('postgresql://postgres:1234@localhost:5432/jmeter')
db_connection = db_engine.connect()
meta = sqlalchemy.MetaData(bind=db_connection, reflect=True, schema="ltc")
insp = reflection.Inspector.from_engine(db_engine)

project_name = sys.argv[1]

project = meta.tables['ltc.project']
test = meta.tables['ltc.test']
test_data = meta.tables['ltc.test_data']
action = meta.tables['ltc.action']
test_action_data = meta.tables['ltc.test_action_data']
server = meta.tables['ltc.server']
server_monitoring_data = meta.tables['ltc.server_monitoring_data']
test_aggregate = meta.tables['ltc.test_aggregate']
test_action_aggregate_data = meta.tables['ltc.test_action_aggregate_data']
user = meta.tables['ltc.user']
project_graphite_settings = meta.tables['ltc.project_graphite_settings']
error = meta.tables['ltc.error']
test_error = meta.tables['ltc.test_error']

Session = sessionmaker(bind=db_engine)

db_session = Session()
""" stm = test_error.delete()
result = db_session.execute(stm)
stm = error.delete()
result = db_session.execute(stm)
stm = server_monitoring_data.delete()
result = db_session.execute(stm)
stm = test_aggregate.delete()
result = db_session.execute(stm)
stm = test_action_data.delete()
result = db_session.execute(stm)
stm = test_data.delete()
result = db_session.execute(stm)
stm = test.delete()
result = db_session.execute(stm)
stm = action.delete()
result = db_session.execute(stm)
stm = server.delete()
result = db_session.execute(stm)
stm = project.delete()
result = db_session.execute(stm)
stm = test_action_aggregate_data.delete()
result = db_session.execute(stm)
db_session.commit() """
logger.info("Starting data generating script.")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-name', default='test')
    parser.add_argument('--jenkins-base-dir', default='/var/lib/jenkins/')
    return parser.parse_args()


def percentile(n):
    def percentile_(x):
        return np.percentile(x, n)

    percentile_.__name__ = 'percentile_%s' % n
    return percentile_


def mask(df, f):
    return df[f(df)]


def getIndex(item):
    return int(re.search('(\d+)/', item[0].replace('\\', '/')).group(1))


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


def execute_db_stmt(stm, data):
    try:
        result = db_connection.execute(stm)
    except (sqlalchemy.exc.DataError, sqlalchemy.exc.StatementError,
            TypeError) as e:
        logger.error("Cannot add new data for action: {}".format(url))
        logger.error("Data: {}".format(data))
        logger.error("Exception {}".format(e))
        result = False
    return result


data_to_analyze = []
releases = []

build_xml = ElementTree()

args = parse_args()
if args.project_name:
    project_name = args.project_name
if args.jenkins_base_dir:
    jenkins_base_dir = args.jenkins_base_dir
builds_dir = os.path.join(jenkins_base_dir, 'jobs', project_name,
                          'builds').replace('\\', '/')
rx = re.compile(r'.+?.jtl')
logger.info('Check builds directory: {}'.format(builds_dir))
for root, dirs, files in os.walk(builds_dir):
    for file in files:
        logger.info(file)
        if re.match(rx, os.path.join(root, file)):
            if os.stat(os.path.join(root, file)).st_size > 0:
                root = root.replace('\\', '/')
                build_parameters = []
                display_name = "unknown"
                description = ""
                started_at = 0
                duration = 0
                monitoring_data = os.path.join(root, "monitoring.data")
                errors_data = os.path.join(root, "errors")
                build_xml_path = os.path.join(root, "build.xml")

                if os.path.isfile(build_xml_path):
                    logger.info(
                        "Try to parse Jenkins build XML-file: {0}".format(
                            build_xml_path))
                    with open(build_xml_path, "r") as fixfile:
                        data = fixfile.read()
                    data = data.replace("&#x", "")
                    with open(build_xml_path, "w") as fixfile:
                        fixfile.write(data)
                    build_xml.parse(build_xml_path)
                    build_tag = build_xml.getroot()

                    for params in build_tag:
                        if params.tag == 'actions':
                            parameters = params.find('.//parameters')
                            for parameter in parameters:
                                name = parameter.find('name')
                                value = parameter.find('value')
                                build_parameters.append({
                                    name.text: value.text
                                })
                                userId = params.find('.//userId')
                                if userId is not None:
                                    started_by = userId.text
                                    if db_session.query(user.c.id).filter(
                                            user.c.login ==
                                            started_by).count() == 0:
                                        logger.info(
                                            "Adding new user: {0}".format(
                                                started_by))
                                        stm = user.insert().values(
                                            login=started_by)
                                        result = db_connection.execute(stm)
                                    user_id = db_session.query(user.c.id). \
                                        filter(user.c.login == started_by).scalar()
                                else:
                                    user_id = 1
                        elif params.tag == 'startTime':
                            started_at = int(params.text)
                        elif params.tag == 'displayName':
                            display_name = params.text
                        elif params.tag == 'duration':
                            duration = int(params.text)
                        elif params.tag == 'description':
                            description = params.text

                if "Performance_HTML_Report" not in os.path.join(root, file):
                    data_to_analyze.append([
                        os.path.join(root, file), monitoring_data, errors_data,
                        display_name, build_parameters, root
                    ])
                    project_name = re.search('/([^/]+)/builds', root).group(1)
                    if db_session.query(project.c.id). \
                            filter(project.c.project_name == project_name).count() == 0:
                        logger.info(
                            "Adding new project: {0}".format(project_name))
                        stm = project.insert().values(
                            project_name=project_name, show=True)
                        result = db_connection.execute(stm)

                    project_id = db_session.query(project.c.id). \
                            filter(project.c.project_name == project_name).scalar()
                    if db_session.query(test.c.path).filter(
                            test.c.path == root).count() == 0:
                        logger.info("Was found new test data, adding.")
                        build_number = int(
                            re.search('/builds/(\d+)', root).group(1))
                        end_time = started_at + duration
                        if started_at == end_time:
                            end_time = int(time.time() * 1000)
                        stm = test.insert().values(
                            path=root,
                            display_name=display_name,
                            description=description,
                            parameters=build_parameters,
                            project_id=project_id,
                            started_at=started_at,
                            end_time=end_time,
                            build_number=build_number,
                            started_by_id=user_id,
                            data_resolution='1Min',
                            show=True)
                        result = db_connection.execute(stm)
data_to_analyze = sorted(data_to_analyze, key=getIndex, reverse=True)

releases.sort()

dateconv = np.vectorize(datetime.datetime.fromtimestamp)

aggregate_table = 'aggregate_table'
monitor_table = 'monitor_table'

agg = {}
mon = {}

rtot_over_releases = []
cpu_over_releases = []

file_index = 0
logger.info("Trying to open CSV-files")

build_roots = [data_to_analyze[i][5] for i in range(0, len(data_to_analyze))]

logger.info(data_to_analyze)
for d_ in data_to_analyze:
    build_root = d_[5]

    logger.info("Current build directory:" + build_root)
    test_id = db_session.query(
        test.c.id).filter(test.c.path == build_root).scalar()
    project_id = db_session.query(
        test.c.project_id).filter(test.c.id == test_id).scalar()

    checksum = -1
    if db_session.query(test_data.c.test_id).filter(
            test_data.c.test_id == test_id).count() == 0:

        df = pd.DataFrame()
        jmeter_results_file = d_[0]
        if not os.path.exists(jmeter_results_file):
            logger.info("Results file does not exists, try to check archive")
            jmeter_results_zip = jmeter_results_file + ".zip"
            if os.path.exists(jmeter_results_zip):
                logger.info("Archive file was found: " + jmeter_results_zip)
                with zipfile.ZipFile(jmeter_results_zip, "r") as z:
                    z.extractall(build_root)
        logger.info("Executing a new parse: " + jmeter_results_file +
                    " size: " + str(os.stat(jmeter_results_file).st_size))
        if os.stat(jmeter_results_file).st_size > 1000007777:
            logger.info("Executing a parse for a huge file")
            chunks = pd.read_table(
                jmeter_results_file, sep=',', index_col=0, chunksize=6000000)
            parse_task = 0
            for chunk in chunks:
                parse_task += 1
                logger.info("Chunk #{}".format(parse_task))
                chunk.columns = [
                    'response_time', 'url', 'responseCode', 'success',
                    'threadName', 'failureMessage', 'grpThreads', 'allThreads'
                ]
                chunk = chunk[~chunk['url'].str.contains('exclude_')]
                #chunk = chunk[np.abs(chunk['response_time']-chunk['response_time'].mean())<=(3*chunk['response_time'].std())] #keep only the ones that are within +3 to -3 standard deviations
                #convert timestamps to normal date/time
                chunk.index = pd.to_datetime(
                    dateconv((chunk.index.values / 1000)))
                num_lines = chunk['response_time'].count()
                logger.info("Number of lines in chunk: %d." % num_lines)

                unique_urls = chunk['url'].unique()
                logger.info("Actions in the chunk: {}".format(
                    str(unique_urls)))
                for url in unique_urls:
                    if db_session.query(action.c.id).filter(action.c.url == url).\
                            filter(action.c.project_id == project_id).count() == 0:
                        logger.info(
                            "Adding new action with URL: {}".format(url))
                        stm = action.insert().values(
                            url=url,
                            project_id=project_id,
                        )
                        result = execute_db_stmt(stm, url)

                    action_id = db_session.query(action.c.id).filter(action.c.url == url). \
                        filter(action.c.project_id == project_id).scalar()
                    logger.info("Adding data for action: {}".format(url))
                    df_url = chunk[(chunk.url == url)]
                    n = df_url.shape[0]
                    freq = '1Min'
                    if n > 10:
                        df_url = df_url[np.abs(df_url['response_time'] -
                                               df_url['response_time'].mean())
                                        <= (3 * df_url['response_time'].std())]
                    url_data = pd.DataFrame()
                    df_url_gr_by_ts = df_url.groupby(pd.Grouper(freq=freq))
                    url_data['avg'] = df_url_gr_by_ts.response_time.mean()
                    url_data['median'] = df_url_gr_by_ts.response_time.median()
                    url_data['count'] = df_url_gr_by_ts.success.count()
                    del df_url_gr_by_ts
                    df_url_gr_by_ts_only_errors = df_url[(
                        df_url.success == False)].groupby(
                            pd.Grouper(freq=freq))
                    try:
                        url_data['errors'] = float(
                            df_url_gr_by_ts_only_errors.success.count())
                    except (ValueError, TypeError) as e:
                        url_data['errors'] = 0

                    url_data['test_id'] = test_id
                    url_data['url'] = url
                    output_json = json.loads(
                        url_data.to_json(orient='index', date_format='iso'),
                        object_pairs_hook=OrderedDict)
                    del url_data
                    for row in output_json:
                        data = {
                            'timestamp': row,
                            'avg': float(output_json[row]['avg']),
                            'median': float(output_json[row]['median']),
                            'count': int(output_json[row]['count']),
                            'url': output_json[row]['url'],
                            'errors': int(output_json[row]['errors']),
                            'test_id': int(output_json[row]['test_id']),
                        }
                        stm = test_action_data.insert().values(
                            test_id=output_json[row]['test_id'],
                            action_id=action_id,
                            data_resolution_id=1,
                            data=data)
                        result = execute_db_stmt(stm, data)

                    url_agg_data = dict(
                        json.loads(
                            df_url['response_time'].describe().to_json()))
                    url_agg_data['99%'] = df_url['response_time'].quantile(.99)
                    url_agg_data['90%'] = df_url['response_time'].quantile(.90)
                    url_agg_data['weight'] = float(
                        df_url['response_time'].sum())
                    url_agg_data['errors'] = float(df_url[(
                        df_url['success'] == False)]['success'].count())

                    logger.info("Check aggregate data: {} {}".format(
                        test_id, action_id))
                    if db_session.query(
                            test_action_aggregate_data.c.id).filter(
                                test_action_aggregate_data.c.test_id == test_id
                            ).filter(test_action_aggregate_data.c.action_id ==
                                     action_id).count() == 0:
                        try:
                            stm = test_action_aggregate_data.insert().values(
                                test_id=test_id,
                                action_id=action_id,
                                data=url_agg_data)
                            result = db_connection.execute(stm)
                        except (sqlalchemy.exc.DataError,
                                sqlalchemy.exc.StatementError, TypeError) as e:
                            logger.error("Data: {}".format(url_agg_data))
                            logger.error("Exception {}".format(e))

                    else:
                        data = {}
                        old_data = db_session.query(
                            test_action_aggregate_data.c.data).filter(
                                test_action_aggregate_data.c.test_id == test_id
                            ).filter(test_action_aggregate_data.c.action_id ==
                                     action_id).one()
                        new_data = url_agg_data
                        logger.info("old_data")
                        logger.info(old_data)
                        logger.info("new_data")
                        logger.info(new_data)

                        maximum = new_data[
                            'max'] if new_data['max'] > old_data[0]['max'] else old_data[
                                0]['max']
                        minimum = new_data[
                            'min'] if new_data['min'] < old_data[0]['min'] else old_data[
                                0]['min']
                        p50 = new_data[
                            '50%'] if new_data['50%'] > old_data[0]['50%'] else old_data[
                                0]['50%']
                        p75 = new_data[
                            '75%'] if new_data['75%'] > old_data[0]['75%'] else old_data[
                                0]['75%']
                        p90 = new_data[
                            '90%'] if new_data['90%'] > old_data[0]['90%'] else old_data[
                                0]['90%']
                        p99 = new_data[
                            '99%'] if new_data['99%'] > old_data[0]['99%'] else old_data[
                                0]['99%']
                        std = new_data['std']
                        old_data = {
                            'mean':
                            (old_data[0]['weight'] + new_data['weight']) /
                            (old_data[0]['count'] + new_data['count']),
                            'max':
                            maximum,
                            'min':
                            minimum,
                            'count':
                            old_data[0]['count'] + new_data['count'],
                            'errors':
                            old_data[0]['errors'] + new_data['errors'],
                            'weight':
                            old_data[0]['weight'] + new_data['weight'],
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
                        stm = test_action_aggregate_data.update().values(
                            data=old_data).where(
                                test_action_aggregate_data.c.test_id == test_id
                            ).where(test_action_aggregate_data.c.action_id ==
                                    action_id)
                        try:
                            result = db_connection.execute(stm)
                        except (sqlalchemy.exc.DataError,
                                sqlalchemy.exc.StatementError, TypeError) as e:
                            logger.error("Data: {}".format(old_data))
                            logger.error("Exception {}".format(e))
                    del url_agg_data, df_url
                test_overall_data = pd.DataFrame()
                df_gr_by_ts = chunk.groupby(pd.Grouper(freq=freq))
                test_overall_data['avg'] = df_gr_by_ts.response_time.mean()
                test_overall_data[
                    'median'] = df_gr_by_ts.response_time.median()
                test_overall_data['count'] = df_gr_by_ts.response_time.count()
                test_overall_data['test_id'] = test_id
                output_json = json.loads(
                    test_overall_data.to_json(
                        orient='index', date_format='iso'),
                    object_pairs_hook=OrderedDict)
                for row in output_json:
                    data = {
                        'timestamp': row,
                        'avg': float(output_json[row]['avg']),
                        'median': float(output_json[row]['median']),
                        'count': float(output_json[row]['count'])
                    }
                    stm = test_data.insert().values(
                        test_id=output_json[row]['test_id'],
                        data=data,
                        data_resolution_id=1,
                        source='default')
                    result = execute_db_stmt(stm, data)
                del test_overall_data, df_gr_by_ts, chunk
                logger.info("Chunk #{} was parsed.".format(parse_task))
            del chunks
        else:
            df = pd.read_csv(
                jmeter_results_file, index_col=0, low_memory=False)
            df.columns = [
                'response_time', 'url', 'responseCode', 'success',
                'threadName', 'failureMessage', 'grpThreads', 'allThreads'
            ]
            df = df[~df['url'].str.contains('exclude_')]
            #df = df[np.abs(df['response_time']-df['response_time'].mean())<=(3*df['response_time'].std())] #keep only the ones that are within +3 to -3 standard deviations

            df.columns = [
                'response_time', 'url', 'responseCode', 'success',
                'threadName', 'failureMessage', 'grpThreads', 'allThreads'
            ]
            #convert timestamps to normal date/time
            df.index = pd.to_datetime(dateconv((df.index.values / 1000)))
            num_lines = df['response_time'].count()
            logger.info("Number of lines in file: %d." % num_lines)

            unique_urls = df['url'].unique()
            for url in unique_urls:
                if db_session.query(action.c.id).filter(action.c.url == url).\
                        filter(action.c.project_id == project_id).count() == 0:
                    logger.info("Adding new action with URL: {}".format(url))
                    stm = action.insert().values(
                        url=url,
                        project_id=project_id,
                    )
                    result = execute_db_stmt(stm, url)

                action_id = db_session.query(action.c.id).filter(action.c.url == url). \
                    filter(action.c.project_id == project_id).scalar()
                logger.info("Adding data for action: {}".format(url))
                df_url = df[(df.url == url)]
                n = df_url.shape[0]
                freq = '1Min'
                if n > 10 and n < 10000000:
                    logger.info('Size of the data set for action {}:{}'.format(
                        url, n))
                    # Filter outliers (> or < 3 sigmas)
                    df_url = df_url[np.abs(df_url['response_time'] -
                                           df_url['response_time'].mean()) <=
                                    (3 * df_url['response_time'].std())]
                elif n > 30000000:
                    freq = '10Min'
                url_data = pd.DataFrame()
                df_url_gr_by_ts = df_url.groupby(pd.Grouper(freq=freq))
                url_data['avg'] = df_url_gr_by_ts.response_time.mean()
                url_data['median'] = df_url_gr_by_ts.response_time.median()
                url_data['count'] = df_url_gr_by_ts.success.count()
                del df_url_gr_by_ts
                df_url_gr_by_ts_only_errors = df_url[(
                    df_url.success == False)].groupby(pd.Grouper(freq=freq))
                url_data[
                    'errors'] = df_url_gr_by_ts_only_errors.success.count()
                url_data['test_id'] = test_id
                url_data['url'] = url
                output_json = json.loads(
                    url_data.to_json(orient='index', date_format='iso'),
                    object_pairs_hook=OrderedDict)
                del url_data
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
                    stm = test_action_data.insert().values(
                        test_id=output_json[row]['test_id'],
                        action_id=action_id,
                        data_resolution_id=1,
                        data=data)
                    result = execute_db_stmt(stm, data)

                url_agg_data = dict(
                    json.loads(df_url['response_time'].describe().to_json()))
                url_agg_data['99%'] = float(df_url['response_time'].quantile(.99))
                url_agg_data['90%'] = float(df_url['response_time'].quantile(.90))
                url_agg_data['weight'] = float(df_url['response_time'].sum())
                url_agg_data['errors'] = int(df_url[(
                    df_url['success'] == False)]['success'].count())
                stm = test_action_aggregate_data.insert().values(
                    test_id=test_id, action_id=action_id, data=url_agg_data)
                result = execute_db_stmt(stm, url_agg_data)
                del url_agg_data, df_url
            test_overall_data = pd.DataFrame()
            df_gr_by_ts = df.groupby(pd.Grouper(freq=freq))
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
                stm = test_data.insert().values(
                    test_id=test_id,
                    data=data,
                    data_resolution_id=1,
                    source='default')
                result = execute_db_stmt(stm, data)
            del test_overall_data, df_gr_by_ts, df
        zip_results_file(jmeter_results_file)

    file_index += 1

num = 0
GRAPHS = ""
for build_root in build_roots:
    uniqueURL = []

    rownum = 0

    if os.path.isfile(
            data_to_analyze[num][1]) and os.stat(data_to_analyze[num][1]).st_size != 0:
        test_id = db_session.query(
            test.c.id).filter(test.c.path == build_root).scalar()
        f = open(data_to_analyze[num][1], "r")
        lines = f.readlines()
        f.close()
        f = open(data_to_analyze[num][1], "w")
        for line in lines:
            if not ('start' in line):
                f.write(line)

        f.close()
        monitoring_df = pd.read_csv(data_to_analyze[num][1], index_col=1, sep=";")

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
            if db_session.query(server.c.id).\
                    filter(server.c.server_name == server_).count() == 0:
                logger.info("Adding new server: {}".format(server_))
                stm = server.insert().values(server_name=server_)
                result = execute_db_stmt(stm, server_)

            server_id = db_session.query(server.c.id).\
                filter(server.c.server_name == server_).scalar()

            if db_session.query(server_monitoring_data.c.test_id).\
                    filter(server_monitoring_data.c.test_id==test_id).\
                    filter(server_monitoring_data.c.server_id==server_id).count()==0:
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
                    stm = server_monitoring_data.insert().values(
                        test_id=test_id,
                        server_id=server_id,
                        data=data,
                        data_resolution_id=1,
                        source='default')
                    result = execute_db_stmt(stm, data)

    else:
        logger.info("Monitoring data is not exist")
    errors_zip_dest = build_root + "/errors.zip"
    test_id = db_session.query(
        test.c.id).filter(test.c.path == build_root).scalar()
    if db_session.query(test_error.c.id).filter(
            test_error.c.test_id == test_id).count() == 0:
        logger.info("Errors data is empty for test: {}".format(test_id))
        if not os.path.isdir(data_to_analyze[num][2]) or not len(
                os.listdir(data_to_analyze[num][2])) > 0:
            if os.path.exists(errors_zip_dest):
                logger.info("Archive file was found: " + errors_zip_dest)
                with zipfile.ZipFile(errors_zip_dest, "r") as z:
                    z.extractall(build_root + '/errors/')
        if os.path.isdir(
                data_to_analyze[num][2]) and len(os.listdir(data_to_analyze[num][2])) > 0:
            logger.info("Parsing errors data")
            project_id = db_session.query(
                test.c.project_id).filter(test.c.id == test_id).scalar()

            # Iterate through files in errors folder
            for root, dirs, files in os.walk(data_to_analyze[num][2]):
                for file in files:
                    error_file = os.path.join(root, file)
                    try:
                        error_text = ""
                        error_code = 0
                        action_name = ""
                        with open(error_file) as fin:
                            error_text = ""
                            for i, line in enumerate(fin):

                                if i == 0:
                                    action_name = line
                                    action_name = re.sub(
                                        '(\r\n|\r|\n)', '', action_name)
                                elif i == 1:
                                    error_code = line
                                    error_code = re.sub(
                                        '(\r\n|\r|\n)', '', error_code)
                                elif i > 1 and i < 6:  # take first 4 line of error
                                    error_text += line
                        error_text = re.sub('\d', 'N', error_text)
                        error_text = re.sub('(\r\n|\r|\n)', '_', error_text)
                        error_text = re.sub('\s', '_', error_text)
                        if db_session.query(action.c.id).filter(action.c.url == action_name).\
                            filter(action.c.project_id == project_id).count() > 0:
                            action_id = db_session.query(
                                action.c.id
                            ).filter(action.c.url == action_name).filter(
                                action.c.project_id == project_id).scalar()
                            if db_session.query(error.c.id).filter(
                                    error.c.text == error_text).count() == 0:
                                logger.info(
                                    "Adding new error: {}".format(error_text))
                                stm = error.insert().values(
                                    text=error_text, code=error_code)
                                result = execute_db_stmt(stm, error_code)
                            error_id = db_session.query(error.c.id).filter(
                                error.c.text == error_text).scalar()
                            if db_session.query(test_error.c.id).filter(
                                    test_error.c.error_id == error_id
                            ).filter(test_error.c.test_id == test_id).filter(
                                    test_error.c.action_id ==
                                    action_id).count() == 0:
                                stm = test_error.insert().values(
                                    test_id=test_id,
                                    error_id=error_id,
                                    action_id=action_id,
                                    count=1)
                                result = execute_db_stmt(stm, action_id)
                            else:
                                prev_count = db_session.query(
                                    test_error.c.count).filter(
                                        test_error.c.error_id == error_id
                                    ).filter(test_error.c.test_id == test_id
                                             ).filter(test_error.c.action_id ==
                                                      action_id).scalar()
                                stm = test_error.update(
                                ).values(count=prev_count + 1).where(
                                    test_error.c.error_id == error_id
                                ).where(test_error.c.test_id == test_id).where(
                                    test_error.c.action_id == action_id)
                                result = execute_db_stmt(stm, action_id)
                    except ValueError:
                        logger.error("Cannot parse error file for: ")
        zip_dir(data_to_analyze[num][2], errors_zip_dest)
        try:
            if 'errors' in data_to_analyze[num][2]:
                shutil.rmtree(data_to_analyze[num][2])
        except OSError:
            logger.error('OSError')
        logger.error("Errors folder was packed and removed")
    num += 1

#stmt = select([test.c.id, test.c.path])
# query_result = db_engine.execute(stmt)
#
# logger.info("Cleanup obsolete test results")
# for q in query_result:
#     test_id = q.id
#     test_path = q.path
#     logger.info("Check data in directory: {}".format(test_path))
#     if not os.path.exists(q.path):
#         logger.info("Deleting test_id: {} path: {}".format(
#             str(test_id), test_path))
#         stm2 = server_monitoring_data.delete().where(
#             server_monitoring_data.c.test_id == test_id)
#         stm3 = test_action_data.delete().where(
#             test_action_data.c.test_id == test_id)
#         stm4 = test_data.delete().where(test_data.c.test_id == test_id)
#         stm5 = test_action_aggregate_data.delete().where(
#             test_action_aggregate_data.c.test_id == test_id)
#         stm6 = test_error.delete().where(test_error.c.test_id == test_id)
#         stm7 = test.delete().where(test.c.id == test_id)
#
#         result2 = db_connection.execute(stm2)
#         result3 = db_connection.execute(stm3)
#         result4 = db_connection.execute(stm4)
#         result5 = db_connection.execute(stm5)
#         result6 = db_connection.execute(stm6)
#         result7 = db_connection.execute(stm7)
