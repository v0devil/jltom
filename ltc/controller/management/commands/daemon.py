import os
import logging
from django.conf import settings
from django.core.management.base import BaseCommand
from controller.models import TestRunning
from controller.views import generate_data

logger = logging.getLogger('django')

class Command(BaseCommand):
    def handle(self, *args, **options):
        for t in TestRunning.objects.all():
            logger.info('[DAEMON] Generating data from file {}'.format(t.result_file_dest))
            generate_data(t.id, mode='online')
