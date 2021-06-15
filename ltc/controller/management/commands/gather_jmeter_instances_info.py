import logging
import time
import paramiko
from django.core.management.base import BaseCommand
from django.db.models.expressions import F

from ltc.controller.models import SSHKey
from ltc.controller.models import (JmeterInstance, JmeterInstanceStatistic,
                               TestRunning)
logger = logging.getLogger('django')


class Command(BaseCommand):
    def handle(self, *args, **options):
            # Connect and gather JAVA metrics from jmeter remote instances
        jmeter_instances = list(
            JmeterInstance.objects.annotate(
                hostname=F('load_generator__hostname'))
            .values('hostname', 'pid', 'project_id', 'threads_number',
                    'test_running_id'))
        for jmeter_instance in jmeter_instances:
            current_time = int(time.time() * 1000)
            hostname = jmeter_instance['hostname']
            project_id = jmeter_instance['project_id']
            pid = jmeter_instance['pid']
            threads_number = jmeter_instance['threads_number']
            test_running_id = jmeter_instance['test_running_id']

            # Estimate number of threads at this moment
            test_running = TestRunning.objects.get(id=test_running_id)
            test_rampup = float(test_running.rampup) * 1000
            test_started_at = float(test_running.started_at)
            current_time = float(time.time() * 1000)
            if (test_started_at + test_rampup) > current_time:
                threads_number = int(
                    threads_number *
                    ((current_time - test_started_at) / test_rampup))

            logger.info("threads_number: {};".format(threads_number))
            ssh_key = SSHKey.objects.get(default=True).path
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(hostname, username="root", key_filename=ssh_key)
            cmd1 = 'jstat -gc {}'.format(pid)
            stdin, stdout, stderr = ssh.exec_command(cmd1)
            i = 0
            process_data = {}
            header = str(stdout.readline()).split()
            data = str(stdout.readline()).split()
            for h in header:
                process_data[h] = data[i]
                i += 1
            logger.info("process_data: {}".format(str(process_data)))
            process_data['threads_number'] = threads_number
            # Need to sum this to get summary heap allocation:
            # S0U: Survivor space 0 utilization (kB).
            # S1U: Survivor space 1 utilization (kB).
            # EU: Eden space utilization (kB).
            # OU: Old space utilization (kB).
            JmeterInstanceStatistic(
                project_id=project_id, data=process_data).save()
            ssh.close()
