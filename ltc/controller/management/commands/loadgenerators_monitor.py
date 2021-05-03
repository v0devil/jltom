import logging
import threading

from django.core.management.base import BaseCommand

from adminapi.dataset import Query
from ltc.controller.models import LoadGenerator

logger = logging.getLogger('django')


def refresh(loadgenerator):
    """Update loadgenerator data

    Args:
        loadgenerator (LoadGenerator): loadgenerator hostname
    """

    loadgenerator.refresh()


class Command(BaseCommand):

    def handle(self, *args, **options):
        threads = []

        hosts = Query({
            'function': 'loadgenerator',
            'state': 'online',
            'servertype': 'vm',
        }, ['num_cpu', 'memory', 'hostname'])

        for host in hosts:
            print(host)
            loadgenerator, _ = LoadGenerator.objects.update_or_create(
                hostname=host['hostname'],
                defaults={
                    'num_cpu': host['num_cpu'],
                    'memory': host['memory'],
                },
            )
            t = threading.Thread(
                target=refresh, args=(
                    loadgenerator,
                )
            )
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
