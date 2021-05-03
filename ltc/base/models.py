import datetime
import json
import logging
import math
import os
import re
import subprocess
import tempfile
import threading
import zipfile
from collections import OrderedDict, defaultdict
from os.path import dirname as up
from subprocess import call
from xml.etree.ElementTree import ElementTree

import numpy as np
import pandas as pd
from django.apps import apps
from django.conf import settings
from django.contrib.postgres.fields import JSONField
from django.db import models
from django.db.models import Avg, F, FloatField, Func, Max, Min, Sum
from django.db.models.expressions import F, RawSQL
from django.template.loader import render_to_string
from django.utils import timezone
from ltc.analyzer.models import (
    Action, ReportTemplate,
    TestActionAggregateData, TestActionData,
    TestData, TestDataResolution
)
from ltc.base.utils.confluence.confluenceposter import ConfluenceClient
from ltc.base.utils.confluence.helpers import generate_confluence_graph
from ltc.controller.models import JmeterServer, JmeterServerData, LoadGenerator
from ltc.controller.utils import jmeter_simple_writer
from pandas import DataFrame

logger = logging.getLogger('django')

dateconv = np.vectorize(datetime.datetime.fromtimestamp)

class Round(Func):
    function = 'ROUND'
    template = '%(function)s(%(expressions)s, 1)'

