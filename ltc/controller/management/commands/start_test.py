import os
import logging
import json
from django.conf import settings
from django.core.management.base import BaseCommand
from ltc.base.models import Test, TestFile, Project
from ltc.controller.views import generate_data
from argparse import ArgumentTypeError
logger = logging.getLogger('django')

class Command(BaseCommand):

    def add_arguments(self, parser):

        parser.add_argument(
            '--jmeter_path',
            type=str,
            help='JMeter path'
        )

        parser.add_argument(
            '--testplan',
            type=str,
            help='testplan'
        )

        parser.add_argument(
            '--project',
            type=str,
            help='Project'
        )

        parser.add_argument(
            '--threads',
            type=str,
            help='Amount of threads'
        )

        parser.add_argument(
            '--duration',
            type=int,
            help='Timeout'
        )

        parser.add_argument(
            '--thread_size',
            type=str,
            help='Thread size'
        )

        parser.add_argument(
            '--vars',
            type=str,
            help='Thread size'
        )


    def handle(self, *args, **kwargs):
        test = Test()
        if 'project' in kwargs:
            project, _ = Project.objects.get_or_create(name=kwargs['project'])
            test.project = project
        if 'threads' in kwargs:
            test.threads = int(kwargs['threads'])
        if 'jmeter_path' in kwargs:
            test.jmeter_path = kwargs['jmeter_path']
        if 'vars' in kwargs:
            vars = json.loads( kwargs['vars'])
            test.vars = vars
        if kwargs.get('duration'):
            test.duration = kwargs['duration']
        test.status = Test.CREATED
        test.save()
        test.prepare_test_plan(kwargs['testplan'])
        loadgenerators = {}
        jmeter_server_amount = 0
        if 'thread_size' in kwargs:
            loadgenerators, jmeter_server_amount = test.find_loadgenerators(
                int(kwargs['thread_size'])
            )
        test.prepare_jmeter()
        test.prepare_jmeter_servers(
            loadgenerators, jmeter_server_amount, kwargs['testplan']
        )
        test.start()
        test.analyze()
        test.cleanup()
        test.project.generate_confluence_report()
