import fnmatch
import logging
import os

from django.db.models import FloatField, Sum
from django.db.models.expressions import F, RawSQL
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.generic import TemplateView

from ltc.base.models import Project
from ltc.controller.models import TestRunning, TestRunningData

logger = logging.getLogger('django')

# Create your views here.

def get_test_running_aggregate(test_running_id):
    '''
    Returns aggregate table for running test

    :return: dict
    '''

    test_running = get_object_or_404(TestRunning, id=test_running_id)
    test_running_aggregate = TestRunningData.objects.get(
        name='aggregate_table', test_running=test_running).data
    return test_running_aggregate


def online_test_success_rate(request, test_running_id):
    test_running = get_object_or_404(TestRunning, id=test_running_id)
    data = TestRunningData.objects.filter(
        name='data_over_time', test_running=test_running).annotate(
            errors=RawSQL("((data->>%s)::numeric)", ('errors', ))).annotate(
                count=RawSQL("((data->>%s)::numeric)", ('count', ))).aggregate(
                    count_sum=Sum(F('count'), output_field=FloatField()),
                    errors_sum=Sum(F('errors'), output_field=FloatField()))
    errors_percentage = round(data['errors_sum'] * 100 / data['count_sum'], 1)
    response = [{
        "fail_%": errors_percentage,
        "success_%": 100 - errors_percentage
    }]
    return JsonResponse(response, safe=False)


def online_test_response_codes(request, test_running_id):
    '''
    Response codes distribution for running test

    :return: JsonResponse
    '''

    test_running = get_object_or_404(TestRunning, id=test_running_id)
    response = []
    test_response_codes = TestRunningData.objects.get(
        name='response_codes', test_running=test_running).data
    for k in test_response_codes:
        response.append({
            'response_code': k,
            'count': test_response_codes.get(k)['count'],
        })
    return JsonResponse(response, safe=False)


def online_test_aggregate(request, test_running_id):
    '''
    Req/s for running test

    :return: Template
    '''

    aggregate = get_test_running_aggregate(test_running_id)
    return render(request, 'online_aggregate_table.html', {
        'aggregate_table': aggregate,
    })


def online_test_rps(request, test_running_id):
    '''
    Req/s for running test

    :return: JsonResponse
    '''

    test_running = get_object_or_404(TestRunning, id=test_running_id)
    data = list(
        TestRunningData.objects.filter(
            name='data_over_time', test_running=test_running).annotate(
                count=RawSQL("((data->>%s)::numeric)", (
                    'count', ))).order_by('-id').values('count'))

    response = [{
        "rps": int(data[1]['count'] / 60),
    }]
    return JsonResponse(response, safe=False)


def online_test_rtot(request, test_running_id):
    '''
    Response times over time for running test

    :return: JsonResponse
    '''

    test_running = get_object_or_404(TestRunning, id=test_running_id)
    response = []
    test_running_data_over_time = list(
        TestRunningData.objects.filter(
            name='data_over_time',
            test_running=test_running).values('data'))
    for d in test_running_data_over_time:
        response.append({
            'time': d['data']['timestamp'][:19],
            'rps': d['data']['count'] / 60,
            'avg': d['data']['avg'],
            'errors': d['data']['errors'],
        })
    return JsonResponse(response, safe=False)


def update(request, test_running_id):
    '''
    Update data for running test

    :return: JsonResponse
    '''

    test_running = get_object_or_404(TestRunning, id=test_running_id)
    test_running.update_data_frame()
    response = {
        "message": {
            "text": "Running test data was updated",
            "type": "success",
            "msg_params": {
                "test_running_id": test_running_id
            }
        }
    }
    return JsonResponse(response, safe=False)


class OnlinePage(TemplateView):
    def get(self, request, **kwargs):
        return render(request, 'online_page.html', context=None)