class Project(models.Model):
    name = models.CharField(max_length=100)
    enabled = models.BooleanField(default=True)
    project_template = models.ForeignKey(
        ReportTemplate, null=True, on_delete=models.DO_NOTHING,
        related_name='related_project',
    )
    template = models.ForeignKey(
        ReportTemplate, null=True, on_delete=models.DO_NOTHING
    )
    class Meta:
        db_table = 'project'

    def __str__(self):
        return self.name

    def get_thread_malloc(self):
        """Return avg memory allocation per thread for the current project

        Returns:
            int: MB
        """

        max_threads = JmeterServerData.objects.filter(
            project=self
        ).annotate(
            threads=RawSQL("((data->>%s)::numeric)",
            ('threads_number',))
        ).aggregate(max_threads=Max(
                F('threads'), output_field=FloatField()
            )
        )

        data = JmeterServerData.objects.filter(
            project=self, data__contains=[{'threads_number': max_threads}]
        ).annotate(mem_per_thread=(
                RawSQL("((data->>%s)::numeric)", ('S0U',)) +
                RawSQL("((data->>%s)::numeric)", ('S1U',)) +
                RawSQL("((data->>%s)::numeric)", ('EU',)) +
                RawSQL("((data->>%s)::numeric)", ('OU',))
            )/1024/RawSQL("((data->>%s)::numeric)", ('threads_number',))
        ).aggregate(thread_malloc=Avg(
                F('thread_malloc'), output_field=FloatField()
            )
        )
        thread_malloc = int(math.ceil(data['thread_malloc']))
        logger.info('Estimated malloc (MB) per thread: %s', thread_malloc)
        return thread_malloc

    def get_test(self, n):
        '''
        Get first, second, N test for project
        '''
        return Test.objects.filter(
            project=self, status=Test.FINISHED
        ).order_by(F('started_at').desc(nulls_last=True))[:n]

    def compare_tests(self, tests_list):
        '''Return comparasion data for dashboard'''
        tests = []
        for t in tests_list:
            test_id = t.id
            project_tests = Test.objects.filter(
                project=self, id__lte=test_id
            ).order_by(F('started_at').desc(nulls_last=True))

            if project_tests.count() > 1:
                prev_test_id = project_tests[1].id
            else:
                prev_test_id = test_id
            test_data = TestActionAggregateData.objects.filter(test_id=test_id). \
                annotate(errors=RawSQL("((data->>%s)::numeric)", ('errors',))). \
                annotate(count=RawSQL("((data->>%s)::numeric)", ('count',))). \
                annotate(weight=RawSQL("((data->>%s)::numeric)", ('weight',))). \
                aggregate(count_sum=Sum(F('count'), output_field=FloatField()),
                        errors_sum=Sum(F('errors'), output_field=FloatField()),
                        overall_avg=Sum(F('weight')) / Sum(F('count')))

            prev_test_data = TestActionAggregateData.objects. \
                filter(test_id=prev_test_id). \
                annotate(errors=RawSQL("((data->>%s)::numeric)", ('errors',))). \
                annotate(count=RawSQL("((data->>%s)::numeric)", ('count',))). \
                annotate(weight=RawSQL("((data->>%s)::numeric)", ('weight',))). \
                aggregate(
                    count_sum=Sum(F('count'), output_field=FloatField()),
                    errors_sum=Sum(F('errors'), output_field=FloatField()),
                    overall_avg=Sum(F('weight')) / Sum(F('count'))
                    )
            try:
                errors_percentage = test_data['errors_sum'] * 100 / test_data[
                    'count_sum']
            except (TypeError, ZeroDivisionError) as e:
                logger.error(e)
                errors_percentage = 0
            success_requests = 100 - errors_percentage
            # TODO: improve this part
            if success_requests >= 98:
                result = 'success'
            elif success_requests < 98 and success_requests >= 95:
                result = 'warning'
            else:
                result = 'danger'
            test_name = t.name
            if test_name:
                test_name = f'{self.name} - {test_id}'
            tests.append({
                'test': t,
                'project': self,
                'success_requests':
                success_requests,
                'test_avg_response_times':
                test_data['overall_avg'],
                'prev_test_avg_response_times':
                prev_test_data['overall_avg'],
                'result':
                result,
                'prefix': self.template.confluence_page
            })
        return tests

    def generate_confluence_report(self, force=False):
        last_test = self.get_test(1)
        data = last_test.first().get_compare_tests_aggregate_data(
            self, 10, order='-started_at'
        )
        # agg_response_times_graph = generate_confluence_graph(self, list(data))
        last_tests = self.get_test(5)
        tests = self.compare_tests(last_tests)
        content = self.project_template.render(
            vars={'last_tests': tests, 'project': self}, force=force
        )
        # content = content.format(self.name, agg_response_times_graph)
        if not self.template.confluence_space:
            logger.warning(
                f'Confluence report template space is not'
                f'configured for {self}'
            )
            return

        wiki_url = settings.WIKI_URL
        wiki_user = settings.WIKI_USER
        wiki_password = settings.WIKI_PASS
        cc = ConfluenceClient(
            wiki_url, wiki_user, wiki_password
        )
        cc.login()

        space = self.template.confluence_space
        target_page = self.template.confluence_page

        if not target_page:
            target_page = '{0} Load Test Reports'.format(self.name)

        target_url = f'{wiki_url}/display/{space}/{target_page}'
        # Post parent summary page
        try:
            logger.info(
                f'Try to open Confluence page: {target_page}: {target_url}'
            )
            page_parent = cc.get_page(space, target_page)
        except Exception as e:
            logger.error(e)

        page_parent['content'] = content

        try:
            cc.post_page(page_parent)
        except Exception as e:
            logger.error(e)
        page_parent_id = page_parent['id']
        del page_parent['id']
        del page_parent['url']
        del page_parent['modified']
        del page_parent['created']
        del page_parent['version']
        del page_parent['contentStatus']
        for test in last_tests:
            test.post_to_confluence(page_parent_id, page_parent, force=force)

        return content


