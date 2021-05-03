import os
import logging
import json
from django.conf import settings
from django.core.management.base import BaseCommand
from ltc.base.models import Test, TestFile, Project
from ltc.controller.views import generate_data
from argparse import ArgumentTypeError
from datetime import datetime, timedelta
from django.db.models import Q

logger = logging.getLogger('django')

class Command(BaseCommand):

    def handle(self, *args, **kwargs):
        tests = Test.objects.exclude(status__in=[
                Test.FINISHED, Test.FAILED
            ]
        ).filter(
            Q(
                last_active__lte=datetime.now()-timedelta(hours=1)
            ) |
            Q(last_active__isnull=True)
        )
        for test in tests:
            print(test.last_active)
            test.terminate()
