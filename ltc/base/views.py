import json
import logging
import os
import time
import zipfile
from datetime import datetime, timedelta
from os.path import basename

from django.contrib.auth.decorators import login_required
from django.db.models import Avg, FloatField, Func, Max, Min, Sum
from django.db.models.expressions import F, RawSQL
from django.shortcuts import render
from django.views.decorators.cache import never_cache
# Create your views here.
from django.views.generic import TemplateView
from django.views.static import serve
from ltc.administrator.models import Configuration, User
from ltc.analyzer.models import (
    TestActionAggregateData,
)
from ltc.base.models import Project, Test

logger = logging.getLogger('django')

@login_required
def index(request):
    last_tests_by_project = []
    # Only tests executed in last 30 days
    tests = Test.objects.filter(
        started_at__gt=datetime.now() - timedelta(days=30)
    ).annotate(
        latest_time=Max('started_at')
    )
    for test in tests:
        t = Test.objects.filter(
            project=test.project, started_at=test.latest_time
        )
        last_tests_by_project.append(t.first())
    last_tests = Test.objects.filter(
        project__enabled=True
    ).order_by(F('started_at').desc(nulls_last=True))[:10]
    tests = dashboard_compare_tests(last_tests)
    tests_by_project = dashboard_compare_tests(last_tests_by_project)
    return render(
        request, 'ltc/dashboard.html', {
            'last_tests': tests,
            'last_tests_by_project': tests_by_project,
        }
    )


def dashboard_compare_tests(tests):
    '''Return comparasion data for dashboard'''

    data = []
    for test in tests:
        project_tests = Test.objects.filter(
            project=test.project, id__lte=test.id
        ).order_by(F('started_at').desc(nulls_last=True))[:10]

        if project_tests.count() > 1:
            prev_test = project_tests[1]
        else:
            prev_test = test.id
        test_data = TestActionAggregateData.objects.filter(
            test=test
        ).annotate(
            errors=RawSQL("((data->>%s)::numeric)", ('errors',))
        ).annotate(
            count=RawSQL("((data->>%s)::numeric)", ('count',))
        ).annotate(
            weight=RawSQL("((data->>%s)::numeric)", ('weight',))
        ).aggregate(
            count_sum=Sum(F('count'), output_field=FloatField()),
            errors_sum=Sum(F('errors'), output_field=FloatField()),
            mean=Sum(F('weight')) / Sum(F('count'))
        )

        prev_test_data = TestActionAggregateData.objects.filter(
            test=prev_test
        ).annotate(
            errors=RawSQL("((data->>%s)::numeric)", ('errors',))
        ).annotate(
            count=RawSQL("((data->>%s)::numeric)", ('count',))
        ).annotate(
            weight=RawSQL("((data->>%s)::numeric)", ('weight',))
        ).aggregate(
            count_sum=Sum(F('count'), output_field=FloatField()),
            errors_sum=Sum(F('errors'), output_field=FloatField()),
            mean=Sum(F('weight')) / Sum(F('count'))
        )
        try:
            errors_percentage = (
                test_data['errors_sum'] * 100 / test_data['count_sum']
            )
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
        data.append({
            'test': test,
            'prev_test': prev_test,
            'test_data': test_data,
            'prev_test_data': prev_test_data,
            'success_requests': success_requests,
            'result': result,
        })
    return data


def zipDir(dirPath, zipPath):
    zipf = zipfile.ZipFile(zipPath , mode='w')
    lenDirPath = len(dirPath)
    for root, _ , files in os.walk(dirPath):
        for file in files:
            filePath = os.path.join(root, file)
            zipf.write(filePath , filePath[lenDirPath :] )
    zipf.close()

# @login_required
# def index(request):
#     return render(request, 'index.html', {
#         'projects': Project.objects.all().order_by('name')
#     })


def get_jmeter(request):
    jmeter_path = Configuration.objects.get(name='jmeter_path').value

    jmeter_zip_path = '/tmp/jmeter.zip'
    logger.info("Packing Jmeter distributive.")
    if not os.path.exists(jmeter_zip_path):
        zipDir(jmeter_path, jmeter_zip_path)
    return serve(request,
                 os.path.basename(jmeter_zip_path),
                 os.path.dirname(jmeter_zip_path))
