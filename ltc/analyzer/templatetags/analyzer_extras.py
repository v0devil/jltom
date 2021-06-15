from django import template
from django.contrib.auth.models import AnonymousUser

register = template.Library()

@register.inclusion_tag('analyzer/report/test_report/report.html',
                         takes_context=True)
def generate_test_report(context, test):
    response = {
        'test': test
    }
    return response


@register.inclusion_tag('analyzer/report/test_report/aggregate_table.html',
                         takes_context=True)
def aggregate_table(context, test):
    response = {
        'test': test,
        'aggregate_table': test.aggregate_table()
    }
    return response


@register.inclusion_tag('analyzer/report/test_report/overview.html',
                         takes_context=True)
def overview(context, test):
    response = {
        'test': test,
    }
    return response