class Test(models.Model):
    CREATED = 'C'
    RUNNING = 'R'
    ANALYZING = 'A'
    SCHEDULED = 'S'
    FINISHED = 'F'
    FAILED = 'FA'
    STATUSES = (
        (CREATED, 'created'),
        (RUNNING, 'running'),
        (ANALYZING, 'analyzing'),
        (SCHEDULED, 'scheduled'),
        (FINISHED, 'finished'),
        (FAILED, 'failed'),
    )
    project = models.ForeignKey(Project, on_delete=models.CASCADE, null=True)
    name = models.TextField(default='')
    status = models.CharField(
        max_length=12, choices=STATUSES, default=CREATED
    )
    jmeter_path = models.TextField(null=True, blank=True)
    temp_path = models.TextField(null=True, blank=True)
    jmeter_heap = models.IntegerField(default=0)
    threads = models.IntegerField(default=0)
    duration = models.IntegerField(default=0)
    started_at = models.DateTimeField(null=True, db_index=True)
    finished_at = models.DateTimeField(null=True, db_index=True)
    last_active = models.DateTimeField(null=True, db_index=True) 
    vars = JSONField(default={})

    def __str__(self):
        return f'{self.project} - {self.id} {self.name}'

    def prev_test(self):
        '''
        Return previous for the current test
        '''

        t = Test.objects.filter(
            started_at__lte=self.started_at, project=self.project
        ).order_by('-started_at')[:2]
        if len(t) > 1:
            return t[1]
        return self

    def analyze(self, mode=''):
        logger.info(
            f'Parse and generate test data: {self.id}; status: {self.status}'
        )
        if self.status == Test.RUNNING:
            self.status = Test.ANALYZING
            self.save()
            for file in TestFile.objects.filter(test=self):
                file.generate_report()
        self.status = Test.FINISHED
        return self.save()

    def init(self, source='jenkins_build'):
        data = TestFile.objects.get(
            test=self, file_type=TestFile.JENKINS_BUILD_XML
        ).test_metadata()
        self.name = data['name']
        self.status = data['status']
        self.project = data['project']
        self.started_at = data['started_at']
        self.finished_at = data['finished_at']
        self.save()

    def aggregate_table(self):
        '''Return aggregate data for the test'''

        return TestActionAggregateData.objects.annotate(
            name=F('action__name')
        ).filter(test=self).values('action__name', 'action_id', 'data')

    def top_errors(self, n=5):
        '''
        Return top N actions with highest errors percentage
        '''

        data = TestActionAggregateData.objects.filter(test=self). \
            annotate(name=F('action__name')). \
            annotate(errors=Round(
                RawSQL("((data->>%s)::numeric)", ('errors',)) * 100 /
                RawSQL("((data->>%s)::numeric)", ('count',)))
        ).order_by('-errors').values('name', 'action_id', 'errors')[:n]
        return data

    def top_mean(self, n=10):
        '''
        Return top N actions with highest average response times
        '''

        data = TestActionAggregateData.objects.filter(
            test=self).annotate(name=F('action__name')).annotate(
            mean=RawSQL("((data->>%s)::numeric)", ('mean',))
        ).order_by('-mean').values('name', 'mean')[:n]
        data = json.dumps(list(data), cls=DjangoJSONEncoder)
        return data

    def get_test_metric(self, metric):

        metrics = {
            'mean':
            {'query':
                Sum(RawSQL("((data->>%s)::numeric)", ('mean',)) *
                    RawSQL("((data->>%s)::numeric)", ('count',))) /
                Sum(RawSQL("((data->>%s)::numeric)", ('count',))),
                'source_model': 'TestData'
            },
            'median':
            {'query':
                Sum(RawSQL("((data->>%s)::numeric)", ('median',)) *
                    RawSQL("((data->>%s)::numeric)", ('count',))) /
                Sum(RawSQL("((data->>%s)::numeric)", ('count',))),
                'source_model': 'TestData'
            },
            'cpu_load':
            {
                'query': Avg(RawSQL(
                    "((data->>%s)::float) + ((data->>%s)::float) + "
                    "((data->>%s)::float)", (
                        'CPU_user',
                        'CPU_iowait',
                        'CPU_system',
                    ))),
                'source_model': 'ServerMonitoringData'
            }

        }

        metric_mapping = {
            metric: metrics[metric]['query']
        }
        source_model = metrics[metric]['source_model']
        model_class = apps.get_model(
            'analyzer', source_model
        )
        data_resolution = TestDataResolution.objects.filter(
            per_sec_divider=60
        ).first()

        data = model_class.objects.filter(
            test=self, source='default', data_resolution=data_resolution
        )
        data = data.annotate(
            started_at=F('test__started_at')
        )

        data = data.values('started_at').annotate(
            **metric_mapping
        )
        # TODO: think about it
        if metric == 'cpu_load':
            data = data.values(metric, 'server__server_name')
        else:
            data = data.values(metric)
        data = data.order_by('started_at')
        return list(data)

    def prepare_test_plan(self, path):
        """Prepares test plan by adding Jmeter Simple CSV write at the end of
        .jmx file
        """
        tmp_dir = f'/tmp/loadtest_{self.id}'
        self.temp_path = tmp_dir
        self.save()
        testplan = TestFile(
            test=self,
            file_type=TestFile.TESTPLAN_FILE,
            path=path
        )
        result_file = TestFile(
            test=self,
            file_type=TestFile.MAIN_RESULT_CSV_FILE,
            path=os.path.join(self.temp_path, 'results.csv')
        )
        result_file.save()
        if testplan:
            with open(testplan.path, 'r') as src_jmx:
                source_lines = src_jmx.readlines()
                closing = source_lines.pop(-1)
                closing = source_lines.pop(-1) + closing
                if "<hashTree/>" in source_lines[-1]:
                    source_lines.pop(-1)
                    source_lines.pop(-1)
                    source_lines.pop(-1)
                    source_lines.pop(-1)
                closing = source_lines.pop(-1) + closing
                tmp_dir = f'/tmp/loadtest_{self.id}'
                self.temp_path = tmp_dir
                os.mkdir(tmp_dir)
                fd, fname = tempfile.mkstemp(
                    '.jmx', 'new_', dir=tmp_dir
                )
                os.close(fd)
                # Destination of test plan
                testplan.path = fname
                file_handle = open(testplan.path, "w")
                logger.info('New testplan: %s', testplan.path)
                file_handle.write(''.join(source_lines))
                file_handle.write(
                    ''.join(jmeter_simple_writer(result_file.path)))
                file_handle.write(closing)
                file_handle.close()
        logger.info(f'Created temporary testplan: {testplan.path}')
        return testplan.save()


    def find_loadgenerators(self, thread_size = 0):
        THREAD_COUNT_CONFIG = {
            1: 400,
            2: 400,
            3: 300,
            4: 300,
            5: 200,
            6: 200,
        }
        thread_malloc = thread_size
        if not thread_malloc:
            thread_malloc = self.project.get_thread_malloc()
        threads_per_jmeter_server = THREAD_COUNT_CONFIG.get(thread_malloc, 100)
        logger.info(
            'Estimated threads per Jmeter server: %s',
            threads_per_jmeter_server
        )
        jmeter_servers_count = int(
                math.ceil(float(self.threads) /
                threads_per_jmeter_server
            )
        )
        jmeter_server_malloc = int(math.ceil(
            thread_malloc * threads_per_jmeter_server * 1.2
        ))
        self.jmeter_malloc = jmeter_server_malloc
        malloc_total = math.ceil(
            jmeter_servers_count * jmeter_server_malloc
        )
        loadgenerators = {}
        max_threads = 0
        for loadgenerator in LoadGenerator.objects.all():
            jmeter_servers = math.ceil(float(
                loadgenerator.memory_free
            )/(jmeter_server_malloc))
            loadgenerators[
                loadgenerator.hostname
            ] = jmeter_servers
            max_threads += jmeter_servers * threads_per_jmeter_server
        if max_threads < self.threads:
            return [] # Not enough generators
        jmeter_server_amount = math.ceil(
            self.threads / threads_per_jmeter_server
        )
        return loadgenerators, jmeter_server_amount

    def start_jmeter_servers(
        self,
        loadgenerator: LoadGenerator,
        jmeter_servers_per_generator: int,
        jmeter_servers_target_amount: int,
        testplan_original,
    ):
        testplan = TestFile.objects.filter(
            file_type=TestFile.TESTPLAN_FILE,
            test=self,
        ).first()
        if not testplan:
            raise Exception('Smth wrong with testplan data')
        dest = os.path.dirname(os.path.abspath(testplan.path))
        src = os.path.dirname(os.path.abspath(testplan_original))
        call(
            [
                'cp', '-a', src + '/.',
                dest + '/'
            ]
        )
        loadgenerator.start_jmeter_servers(
            jmeter_servers_per_generator,
            jmeter_servers_target_amount,
            self
        )
        loadgenerator.distribute_testplan(testplan)

    def prepare_jmeter(self):
        if not os.path.exists(self.jmeter_path):
            raise Exception(f'{self.jmeter_path} does not exist')
        temp_dir = tempfile.mkdtemp()
        call(['cp', '-a', self.jmeter_path + '/.', temp_dir + '/'])
        self.jmeter_path = temp_dir
        logger.info(f'Temp jmeter path: {self.jmeter_path}')

    def prepare_jmeter_servers(
        self, loadgenerators, jmeter_servers_target_amount, testplan
    ):
        logger.info(
            f'Estimated amount of '
            f'jmeter servers: {jmeter_servers_target_amount}'
        )
        target_amount = jmeter_servers_target_amount
        jmeter_servers_per_lg = {}
        safe_loop = jmeter_servers_target_amount
        while jmeter_servers_target_amount > 0:
            for k, v in loadgenerators.items():
                if v > 0:
                    loadgenerators[k] -= 1
                    if jmeter_servers_per_lg.get(k) is None:
                        jmeter_servers_per_lg[k] = 0
                    jmeter_servers_per_lg[k] += 1
                    jmeter_servers_target_amount -= 1
                    if jmeter_servers_target_amount < 1:
                        break
            safe_loop -= 1
            if safe_loop < 1 and jmeter_servers_target_amount > 0:
                raise Exception('Smth went wrong')

        for k, jmeter_servers_per_generator in jmeter_servers_per_lg.items():
            loadgenerator, _ = LoadGenerator.objects.get_or_create(
                hostname=k
            )
            self.start_jmeter_servers(
                loadgenerator,
                jmeter_servers_per_generator,
                target_amount,
                testplan,
            )

    def start(self):
        testplan = TestFile.objects.filter(
            file_type=TestFile.TESTPLAN_FILE,
            test=self,
        ).first()
        jmeter_bin = os.path.join(self.jmeter_path, 'bin', 'ApacheJMeter.jar')
        jmeter_servers_str = '-R '
        for jmeter_server in JmeterServer.objects.filter(test=self):
            jmeter_servers_str += (
                f',{jmeter_server.loadgenerator.hostname}:{jmeter_server.port}'
            )
        args = ['java', '-jar', '-Xms7g', '-Xmx7g', '-Xss228k']
        args += [
            jmeter_bin,
            '-Jserver.rmi.ssl.disable=true',
            '-n', '-t',  testplan.path,
            '-j',  f'{self.temp_path}/loadtest.log',
            jmeter_servers_str.strip(),
            '-X',
            '-Jjmeter.save.saveservice.default_delimiter=,'
        ]
        logger.info(f'Main jmeter cmd: {" ".join(args)}')
        if self.duration > 0:
            jmeter_process = subprocess.Popen(
                args,
                executable='java',
                stdout=subprocess.PIPE,
                preexec_fn=os.setsid,
                close_fds=True,
                #timeout=self.duration,
            )
        else:
            jmeter_process = subprocess.Popen(
                args,
                executable='java',
                stdout=subprocess.PIPE,
                preexec_fn=os.setsid,
                close_fds=True,
            )
        self.status = Test.RUNNING
        self.started_at = timezone.now()
        self.save()
        self.wait_for_finished(jmeter_process)

    def wait_for_finished(self, jmeter_process):
        ''' Check if test is still running'''
        if self.status == Test.RUNNING:
            while jmeter_process.poll() is None:
                self.last_active = timezone.now()
                self.save()
                l = jmeter_process.stdout.readline()
                logger.info(l)
            # When the subprocess terminates there might be unconsumed output
            # that still needs to be processed.
            logger.info(jmeter_process.stdout.read())
            retcode = jmeter_process.poll()
            logger.info(
                f'JMeter process finished with exit code: {retcode}'
            )
            self.finished_at = timezone.now()
            self.save()
            self.stop(jmeter_process)

    def stop(self, jmeter_process):
        logger.info(f'Stopping test {self.id}')
        for loadgenerator in LoadGenerator.objects.all():
            loadgenerator.gather_errors_data(self)
            loadgenerator.stop_jmeter_servers(self)

    def cleanup(self):
        if self.temp_path and '/tmp/' in self.temp_path:
            if not '/tmp/' in self.temp_path:
                return
            logger.info(f'Cleaning up main tmp dir:{self.temp_path}')
            call(['rm', '-rf', self.temp_path])
        if self.jmeter_path:
            if not '/tmp/' in self.jmeter_path:
                return
            logger.info(f'Cleaning up jmeter tmp dir:{self.jmeter_path}')
            call(['rm', '-rf', self.jmeter_path])

    def terminate(self):
        logger.info(f'Terminating test {self.id}')
        for loadgenerator in LoadGenerator.objects.all():
            loadgenerator.stop_jmeter_servers(self)
        self.cleanup()
        self.status = Test.FAILED
        self.save()

    def post_to_confluence(self, page_parent_id, page_parent, force=False):

        wiki_url = settings.WIKI_URL
        wiki_user = settings.WIKI_USER
        wiki_password = settings.WIKI_PASS
        cc = ConfluenceClient(
            wiki_url, wiki_user, wiki_password
        )
        cc.login()
        space = self.project.template.confluence_space
        target_page = self.project.template.confluence_page

        page_title = target_page + ' - ' + str(self.id)
        test_report_page_exists = True
        try:
            page_test_report = cc.get_page(space, page_title)
        except Exception as e:
            test_report_page_exists = False
            page_test_report = page_parent
            page_test_report['parentId'] = page_parent_id
            page_test_report['title'] = page_title

        if not test_report_page_exists or force is True:
            logger.info('Creating test report page: {}'.format(self.id))
            content = self.project.template.render(
                vars={'test': self}, force=force
            )
            page_test_report['content'] = content
            try:
                cc.post_page(page_test_report)
            except Exception as e:
                logger.error(e)

        cc.logout()

    def get_compare_tests_aggregate_data(
        self, project, n, order='-test__started_at',
        source='default'
    ):
        '''
        Compares given test against n previous tests
        '''
        if not self.started_at:
            return
        data = TestData.objects. \
            filter(
                test__started_at__lte=self.started_at,
                test__project=project,
                source=source,
                data_resolution_id=1
            ).\
            annotate(name=F('test__name')). \
            annotate(started_at=F('test__started_at')). \
            values('name', 'started_at'). \
            annotate(average=Sum(
                    RawSQL("((data->>%s)::numeric)", ('avg',)
            ) * RawSQL("((data->>%s)::numeric)", ('count',))
            ) / Sum(RawSQL("((data->>%s)::numeric)", ('count',)))). \
            annotate(median=Sum(
                RawSQL(
                    "((data->>%s)::numeric)", ('median',)
                ) * RawSQL(
                    "((data->>%s)::numeric)", ('count',))
            ) / Sum(RawSQL("((data->>%s)::numeric)", ('count',)))). \
            order_by(order)[:n]
        return data

