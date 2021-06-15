from ltc.controller.models import LoadGenerator
from ltc.api.serializers import TestSerializer, LoadGeneratorSerializer
from django.shortcuts import render
from django.http import HttpResponse, HttpResponseBadRequest
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.reverse import reverse
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.decorators import api_view
from ltc.base.models import Test, Project


@api_view(['GET'])
def api_root(request, version):
    return Response({
        'events': reverse(
            'api.event-all', request=request, kwargs={'version': version}
        ),
        'tickets': reverse(
            'api.ticket-all', request=request, kwargs={'version': version}
        ),
    })


# Create your views here.
@api_view(['HEAD', 'GET'])
def api_health_check(request, version):
    response = HttpResponse()
    response['api'] = version
    return response


class ListCreateTestView(generics.ListCreateAPIView):
    """
    Returns a list of all tests. Creates a single test.
    """

    queryset = Test.objects.all()
    serializer_class = TestSerializer
    # permission_classes = (IsAuthenticated,)

    def get_queryset(self):
        queryset = Test.objects.all()
        status = self.request.query_params.getlist(
            'status[]', []
        )
        if status:
            queryset = queryset.filter(status__in=status)
        return queryset


class ListLoadgeneratorView(generics.ListCreateAPIView):
    """
    Returns a list of load generators.
    """

    queryset = LoadGenerator.objects.all()
    serializer_class = LoadGeneratorSerializer

    def get_queryset(self):
        queryset = LoadGenerator.objects.all()
        return queryset