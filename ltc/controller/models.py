import datetime
import json
import random
import re
import logging
import os

from collections import OrderedDict, defaultdict
import subprocess
from django.db.models.fields import related

import pandas as pd
import paramiko
from django.contrib.postgres.fields import JSONField
from django.db import models
from pylab import np

# Create your models here.
from ltc.base.models import TestData

dateconv = np.vectorize(datetime.datetime.fromtimestamp)
logger = logging.getLogger('django')


class SSHKey(models.Model):
    path = models.TextField(default='')
    description = models.TextField(default='')
    default = models.BooleanField(default=True)

class ProjectGraphiteSettings(models.Model):
    project = models.ForeignKey(to='base.Project', on_delete=models.CASCADE)
    name = models.CharField(max_length=1000, default="")
    value = models.CharField(max_length=10000, default="")

    class Meta:
        db_table = 'project_graphite_settings'
        unique_together = (('project', 'value'))


class Proxy(models.Model):
    port = models.IntegerField(default=0)
    pid = models.IntegerField(default=0)
    destination = models.CharField(max_length=200, default="https://dest")
    destination_port = models.IntegerField(default=443)
    delay = models.FloatField()
    started = models.BooleanField(default=False)

    class Meta:
        db_table = 'proxy'


class TestRunning(models.Model):
    '''Model for the running test'''
    project = models.ForeignKey(to='base.Project', on_delete=models.CASCADE)
    result_file_dest = models.CharField(max_length=200, default='')
    monitoring_file_dest = models.CharField(max_length=200, default='')
    testplan_file_dest = models.CharField(max_length=200, default='')
    log_file_dest = models.CharField(max_length=200, default='')
    display_name = models.CharField(max_length=100, default='')
    started_at = models.BigIntegerField()
    pid = models.IntegerField(default=0)
    jmeter_remote_instances = JSONField(null=True, blank=True)
    workspace = models.CharField(max_length=200, default='')
    is_running = models.BooleanField(default=False)
    build_number = models.IntegerField(default=0)
    rampup = models.IntegerField(default=0)  # ramp up test period in seconds
    # overall test duration (incl. rampup)
    duration = models.IntegerField(default=0)
    result_start_line = models.IntegerField(default=0)
    result_file_size = models.IntegerField(default=0)
    locked = models.BooleanField(default=False)
    build_path = models.CharField(max_length=600, default='')
    last_analyzed = models.DateTimeField(default=None, null=True)

    def update_data_frame(self):
        if self.locked is False:
            self.locked = True
            self.save()
            num_lines = sum(1 for line in open(self.result_file_dest))
            if self.result_start_line < num_lines - 10:
                read_lines = num_lines - self.result_start_line - 10
                skiprows = self.result_start_line
                df = pd.read_csv(
                    self.result_file_dest,
                    index_col=0,
                    low_memory=False,
                    skiprows=skiprows,
                    nrows=read_lines)
                self.result_start_line = (skiprows + read_lines)
                self.save()
                df.columns = [
                    'response_time', 'URL', 'responseCode', 'success',
                    'threadName', 'failureMessage', 'grpThreads', 'allThreads'
                ]

                df = df[~df['URL'].str.contains('exclude_')]
                df.index = pd.to_datetime(dateconv((df.index.values / 1000)))
                # update start line for the next parse

                # Response Codes
                group_by_response_codes = df.groupby('responseCode')
                update_df = pd.DataFrame()
                update_df['count'] = group_by_response_codes.success.count()
                update_df = update_df.fillna(0)
                output_json = json.loads(
                    update_df.to_json(orient='index', date_format='iso'),
                    object_pairs_hook=OrderedDict)
                new_data = {}
                for row in output_json:
                    new_data[row] = {'count': output_json[row]['count']}

                if not TestRunningData.objects.filter(
                        test_running_id=self.id, name="response_codes").exists():
                    test_running_data = TestRunningData(
                        test_running_id=self.id,
                        name="response_codes",
                        data=new_data)
                    test_running_data.save()
                else:
                    data = {}
                    test_running_data = TestRunningData.objects.get(
                        test_running_id=self.id, name="response_codes")
                    old_data = test_running_data.data
                    for k in new_data:
                        if k not in old_data:
                            old_data[k] = {'count': 0}
                        old_data[k] = {
                            'count': old_data[k]['count'] + new_data[k]['count']
                        }
                    test_running_data.data = old_data
                    test_running_data.save()

                # Aggregate table
                update_df = pd.DataFrame()
                group_by_url = df.groupby('URL')
                update_df = group_by_url.aggregate({
                    'response_time': np.mean
                }).round(1)
                update_df['maximum'] = group_by_url.response_time.max().round(1)
                update_df['minimum'] = group_by_url.response_time.min().round(1)
                update_df['count'] = group_by_url.success.count().round(1)
                update_df['errors'] = df[(
                    df.success == False)].groupby('URL')['success'].count()
                update_df['weight'] = group_by_url.response_time.sum()
                update_df = update_df.fillna(0)
                update_df.columns = [
                    'average', 'maximum', 'minimum', 'count', 'errors', 'weight'
                ]
                new_data = {}
                output_json = json.loads(
                    update_df.to_json(orient='index', date_format='iso'),
                    object_pairs_hook=OrderedDict)
                for row in output_json:
                    new_data[row] = {
                        'average': output_json[row]['average'],
                        'maximum': output_json[row]['maximum'],
                        'minimum': output_json[row]['minimum'],
                        'count': output_json[row]['count'],
                        'errors': output_json[row]['errors'],
                        'weight': output_json[row]['weight']
                    }
                if not TestRunningData.objects.filter(
                        test_running_id=self.id, name="aggregate_table").exists():
                    test_running_data = TestRunningData(
                        test_running_id=self.id,
                        name="aggregate_table",
                        data=new_data)
                    test_running_data.save()
                else:
                    data = {}
                    test_running_data = TestRunningData.objects.get(
                        test_running_id=self.id, name="aggregate_table")
                    old_data = test_running_data.data
                    for k in new_data:
                        if k not in old_data:
                            old_data[k] = {
                                'average': 0,
                                'maximum': 0,
                                'minimum': 0,
                                'count': 0,
                                'errors': 0,
                                'weight': 0
                            }
                        maximum = new_data[k][
                            'maximum'] if new_data[k]['maximum'] > old_data[k]['maximum'] else old_data[
                                k]['maximum']
                        minimum = new_data[k][
                            'minimum'] if new_data[k]['minimum'] < old_data[k]['minimum'] else old_data[
                                k]['minimum']
                        old_data[k] = {
                            'average':
                            (old_data[k]['weight'] + new_data[k]['weight']) /
                            (old_data[k]['count'] + new_data[k]['count']),
                            'maximum':
                            maximum,
                            'minimum':
                            minimum,
                            'count':
                            old_data[k]['count'] + new_data[k]['count'],
                            'errors':
                            old_data[k]['errors'] + new_data[k]['errors'],
                            'weight':
                            old_data[k]['weight'] + new_data[k]['weight'],
                        }
                    test_running_data.data = old_data
                    test_running_data.save()

                # Over time data
                update_df = pd.DataFrame()
                df_gr_by_ts = df.groupby(pd.Grouper(freq='1Min'))
                update_df['avg'] = df_gr_by_ts.response_time.mean()
                update_df['count'] = df_gr_by_ts.success.count()
                update_df['weight'] = df_gr_by_ts.response_time.sum()
                df_gr_by_ts_only_errors = df[(
                    df.success == False)].groupby(pd.Grouper(freq='1Min'))
                update_df['errors'] = df_gr_by_ts_only_errors.success.count()
                new_data = {}
                output_json = json.loads(
                    update_df.to_json(orient='index', date_format='iso'),
                    object_pairs_hook=OrderedDict)

                for row in output_json:
                    new_data = {
                        'timestamp': row,
                        'avg': output_json[row]['avg'],
                        'count': output_json[row]['count'],
                        'errors': output_json[row]['errors'],
                        'weight': output_json[row]['weight'],
                    }
                    if not TestRunningData.objects.filter(
                            test_running_id=self.id,
                            name="data_over_time").exists():
                        test_running_data = TestRunningData(
                            test_running_id=self.id,
                            name="data_over_time",
                            data=new_data)
                        test_running_data.save()
                    else:
                        data_over_time_data = TestRunningData.objects.filter(
                            test_running_id=self.id,
                            name="data_over_time").values()
                        update = False
                        for d in data_over_time_data:
                            if d['data']['timestamp'] == new_data['timestamp']:
                                d_id = d['id']
                                update = True
                        if update:
                            test_running_data = TestRunningData.objects.get(
                                id=d_id)
                            old_data = test_running_data.data
                            old_data['average'] = (
                                old_data['weight'] + new_data['weight']) / (
                                    old_data['count'] + new_data['count'])
                            old_data[
                                'count'] = old_data['count'] + new_data['count']
                            old_errors = 0 if old_data['errors'] is None else old_data['errors']
                            new_errors = 0 if new_data['errors'] is None else new_data['errors']
                            old_data[
                                'errors'] = old_errors + new_errors
                            old_data[
                                'weight'] = old_data['weight'] + new_data['weight']
                            test_running_data.data = old_data
                            test_running_data.save()
                        else:
                            test_running_data = TestRunningData(
                                test_running_id=self.id,
                                name="data_over_time",
                                data=new_data)
                            test_running_data.save()
                self.locked = False
                self.save()

    class Meta:
        db_table = 'test_running'