class TestFile(models.Model):
    ONLINE_RESULT_CSV_FILE = 'O'
    MAIN_RESULT_CSV_FILE = 'M'
    LOG_FILE = 'L'
    TESTPLAN_FILE = 'T'
    JENKINS_BUILD_XML = 'J'
    TYPES = (
        (ONLINE_RESULT_CSV_FILE, 'online_result'),
        (MAIN_RESULT_CSV_FILE, 'result'),
        (LOG_FILE, 'log'),
        (TESTPLAN_FILE, 'testplan'),
        (JENKINS_BUILD_XML, 'jenkins_build_xml')
    )
    test = models.ForeignKey(Test, on_delete=models.CASCADE)
    path = models.TextField()
    file_type = models.CharField(
        max_length=12, choices=TYPES, default=MAIN_RESULT_CSV_FILE
    )
    file_size = models.IntegerField(default=0)
    last_analyzed = models.DateTimeField(default=None, null=True)
    last_analyzed_line = models.IntegerField(default=0)

    def archive(self):
        archive_path = self.path + '.zip'
        if os.path.exists(archive_path):
            os.remove(archive_path)

        logger.info('Move results file ' + self.path + ' to zip archive.')
        with zipfile.ZipFile(
            self.path + '.zip', 'w', zipfile.ZIP_DEFLATED,
            allowZip64=True
        ) as zip_file:
            zip_file.write(self.path, os.path.basename(self.path))
        os.remove(self.path)
        logger.info('File was packed, original file was deleted.')

    def generate_report(self):
        logger.info(f'File: {self.path}')
        if self.file_type == TestFile.MAIN_RESULT_CSV_FILE:
            self.parse_csv()

    def test_metadata(self):
        data = self.parse_build_xml()
        return data

    def parse_build_xml(self):
        """Parse Jenkins build.xml file and create test object

        Returns:
            test: test object
        """

        build_xml = ElementTree()
        build_parameters = []
        display_name = 'unknown'

        if os.path.isfile(self.path):
            build_xml.parse(self.path)
            build_tag = build_xml.getroot()

            for params in build_tag:
                if params.tag == 'actions':
                    parameters = params.find('.//parameters')
                    for parameter in parameters:
                        name = parameter.find('name')
                        value = parameter.find('value')
                        build_parameters.append([name.text, value.text])
                elif params.tag == 'startTime':
                    started_at = int(params.text)/1000
                elif params.tag == 'duration':
                    duration = int(params.text)
                elif params.tag == 'displayName':
                    display_name = params.text
        project_name = os.path.basename(up(up(up(self.path))))
        build_number = os.path.basename(up(self.path))
        project, created = Project.objects.get_or_create(
            name=project_name
        )
        data = {
            'name': display_name,
            'project': project,
            'status': Test.RUNNING,
            'started_at': datetime.datetime.fromtimestamp(started_at),
            'finished_at': datetime.datetime.fromtimestamp(
                started_at + duration
            ),
        }
        logger.debug('Parsed build.xml test data {}.'.format(data))
        return data

    def parse_csv(self, data_resolution='1Min', csv_file_fields=[]):
        """Parse Jmeter .csv result file and put aggregate data to database.

        Args:
            data_resolution (str, optional): Data resolution
            to keep in database. Defaults to '1Min'.
            csv_file_fields (list, optional): CSV-file Header  Defaults to [].
        """

        logger.info(f'Parsing file: {self.path}')
        if not os.path.exists(self.path):
            logger.error('File {} does not exists'.format(self.path))
            return

        data_resolution_id = TestDataResolution.objects.get(
            frequency=data_resolution
        ).id

        if not csv_file_fields:
            csv_file_fields = [
                'response_time', 'url', 'responseCode', 'success',
                'threadName',
                'failureMessage', 'grpThreads', 'allThreads'
            ]

        df = pd.DataFrame()
        if os.stat(self.path).st_size > 1000007777:
            logger.info("Executing a parse for a huge file")
            chunks = pd.read_table(
                self.path, sep=',', index_col=0, chunksize=3000000
            )
            for chunk in chunks:
                chunk.columns = csv_file_fields.split(',')
                chunk = chunk[~chunk['URL'].str.contains('exclude_')]
                df = df.append(chunk)
        else:
            df = pd.read_csv(
                self.path, index_col=0, low_memory=False
            )
            df.columns = csv_file_fields
            df = df[~df['url'].str.contains('exclude_', na=False)]
        self.archive()
        df.columns = csv_file_fields
        df.index = pd.to_datetime(dateconv((df.index.values / 1000)))
        num_lines = df['response_time'].count()
        logger.info('Number of lines in file: {}'.format(num_lines))
        unique_urls = df['url'].unique()
        for url in unique_urls:
            action, created = Action.objects.get_or_create(
                name=str(url),
                project=self.test.project
            )
            if TestActionData.objects.filter(
                action=action,
                test=self.test,
                data_resolution_id=data_resolution_id
            ).exists():
                logger.warning(
                    '{} data for test {} exists, skipping.'.format(
                        action.name, self.test.name
                    )
                )
                continue
            logger.info("Adding action data: {}".format(action.name))
            df_url = df[(df.url == action.name)]
            url_data = pd.DataFrame()
            df_url_gr_by_ts = df_url.groupby(
                pd.Grouper(freq=data_resolution))
            url_data['mean'] = df_url_gr_by_ts.response_time.mean()
            url_data['median'] = df_url_gr_by_ts.response_time.median()
            url_data['count'] = df_url_gr_by_ts.success.count()
            df_url_gr_by_ts_only_errors = df_url[(
                df_url.success == False
            )].groupby(pd.Grouper(freq=data_resolution))
            url_data[
                'errors'] = df_url_gr_by_ts_only_errors.success.count()
            url_data['test_id'] = self.test.id
            url_data['name'] = action.name
            output_json = json.loads(
                url_data.to_json(orient='index', date_format='iso'),
                object_pairs_hook=OrderedDict
            )
            for row in output_json:
                data = {
                    'timestamp': row,
                    'mean': output_json[row]['mean'],
                    'median': output_json[row]['median'],
                    'count': output_json[row]['count'],
                    'name': output_json[row]['name'],
                    'errors': output_json[row]['errors'],
                    'test_id': output_json[row]['test_id'],
                }
                test_action_data = TestActionData(
                    test=self.test,
                    action=action,
                    data_resolution_id=data_resolution_id,
                    data=data)
                test_action_data.save()

            if not TestActionAggregateData.objects.filter(
                action=action, test=self.test
            ).exists():
                url_agg_data = dict(
                    json.loads(df_url['response_time'].describe().to_json())
                )
                url_agg_data['99%'] = float(
                    df_url['response_time'].quantile(.99)
                )
                url_agg_data['90%'] = float(
                    df_url['response_time'].quantile(.90)
                )
                url_agg_data['weight'] = float(
                    df_url['response_time'].sum())
                url_agg_data['errors'] = float(df_url[(
                    df_url['success'] == False)]['success'].count())
                test_action_aggregate_data = TestActionAggregateData(
                    test=self.test,
                    action=action,
                    data=url_agg_data
                )
                test_action_aggregate_data.save()

        if not TestData.objects.filter(
            test=self.test,
            data_resolution_id=data_resolution_id
        ).exists():
            test_overall_data = pd.DataFrame()
            df_gr_by_ts = df.groupby(pd.Grouper(freq=data_resolution))
            test_overall_data['mean'] = df_gr_by_ts.response_time.mean()
            test_overall_data['median'] = df_gr_by_ts.response_time.median()
            test_overall_data['count'] = df_gr_by_ts.response_time.count()
            test_overall_data['test_id'] = self.test.id
            output_json = json.loads(
                test_overall_data.to_json(orient='index', date_format='iso'),
                object_pairs_hook=OrderedDict
            )
            for row in output_json:
                data = {
                    'timestamp': row,
                    'mean': output_json[row]['mean'],
                    'median': output_json[row]['median'],
                    'count': output_json[row]['count']
                }
                test_data = TestData(
                    test=self.test,
                    data_resolution_id=data_resolution_id,
                    data=data
                )
                test_data.save()

