import os
import re

import logging
from xml.etree.ElementTree import ElementTree
from django.http import JsonResponse
from django.shortcuts import render

# Create your views here.
from ltc.administrator.models import JMeterProfile, User, Configuration
from ltc.controller.models import SSHKey
from ltc.base.models import Test

logger = logging.getLogger('django')


def administrator_page(request):
    jds = JMeterProfile.objects.values()
    ssh_keys = SSHKey.objects.values()
    configuration_params = Configuration.objects.filter(secure=False).values()
    return render(request, 'administrator_page.html', {
        'configuration_params': configuration_params,
        'jds': jds,
        'ssh_keys': ssh_keys
    })


def new_jd_page(request):
    new_jd = JMeterProfile()
    new_jd.save()
    return render(request, 'new_jd.html', {
        'jd': new_jd,
    })


def delete_jd(request, jd_id):
    jd = JMeterProfile.objects.get(id=jd_id)
    jd.delete()
    response = {
        "message": {
            "text": "JMeter distro deleted",
            "type": "warning",
            "msg_params": {
                "id": jd_id
            }
        }
    }
    return JsonResponse(response, safe=False)


def create_jd(request, jd_id):
    jd = JMeterProfile.objects.get(id=jd_id)
    response = {}
    if request.method == 'POST':
        name = request.POST.get('name', '')
        path = request.POST.get('path', '')
        version = request.POST.get('version', '')
        jvm_args_main = request.POST.get('jvm_args_main', '')
        jvm_args_jris = request.POST.get('jvm_args_jris', '')
        jd.name = name
        jd.path = path
        jd.version = version
        jd.jvm_args_main = jvm_args_main
        jd.jvm_args_jris = jvm_args_jris
        jd.save()
        response = {
            "message": {
                "text": "JMeter distro was added",
                "type": "info",
                "msg_params": {
                    "id": jd_id
                }
            }
        }
    return JsonResponse(response, safe=False)


def new_ssh_key_page(request):
    new_ssh_key = SSHKey()
    new_ssh_key.save()
    return render(request, 'new_ssh_key.html', {
        'ssh_key': new_ssh_key,
    })


def delete_ssh_key(request, ssh_key_id):
    ssh_key = SSHKey.objects.get(id=ssh_key_id)
    ssh_key.delete()
    response = {
        "message": {
            "text": "SSH-key was deleted",
            "type": "warning",
            "msg_params": {
                "id": ssh_key_id
            }
        }
    }
    return JsonResponse(response, safe=False)


def create_ssh_key(request, ssh_key_id):
    ssh_key = SSHKey.objects.get(id=ssh_key_id)
    response = {}
    if request.method == 'POST':
        path = request.POST.get('path', '')
        description = request.POST.get('description', '')
        ssh_key.path = path
        ssh_key.description = description
        ssh_key.save()
        response = {
            "message": {
                "text": "SSH-key was added",
                "type": "info",
                "msg_params": {
                    "id": ssh_key_id
                }
            }
        }
    return JsonResponse(response, safe=False)

def new_parameter_page(request):
    new_parameter = Configuration()
    new_parameter.save()
    return render(request, 'new_parameter.html', {
        'new_parameter': new_parameter,
    })


def create_parameter(request, param_id):
    param = Configuration.objects.get(id=param_id)
    response = {}
    if request.method == 'POST':
        name = request.POST.get('name', '')
        description = request.POST.get('description', '')
        value = request.POST.get('value', '')
        param.name = name
        param.description = description
        param.value = value
        param.save()
        response = {
            "message": {
                "text": "A new parameter was created",
                "type": "info",
                "msg_params": {
                    "id": param_id
                }
            }
        }
    return JsonResponse(response, safe=False)

def delete_parameter(request, param_id):
    param = Configuration.objects.get(id=param_id)
    param.delete()
    response = {
        "message": {
            "text": "A parameter was deleted",
            "type": "warning",
            "msg_params": {
                "id": param_id
            }
        }
    }
    return JsonResponse(response, safe=False)


def test_data_refresh(request):
    builds_dir = "/var/lib/jenkins/jobs"
    # builds_dir="C:\\work\\reportdata"
    build_xml = ElementTree()
    rx = re.compile(r'/var/lib/jenkins/jobs/.+?/builds/\d+?/build\.xml')
    # rx = re.compile(r'C:\\work\\reportdata.+?\\builds\\\d+?\\build\.xml')
    logger.info("Refreshing test data")
    for root, dirs, files in os.walk(builds_dir):
        for file in files:
            if re.match(rx, os.path.join(root, file)):
                if os.stat(os.path.join(root, file)).st_size > 0:
                    build_parameters = []
                    display_name = "unknown"
                    description = ""
                    started_at = 0
                    duration = 0
                    build_xml_path = os.path.join(root, "build.xml")
                    if os.path.isfile(build_xml_path):
                        logger.info("Try to parse Jenkins build XML-file: {0}".
                                    format(build_xml_path))
                        with open(build_xml_path, "r") as fixfile:
                            data = fixfile.read()
                        data = data.replace("&#x", "")
                        with open(build_xml_path, "w") as fixfile:
                            fixfile.write(data)
                        build_xml.parse(build_xml_path)
                        build_tag = build_xml.getroot()

                        for params in build_tag:
                            if params.tag == 'actions':
                                parameters = params.find('.//parameters')
                                for parameter in parameters:
                                    name = parameter.find('name')
                                    value = parameter.find('value')
                                    build_parameters.append(
                                        {name.text: value.text})
                                userId = params.find('.//userId')
                                if userId is not None:
                                    started_by = userId.text
                                    if not User.objects.filter(
                                            login=started_by).exists():
                                        u = User(login=started_by)
                                        u.save()
                                        user_id = u.id
                                    else:
                                        u = User.objects.get(login=started_by)
                                        user_id = u.id
                                else:
                                    user_id = 0
                            elif params.tag == 'startTime':
                                started_at = int(params.text)
                            elif params.tag == 'displayName':
                                display_name = params.text
                            elif params.tag == 'duration':
                                duration = int(params.text)
                            elif params.tag == 'description':
                                description = params.text
                        if Test.objects.filter(path=root).exists():
                            test = Test.objects.get(path=root)
                            logger.info(
                                "Updating test, id: {0}".format(str(test.id)))
                            test.display_name = display_name
                            test.started_at = started_at
                            test.end_time = started_at + duration
                            test.description = description
                            test.parameters = build_parameters
                            test.started_by_id = user_id
                            test.save()
    response = {
        "message": {
            "text": "Tests information was updated.",
            "type": "info",
            "msg_params": {}
        }
    }

    return JsonResponse(response, safe=False)