class TestRunningData(models.Model):
    test_running = models.ForeignKey(TestRunning, on_delete=models.CASCADE)
    name = models.CharField(max_length=200, default="")
    data = JSONField()

    class Meta:
        db_table = 'test_running_data'


class LoadGeneratorServer(models.Model):
    address = models.CharField(max_length=200, default="")
    ssh_key = models.ForeignKey(
        SSHKey, on_delete=models.CASCADE, null=True, blank=True)

    class Meta:
        db_table = 'load_generator_server'


class LoadGenerator(models.Model):
    hostname = models.CharField(max_length=200, default='', unique=True)
    num_cpu = models.CharField(max_length=200, default='')
    memory = models.CharField(max_length=200, default='')
    memory_free = models.CharField(max_length=200, default='')
    la_1 = models.CharField(max_length=200, default='')
    la_5 = models.CharField(max_length=200, default='')
    la_15 = models.CharField(max_length=200, default='')
    active = models.BooleanField(default=True)

    def status(self):
        status = 'success'
        reason = 'ok'
        if float(self.memory_free) < float(self.memory) * 0.5:
            status = 'warning'
            reason = 'memory'
        elif float(self.memory_free) < float(self.memory) * 0.1:
            status = 'danger'
            reason = 'low memory'
        if float(self.la_5) > float(self.num_cpu) / 2:
            status = 'warning'
            reason = 'average load'
        elif float(self.la_5) > float(self.num_cpu):
            status = 'danger'
            reason = 'high load'
        return {'status': status, 'reason': reason}

    def refresh(self):
        """SSH to loadgenerator and gather system data
        """

        logger.info('Refresh loadgenerator data: %s', self.hostname)
        ssh_key = SSHKey.objects.filter(default=True).first()
        if not ssh_key:
            logger.info('SSH-key is not set')
            return
        ssh_key = ssh_key.path
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        logger.info('ssh to %s', self.hostname)
        ssh.connect(self.hostname, username='root', key_filename=ssh_key)
        stdin, stdout, stderr = ssh.exec_command('cat /proc/meminfo')
        memory_free = str(
            int(re.search(
                'MemFree:\s+?(\d+)', str(stdout.readlines())
            ).group(1)) / 1024
        )
        stdin, stdout, stderr = ssh.exec_command('uptime')
        load_avg = re.search(
            'load average:\s+([0-9.]+?),\s+([0-9.]+?),\s+([0-9.]+)',
            str(stdout.readlines())
        )
        ssh.close()
        if load_avg:
            la_1 = load_avg.group(1)
            la_5 = load_avg.group(2)
            la_15 = load_avg.group(3)
        self.memory_free = memory_free
        self.la_1 = la_1
        self.la_5 = la_5
        self.la_15 = la_15
        self.active = True
        self.save()

    def start_jmeter_servers(
        self,
        jmeter_servers_per_generator,
        jmeter_servers_target_amount,
        test
    ):
        ssh_key = SSHKey.objects.get(default=True).path
        jmeter_path = f'/tmp/loadtest_{test.id}'
        logger.info(
            f'Rsyncing jmeter to remote host: {self.hostname}:{jmeter_path}'
        )
        p = subprocess.Popen(
            [
                "rsync", "-avH", f'{test.jmeter_path}/.' , "-e",
                f'ssh -i {ssh_key}',
                "root@{}:{}".format(self.hostname, jmeter_path),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE
        )
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(self.hostname, username='root', key_filename=ssh_key)
        # create an array of used ports
        cmd1 = 'netstat -tulpn | grep LISTEN'
        stdin, stdout, stderr = ssh.exec_command(cmd1)
        used_ports = []
        netstat_output = str(stdout.readlines())
        ports = re.findall('\d+\.\d+\.\d+\.\d+\:(\d+)', netstat_output)
        ports_ipv6 = re.findall('\:\:\:(\d+)', netstat_output)
        p.wait()
        for port in ports:
            used_ports.append(int(port))
        for port in ports_ipv6:
            used_ports.append(int(port))
        ssh.close()
        for _ in range(0, jmeter_servers_per_generator):
            port = int(random.randint(10000, 20000))
            while port in used_ports:
                port = int(random.randint(10000, 20000))
            jmeter_server = JmeterServer(
                test=test,
                loadgenerator=self,
                port=port,
                jmeter_path=jmeter_path,
            )
            jmeter_server.start(test, jmeter_servers_target_amount)


    def distribute_testplan(self, test_plan):
        ssh_key = SSHKey.objects.get(default=True).path
        test_plan_path = os.path.dirname(os.path.abspath(test_plan.path))
        logger.info(
            f'Rsyncing test plan files {test_plan_path} '
            f'to remote host: {self.hostname}'
        )
        p = subprocess.Popen(
            [
                "rsync", "-aH", f'{test_plan_path}/.' , "-e",
                f'ssh -i {ssh_key}',
                "root@{}:{}".format(self.hostname, test_plan_path + '/bin')
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE
        )
        p.wait()

    def gather_errors_data(self, test):
        hostname = self.hostname
        errors_dir = test.temp_path + '/bin/errors/'
        logger.info(f'Gathering errors data from: {hostname}:{errors_dir}')
        ssh_key = SSHKey.objects.get(default=True).path
        p = subprocess.Popen(
            [
                'scp', '-i', ssh_key, '-r',
                f'root@{hostname}:{errors_dir}',
                test.temp_path
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE
        )
        p.wait()

    def stop_jmeter_servers(self, test):
        jmeter_servers = JmeterServer.objects.filter(
            test=test, loadgenerator=self
        )
        if jmeter_servers.exists():
            ssh_key = SSHKey.objects.get(default=True).path
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(self.hostname, key_filename=ssh_key, username='root')
            for jmeter_server in jmeter_servers:
                logger.info(
                    f'Killing jmeter server on '
                    f'{self.hostname}:{jmeter_server.port} '
                    f'PID: {jmeter_server.pid}'
                )
                cmds = [f'kill -9 {jmeter_server.pid}']
                stdin, stdout, stderr = ssh.exec_command(' ; '.join(cmds))
                if not test.temp_path:
                    continue
                if not os.path.exists(test.temp_path):
                    continue
                logger.info(
                    f'Deleting tmp directory from {self.hostname}: '
                    f'{test.temp_path}'
                )
                cmds = [f'rm -rf {test.temp_path}']
                stdin, stdout, stderr = ssh.exec_command(' ; '.join(cmds))
            ssh.close()

    def __str__(self) -> str:
        return self.hostname

    class Meta:
        db_table = 'load_generator'


class JmeterServer(models.Model):
    test = models.ForeignKey(
        to='base.Test', on_delete=models.CASCADE
    )
    loadgenerator = models.ForeignKey(
        LoadGenerator, on_delete=models.CASCADE, related_name='jmeter_servers'
    )
    pid = models.IntegerField(default=0)
    port = models.IntegerField(default=0)
    jmeter_path = models.TextField(default='')
    threads = models.IntegerField(default=0)
    java_args = models.TextField(default='')
    local_args = models.TextField(default='')

    def java_args(self, memory):
        """Generate Java arg string for a new Jmeter server

        Args:
            memory (int): expected memory

        Returns:
            str: java args string
        """

        java_args = [
            '-server',
            '-Xms{memory}m',
            '-Xmx{memory}m',
            '-Xss228k',
            '-XX:+DisableExplicitGC',
            '-XX:+CMSClassUnloadingEnabled',
            '-XX:+UseCMSInitiatingOccupancyOnly',
            '-XX:CMSInitiatingOccupancyFraction=70',
            '-XX:+ScavengeBeforeFullGC',
            '-XX:+CMSScavengeBeforeRemark',
            '-XX:+UseConcMarkSweepGC',
            '-XX:+CMSParallelRemarkEnabled',
            '-Djava.net.preferIPv6Addresses=true',
            '-Djava.net.preferIPv4Stack=false'
        ]
        self.java_args = ' '.join(java_args).format(memory=memory)
        return self.java_args

    def start(self, test, jmeter_servers_target_amount):
        current_amount = JmeterServer.objects.filter(test=test).count()
        ssh_key = SSHKey.objects.get(default=True).path
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        hostname = self.loadgenerator.hostname
        logger.info(
            f'Starting jmeter instance on : {hostname}:{self.port}'
        )
        ssh.connect(
            self.loadgenerator.hostname, username="root", key_filename=ssh_key
        )
        local_args = ''
        for var in self.test.vars:
            if (
                any(v not in var for v in ['name', 'count', 'value']) or
                current_amount > int(var['count'])
            ):
                continue
            local_args += '-D{}={} '.format(
                var['name'], var['value']
            )
        for var in self.test.vars:
            if (
                any(v not in var for v in ['name', 'value']) or 'count' in var
            ):
                continue
            if var.get('distributed') == True:
                value = int(
                    float(var['value']) / jmeter_servers_target_amount
                )
                logger.info(
                    f'Estimated {var["name"]} per '
                    f'jmeter server: {value}'
                )
                local_args += ' -D{}={}'.format(
                    var['name'], value
                )
            else:
                local_args += ' -D{}={}'.format(
                    var['name'], var['value']
                )
        self.local_args = local_args
        start_jmeter_server_cmd = (
                f'nohup java {self.java_args(self.test.jmeter_malloc)} '
                f'-Duser.dir={self.jmeter_path}/bin/ -jar '
                f'"{self.jmeter_path}/bin/ApacheJMeter.jar" '
                f'-Jserver.rmi.ssl.disable=true '
                f'"-Djava.rmi.server.hostname={self.loadgenerator.hostname}" '
                f'-Dserver_port={self.port} -s -j jmeter-server.log '
                f'{self.local_args} > /dev/null 2>&1 '
            )
        logger.info(f'Using command: {start_jmeter_server_cmd}')
        command = 'echo $$; exec ' + start_jmeter_server_cmd
        cmds = ['cd {0}/bin/'.format(self.jmeter_path), command]
        stdin, stdout, stderr = ssh.exec_command(' ; '.join(cmds))
        pid = int(stdout.readline())
        self.pid = pid
        self.save()
        logger.info(
            f'New jmeter instance was added to database, pid: {self.pid},'
            f'port: {self.port}, test_id: {self.test.id}'
        )
        ssh.close()
        return True


class JmeterServerData(models.Model):
    project = models.ForeignKey(to='base.Project', on_delete=models.CASCADE)
    data = JSONField()

class ActivityLog(models.Model):
    date = models.DateTimeField(auto_now_add=True, blank=True)
    action = models.CharField(max_length=1000, default="")
    load_generator = models.ForeignKey(LoadGenerator, on_delete=models.CASCADE)
    data = JSONField()

    class Meta:
        db_table = 'activity_log'


class JmeterInstanceStatistic(models.Model):
    project = models.ForeignKey(to='base.Project', on_delete=models.CASCADE)
    data = JSONField()

    class Meta:
        db_table = 'jmeter_instance_statistic'


class JMeterTestPlanParameter(models.Model):
    p_name = models.CharField(max_length=200, default="")

    class Meta:
        db_table = 'jmeter_parameter'


class ScriptParameter(models.Model):
    p_name = models.CharField(max_length=200, default="")

    class Meta:
        db_table = 'script_parameter'
