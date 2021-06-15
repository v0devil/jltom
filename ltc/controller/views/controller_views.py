import json
import os
import subprocess
import sys
import psutil
import time
import logging
import re
import datetime
from subprocess import call
from sys import platform as _platform
from django.db.models.expressions import F, RawSQL, Value
import select

import shutil

from ltc.administrator.models import JMeterProfile

if _platform == "linux" or _platform == "linux2":
    import resource

import paramiko
import tempfile
from ltc.controller.graphite import graphiteclient
from ltc.analyzer.models import Server, ServerMonitoringData, TestData
from ltc.base.models import Project, Test
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render

from ltc.controller.models import JmeterServer, LoadGenerator, SSHKey, Proxy, TestRunning, LoadGeneratorServer, JMeterTestPlanParameter, ScriptParameter, ProjectGraphiteSettings
from django.db.models import Sum, Avg, Max, Min, FloatField, IntegerField
from ltc.administrator.models import Configuration

logger = logging.getLogger('django')


def setlimits():
    logger.info("Setting resource limit in child (pid %d)" % os.getpid())
    if _platform == "linux" or _platform == "linux2":
        resource.setrlimit(resource.RLIMIT_NOFILE, (131072, 131072))


def change_proxy_delay(request, proxy_id):
    if request.method == 'POST':
        delay = request.POST.get('delay', '0')
        p = Proxy.objects.get(id=proxy_id)
        p.delay = delay
        p.save()
        response = [{
            "message": "proxy`s delay was changed",
            "proxy_id": proxy_id,
            "delay": delay
        }]
    return JsonResponse(response, safe=False)


def parse_results(request):
    response = []
    if request.method == 'POST':
        results_dir = request.POST.get('results_dir', '/tmp')
        parse_results_in_dir(results_dir)
    return JsonResponse(response, safe=False)


def stop_proxy(request, proxy_id):
    p = Proxy.objects.get(id=proxy_id)
    if p.pid != 0:
        try:
            proxy_process = psutil.Process(p.pid)
            proxy_process.terminate()
        except psutil.NoSuchProcess:
            logger.info("Process {} is not exists anymore".format(p.pid))
        p.started = False
        p.pid = 0
        p.save()
        proxy = Proxy.objects.filter(id=proxy_id).values()
        response = [{"message": "proxy was stopped", "proxy": list(proxy)[0]}]
    else:
        proxy = Proxy.objects.filter(id=proxy_id).values()
        response = [{
            "message": "proxy is already stopped",
            "proxy": list(proxy)[0]
        }]
    return JsonResponse(response, safe=False)


def get_load_generator_data(request, load_generator_id):
    load_generator = LoadGenerator.objects.get(id=load_generator_id)
    jmeter_instances = JmeterServer.objects.annotate(
        project_name=F('project__project_name')).filter(
            load_generator_id=load_generator_id).values(
                'pid', 'port', 'jmeter_dir', 'project_name', 'threads_number',
                'java_args')
    return render(request, 'load_generator_page.html', {
        'load_generator': load_generator,
        'jmeter_instances': jmeter_instances,
    })



def start_proxy(request, proxy_id):
    p = Proxy.objects.get(id=proxy_id)
    if request.method == 'POST':
        port = request.POST.get('port', '0')
        destination = request.POST.get('destination', 'https://empty')
        destination_port = request.POST.get('destination_port', '443')
        delay = request.POST.get('delay', '0')
        p.delay = delay
        p.port = port
        p.destination = destination
        p.destination_port = destination_port
        p.started = True
        p.save()
    out = open('/var/lib/ltc/logs/proxy_output_' + str(proxy_id), 'w')
    proxy_script = "../proxy.py"
    #if _platform == "linux" or _platform == "linux2":
    #	proxy_script = "proxy_linux.py"
    env = dict(os.environ, **{'PYTHONUNBUFFERED': '1'})
    proxy_process = subprocess.Popen(
        [
            sys.executable, proxy_script,
            str(p.port), p.destination, p.destination_port,
            str(p.id)
        ],
        cwd=os.path.dirname(os.path.realpath(__file__)),
        stdout=out,
        stderr=out,
        env=env,
        preexec_fn=setlimits)
    logger.info("Proxy pid:" + str(proxy_process.pid))
    p = Proxy.objects.get(id=proxy_id)
    p.pid = proxy_process.pid
    p.save()
    p = Proxy.objects.filter(id=proxy_id).values()
    response = [{"message": "proxy was started", "proxy": list(p)[0]}]
    return JsonResponse(response, safe=False)


def add_proxy(request):
    if request.method == 'POST':
        port = request.POST.get('port', '0')
        destination = request.POST.get('destination', 'https://empty')
        destination_port = request.POST.get('destination_port', '443')
        delay = request.POST.get('delay', '0')
        p = Proxy(
            delay=delay,
            port=port,
            destination=destination,
            destination_port=destination_port,
            started=False,
            pid=0)
        p.save()
        new_id = p.id
        proxy = Proxy.objects.filter(id=new_id).values()
        response = [{"message": "proxy was started", "proxy": list(proxy)[0]}]
    return JsonResponse(response, safe=False)


def new_proxy_page(request):
    return render(request, "new_proxy_page.html")


def new_jri_page(request, project_id):
    ssh_keys = SSHKey.objects.values()
    return render(request, "new_jri.html",
                  {'project_id': project_id,
                   'ssh_keys': ssh_keys})


def new_jmeter_param_page(request, project_id):
    return render(request, "new_jmeter_param.html", {'project_id': project_id})


def new_script_param_page(request, project_id):
    return render(request, "new_script_param.html", {'project_id': project_id})


def jri_list(request, project_id):
    project = Project.objects.values().get(id=project_id)
    jris = json.loads(
        json.dumps(
            project['jmeter_remote_instances'], indent=4, sort_keys=True))
    if jris is None:
        jris = []
    return render(request, 'jri.html', {'jris': jris, 'project': project})

def jmeter_param_delete(request, project_id, param_id):
    project = Project.objects.values().get(id=project_id)
    jmeter_params = json.loads(
        json.dumps(project['jmeter_parameters'], indent=4, sort_keys=True))
    for i in xrange(len(jmeter_params)):
        if int(jmeter_params[i]['id']) == int(param_id):
            jmeter_params.pop(i)
            break
    project = Project.objects.get(id=project_id)
    project.jmeter_parameters = jmeter_params
    project.save()
    response = [{
        "message": "JMeter parameter was deleted",
        "project_id": project_id,
        "param_id": param_id
    }]
    return JsonResponse(response, safe=False)


def script_param_delete(request, project_id, param_id):
    project = Project.objects.values().get(id=project_id)
    script_params = json.loads(
        json.dumps(project['script_parameters'], indent=4, sort_keys=True))
    for i in xrange(len(script_params)):
        if int(script_params[i]['id']) == int(param_id):
            script_params.pop(i)
            break
    project = Project.objects.get(id=project_id)
    project.script_parameters = script_params
    project.save()
    response = [{
        "message": "Script parameter was deleted",
        "project_id": project_id,
        "param_id": param_id
    }]
    return JsonResponse(response, safe=False)


def jri_delete(request, project_id, jri_id):
    project = Project.objects.values().get(id=project_id)
    jris = json.loads(
        json.dumps(
            project['jmeter_remote_instances'], indent=4, sort_keys=True))
    for i in xrange(len(jris)):
        if int(jris[i]['id']) == int(jri_id):
            jris.pop(i)
            break
    project = Project.objects.get(id=project_id)
    project.jmeter_remote_instances = jris
    project.save()
    response = {
        "message": {
            "text": "JMeter remote instance was deleted from project",
            "type": "success",
            "msg_params": {
                "project_id": project_id
            }
        }
    }
    return JsonResponse(response, safe=False)


def add_jmeter_param(request, project_id):
    project = Project.objects.values().get(id=project_id)
    response = []
    if request.method == 'POST':
        p_id = 0
        p_name = request.POST.get('name', 'name').strip()
        if not JMeterTestPlanParameter.objects.filter(p_name=p_name).exists():
            p = JMeterTestPlanParameter(p_name=p_name)
            p.save()
            p_id = p.id
        else:
            p = JMeterTestPlanParameter.objects.get(p_name=p_name)
            p_id = p.id

        value = request.POST.get('value', '1')
        jmeter_params = json.loads(
            json.dumps(project['jmeter_parameters'], indent=4, sort_keys=True))
        if jmeter_params is None:

            jmeter_params = list([{
                'id': p_id,
                'p_name': p_name,
                'value': value,
                'comment': ""
            }])
            response = [{
                "message": "JRI was added",
                'id': p_id,
                'p_name': p_name,
                'value': value,
                'comment': ""
            }]
        else:
            already_in_list = False
            for jmeter_param in jmeter_params:
                if jmeter_param.get('p_name') == p_name:
                    already_in_list = True
            if already_in_list:
                response = [{
                    "message":
                    "Jmeter parameter with the same "
                    "name is already exists",
                }]
            else:
                jmeter_params.append({
                    'id': p_id,
                    'p_name': p_name,
                    'value': value,
                    'comment': ""
                })
                response = [{
                    "message": "Jmeter parameter was added",
                    'id': p_id,
                    'p_name': p_name,
                    'value': value,
                    'comment': ""
                }]
        project = Project.objects.get(id=project_id)
        project.jmeter_parameters = jmeter_params
        project.save()
    return JsonResponse(response, safe=False)


def add_script_param(request, project_id):
    project = Project.objects.values().get(id=project_id)
    response = []
    if request.method == 'POST':
        p_id = 0
        p_name = request.POST.get('name', 'name').strip()
        if not ScriptParameter.objects.filter(p_name=p_name).exists():
            p = ScriptParameter(p_name=p_name)
            p.save()
            p_id = p.id
        else:
            p = ScriptParameter.objects.get(p_name=p_name)
            p_id = p.id

        value = request.POST.get('value', '1')
        script_params = json.loads(
            json.dumps(project['script_parameters'], indent=4, sort_keys=True))
        if script_params is None:

            script_params = list([{
                'id': p_id,
                'p_name': p_name,
                'value': value,
                'comment': ""
            }])
            response = [{
                "message": "Script parameter was added",
                'id': p_id,
                'p_name': p_name,
                'value': value,
                'comment': ""
            }]
        else:
            already_in_list = False
            for script_param in script_params:
                if script_param.get('p_name') == p_name:
                    already_in_list = True
            if already_in_list:
                response = [{
                    "message":
                    "Script parameter with the same "
                    "name is already exists",
                }]
            else:
                script_params.append({
                    'id': p_id,
                    'p_name': p_name,
                    'value': value,
                    'comment': ""
                })
                response = [{
                    "message": "Script parameter was added",
                    'id': p_id,
                    'p_name': p_name,
                    'value': value,
                    'comment': ""
                }]
        project = Project.objects.get(id=project_id)
        project.script_parameters = script_params
        project.save()
    return JsonResponse(response, safe=False)


def script_header(project_id):
    script_header = "#!/bin/bash\n"
    project = Project.objects.values().get(id=project_id)
    script_params = project['script_parameters']
    if script_params is None:
        script_params = []

    script_params_string = ""
    for script_param in script_params:
        script_params_string += "{0}={1}\n".format(
            script_param.get('p_name'), script_param.get('value'))
    script_header += script_params_string
    return script_header


def script_pre_configure(request, project_id):
    project = Project.objects.values().get(id=project_id)
    script_type = "pre"
    return render(request, 'script_config.html', {
        'project': project,
        'script_type': script_type,
        'script_header': script_header(project_id)
    })


def script_post_configure(request, project_id):
    project = Project.objects.values().get(id=project_id)
    script_type = "post"
    return render(request, 'script_config.html',
                  {'project': project,
                   'script_type': script_type})


def script_pre_save(request, project_id):
    project = Project.objects.get(id=project_id)
    response = []
    if request.method == 'POST':
        script = request.POST.get('script', '')
        project.script_pre = script
        project.save()
        response = [{"message": "Pre-test script was changed"}]
    return JsonResponse(response, safe=False)


def script_post_save(request, project_id):
    project = Project.objects.get(id=project_id)
    response = []
    if request.method == 'POST':
        script = request.POST.get('script', '')
        project.script_post = script
        project.save()
        response = [{"message": "Post-test script was changed"}]
    return JsonResponse(response, safe=False)


def add_jri(request, project_id):
    project = Project.objects.values().get(id=project_id)
    response = []
    if request.method == 'POST':
        server_id = 0
        address = request.POST.get('address', '127.0.0.1')
        ssh_key_id = request.POST.get('ssh_key_id', '0')
        if not LoadGeneratorServer.objects.filter(address=address).exists():
            s = LoadGeneratorServer(address=address, ssh_key_id=ssh_key_id)
            s.save()
            server_id = s.id
        else:
            s = LoadGeneratorServer.objects.get(address=address)
            server_id = s.id
            ssh_key_id = s.ssh_key_id
        count = request.POST.get('count', '1')
        jris = json.loads(
            json.dumps(
                project['jmeter_remote_instances'], indent=4, sort_keys=True))
        if jris is None:
            jris = list([{
                'id': server_id,
                'address': address,
                'ssh_key_id': ssh_key_id,
                'count': count
            }])

            response = {
                "message": {
                    "text": "JMeter remote instance was added to project",
                    "type": "success",
                    "msg_params": {
                        "server_id": server_id
                    }
                }
            }

        else:
            already_in_list = False
            for jri in jris:
                if jri.get('address') == address:
                    already_in_list = True
            if already_in_list:
                response = [{
                    "message": "JRI was already in list",
                }]
            else:

                jris.append({
                    'id': server_id,
                    'ssh_key_id': ssh_key_id,
                    'address': address,
                    'count': count
                })

                response = {
                    "message": {
                        "text": "JMeter remote instance was added to project",
                        "type": "success",
                        "msg_params": {
                            "server_id": server_id
                        }
                    }
                }

        project = Project.objects.get(id=project_id)
        project.jmeter_remote_instances = jris
        project.save()
    return JsonResponse(response, safe=False)


def controller_page(request):
    projects_list = list(Project.objects.values())
    proxies_list = Proxy.objects.values()
    for proxy in proxies_list:
        if proxy["pid"] != 0:
            if not psutil.pid_exists(proxy["pid"]):
                p = Proxy.objects.get(id=proxy["id"])
                p.pid = 0
                p.started = False
                p.save()
    proxies_list = Proxy.objects.values()
    return render(request, 'controller_page.html', {
        'projects_list': projects_list,
        'running_tests_list': [],
        'proxies_list': list(proxies_list)
    })


def configure_test(request, project_id):
    project = Project.objects.values().get(id=project_id)
    jmeter_profiles = JMeterProfile.objects.values()
    jmeter_parameters = json.loads(
        json.dumps(project['jmeter_parameters'], indent=4, sort_keys=True))
    jris = json.loads(
        json.dumps(
            project['jmeter_remote_instances'], indent=4, sort_keys=True))
    return render(request, 'configure_test_page.html', {
        'project': project,
        'jmeter_parameters': jmeter_parameters,
        'jmeter_profiles': jmeter_profiles
    })


def create_project_page(request):
    new_project = Project()
    new_project.save()
    return render(request, 'create_project_page.html', {
        'project': new_project,
    })


def create_project(request, project_id):
    project = Project.objects.get(id=project_id)
    response = []
    if request.method == 'POST':
        project_name = request.POST.get('project_name', '')
        jmeter_destination = request.POST.get('jmeter_destination', '')
        test_plan_destination = request.POST.get('test_plan_destination', '')
        project.jmeter_destination = jmeter_destination
        project.test_plan_destination = test_plan_destination
        project.project_name = project_name
        project.save()
    return JsonResponse(response, safe=False)


def running_test_log(request, running_test_id, log_type):
    running_test = TestRunning.objects.get(id=running_test_id)
    running_test_logs_dir = os.path.join(running_test.workspace, 'logs/')
    log_file_dest = os.path.join(running_test_logs_dir, log_type + '.log')
    log = []
    f = open(log_file_dest, mode='rb')
    log = f.read()
    f.close()
    return HttpResponse(log, content_type="text/plain")


def show_log_page(request, running_test_id):
    return render(request, 'running_test_log.html',
                  {"running_test_id": running_test_id})


def delete_project(request, project_id):
    project = Project.objects.get(id=project_id)
    project.delete()
    response = [{"message": "project was deleted", "test_id": project_id}]
    return JsonResponse(response, safe=False)


def jmeter_params_list(request, project_id):
    project = Project.objects.values().get(id=project_id)
    jmeter_parameters = json.loads(
        json.dumps(project['jmeter_parameters'], indent=4, sort_keys=True))
    return render(request, 'jmeter_param.html', {
        'project': project,
        'jmeter_parameters': jmeter_parameters,
    })


def script_params_list(request, project_id):
    project = Project.objects.values().get(id=project_id)
    script_parameters = json.loads(
        json.dumps(project['script_parameters'], indent=4, sort_keys=True))
    return render(request, 'script_param.html', {
        'project': project,
        'script_parameters': script_parameters,
    })


def start_test(request, project_id):
    response = []
    project = Project.objects.get(id=project_id)
    pid = 0
    started_at = 0
    display_name = ""
    test_id = 0
    if request.method == 'POST':
        # Create dir for new test:
        last_test_id = 0
        if Test.objects.filter(project_id=project_id).exists():
            last_test_id = Test.objects.filter(
                project_id=project_id).order_by("-id")[0].id
        running_test_dir = os.path.join('/tmp/', 'ltc', project.project_name,
                                        str(last_test_id + 1))
        running_test_results_dir = os.path.join(running_test_dir, 'results/')
        running_test_logs_dir = os.path.join(running_test_dir, 'logs/')
        running_test_testplan_dir = os.path.join(running_test_dir, 'testplan/')

        running_test_log_file_destination \
            = os.path.join(running_test_logs_dir,
                           "main.log")
        script_pre_log_file_destination \
            = os.path.join(running_test_logs_dir,
                           "script_pre.log")

        result_file_destination = os.path.join(running_test_results_dir,
                                               "results.jtl")
        if os.path.exists(running_test_dir):
            shutil.rmtree(running_test_dir)
        os.makedirs(running_test_dir)
        os.mkdir(running_test_testplan_dir, 777)
        os.mkdir(running_test_logs_dir, 777)
        os.mkdir(running_test_results_dir, 777)

        test_plan_params_flag = ""
        test_plan_params_str = ""
        jris_str = "-R "
        jris = json.loads(
            json.dumps(
                project.jmeter_remote_instances, indent=4, sort_keys=True))
        if jris is None:
            # If not remote
            test_plan_params_flag = " -J"
        else:
            # If remote
            test_plan_params_flag = " -G"

        jmeter_params = json.loads(
            json.dumps(project.jmeter_parameters, indent=4, sort_keys=True))
        test_plan_params_arg = []
        test_plan_params_str = ''
        if jmeter_params:
            for jmeter_param in jmeter_params:
                test_plan_params_arg.append(
                                    test_plan_params_flag + \
                                    jmeter_param.get('p_name') + \
                                    '=' + \
                                    jmeter_param.get('value'))
                test_plan_params_str += test_plan_params_flag + \
                                    jmeter_param.get('p_name') + \
                                    '=' + \
                                    jmeter_param.get('value')
        jmeter_profile_id = request.POST.get('jmeter_profile_id', '')
        jmeter_profile = JMeterProfile.objects.get(id=jmeter_profile_id)
        test_plan_destination = request.POST.get('test_plan_destination', '{}')
        display_name = request.POST.get('test_display_name', '{}')

        project.jmeter_profile_id = jmeter_profile_id
        project.test_plan_destination = test_plan_destination
        project.save()
        prepare_test_plan(
            running_test_testplan_dir,
            test_plan_destination,
            result_file_destination
        )
        #project.jmeter_parameters = json.loads(jmeter_parameters)
        java_exec = 'java'
        running_test_jris = []
        if jris is not None:
            for jri in jris:
                hostname = jri.get('address')
                ssh_key_id = int(jri.get('ssh_key_id'))
                count = int(jri.get('count'))
                logger.info( "Try to connect via SSH to {0} {1} times". \
                    format(hostname, str(count)))
                for i in range(1, count + 1):
                    port = 10000 + i
                    jris_str += '{0}:{1},'.format(hostname, str(port))
                    logger.info( "{0} time". \
                        format(i))
                    ssh_key = SSHKey.objects.get(id=ssh_key_id).path
                    ssh = paramiko.SSHClient()
                    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh.connect(hostname, key_filename=ssh_key)
                    logger.info('Executing SSH commands.')
                    cmds = [
                        'cd {0}/bin/'.format(jmeter_profile.path),
                        'DIRNAME=`dirname -- $0`'
                    ]

                    stdin, stdout, stderr = ssh.exec_command(' ; '.join(cmds))
                    output = stdout.read()
                    text_file = open(running_test_log_file_destination, "wb")
                    text_file.write(output)
                    text_file.close()
                    run_jmeter_server_cmd = 'nohup java {0} -jar "{1}/bin/ApacheJMeter.jar" "$@" "-Djava.rmi.server.hostname={2}" -Dserver_port={3} -s -Jpoll={4} > /dev/null 2>&1 '.\
                        format(jmeter_profile.jvm_args_jris, jmeter_profile.path, hostname, str(port), str(i))
                    command = 'echo $$; exec ' + run_jmeter_server_cmd
                    stdin, stdout, stderr = ssh.exec_command(command)
                    pid = int(stdout.readline())
                    running_test_jris.append({
                        'hostname': hostname,
                        'pid': pid
                    })
                    logger.info("Started remote Jmeter instance, pid: " +
                                str(pid))
                    ssh.close()

        jris_str = jris_str.rstrip(',')

        args = [java_exec, '-jar']
        args += splitstring(jmeter_profile.jvm_args_main)
        args += [
            jmeter_profile.jmeter_jar_path(), "-n", "-t",
            test_plan_destination, '-j', running_test_log_file_destination,
            jris_str,
            test_plan_params_str.lstrip(),
            '-Jjmeter.save.saveservice.default_delimiter=,'
        ]
        args += splitstring(test_plan_params_str)
        #pre-test script execution:
        header = script_header(project_id)
        body = project.script_pre
        if body is not None:
            script = header + body
            with open(script_pre_log_file_destination, 'w') as f:
                rc = call(script, shell=True, stdout=f)
        if _platform == "linux" or _platform == "linux2":
            jmeter_process = subprocess.Popen(
                args,
                executable=java_exec,
                stdout=subprocess.PIPE,
                preexec_fn=os.setsid,
                close_fds=True)
        else:
            jmeter_process = subprocess.Popen(
                args,
                executable=java_exec,
            )
        pid = jmeter_process.pid
        started_at = int(time.time() * 1000)
        t = TestRunning(
            pid=pid,
            started_at=started_at,
            result_file_dest=result_file_destination,
            monitoring_file_dest="",
            display_name=display_name,
            log_file_dest=running_test_log_file_destination,
            project_id=project_id,
            jmeter_remote_instances=running_test_jris,
            workspace=running_test_dir,
            build_number=0,
            is_running=True
        )
        t.save()
        test_id = t.id

    project = Project.objects.filter(id=project_id).values()
    response = {
        "message": {
            "text": "Test was started",
            "type": "success",
            "msg_params": {
                "pid": pid
            }
        }
    }
    wait_for_finished_test(request, t, jmeter_process)
    return JsonResponse(response, safe=False)


def wait_for_finished_test(request, t, jmeter_process):
    ''' Check if test is still running'''
    while t.is_running:
        retcode = jmeter_process.poll()
        logger.info(
            "Check if JMeter process is still exists, current state: {0}".
            format(retcode))
        if retcode is not None:
            logger.info(
                "JMeter process finished with exit code: {0}".format(retcode))
            t.is_running = False
            stop_test(request, t.id)
            break
        time.sleep(10)


def stop_test(request, running_test_id):
    running_test = TestRunning.objects.get(id=running_test_id)
    workspace = running_test.workspace
    jris = json.loads(
        json.dumps(
            running_test.jmeter_remote_instances, indent=4, sort_keys=True))
    if jris is not None:
        for jri in jris:
            hostname = jri.get('hostname')
            pid = int(jri.get('pid'))
            ssh_key_id = int(
                LoadGeneratorServer.objects.get(address=hostname).ssh_key_id)
            ssh_key = SSHKey.objects.get(id=ssh_key_id).path
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(hostname, key_filename=ssh_key)
            cmds = ['kill -9 {0}'.format(str(pid))]
            stdin, stdout, stderr = ssh.exec_command(' ; '.join(cmds))
    response = []
    generate_data(running_test.id)
    try:
        proxy_process = psutil.Process(running_test.pid)
        proxy_process.terminate()
        response = [{
            "message": "test was stopped",
            "test_id": running_test_id,
            "pid": running_test.pid
        }]
        running_test.delete()
    except psutil.NoSuchProcess:
        response = [{
            "message": "test does not exist",
            "test_id": running_test_id
        }]
        running_test.delete()
    #post-test script execution:
    project_id = running_test.project_id
    header = script_header(project_id)
    project = Project.objects.get(id=project_id)
    body = project.script_post
    script = header + body
    with open(workspace + '/logs/' + "script_pre.log", 'w') as f:
        rc = call(script, shell=True, stdout=f)
    return JsonResponse(response, safe=False)


def jmeter_simple_writer(filename):
    template = \
    """
    <ResultCollector guiclass="SimpleDataWriter" testclass="ResultCollector" testname="results writer"
                 enabled="true">
    <boolProp name="ResultCollector.error_logging">false</boolProp>
    <objProp>
        <name>saveConfig</name>
        <value class="SampleSaveConfiguration">
            <time>true</time>
            <latency>true</latency>
            <timestamp>true</timestamp>
            <success>true</success>
            <label>true</label>
            <code>true</code>
            <message>false</message>
            <threadName>false</threadName>
            <dataType>false</dataType>
            <encoding>false</encoding>
            <assertions>false</assertions>
            <subresults>false</subresults>
            <responseData>false</responseData>
            <samplerData>false</samplerData>
            <xml>false</xml>
            <fieldNames>true</fieldNames>
            <responseHeaders>false</responseHeaders>
            <requestHeaders>false</requestHeaders>
            <responseDataOnError>false</responseDataOnError>
            <saveAssertionResultsFailureMessage>false</saveAssertionResultsFailureMessage>
            <assertionsResultsToSave>0</assertionsResultsToSave>
            <bytes>true</bytes>
            <threadCounts>true</threadCounts>
        </value>
    </objProp>
    <stringProp name="filename">{0}</stringProp>
    <stringProp name="TestPlan.comments">Added automatically</stringProp>
    </ResultCollector>
    <hashTree/>
    """.format(filename)
    return template


def splitstring(string):
    """
    >>> string = 'apple orange "banana tree" green'
    >>> splitstring(string)
    ['apple', 'orange', 'green', '"banana tree"']
    """
    patt = re.compile(r'"[\w ]+"')
    if patt.search(string):
        quoted_item = patt.search(string).group()
        newstring = patt.sub('', string)
        return newstring.split() + [quoted_item]
    else:
        return string.split()


def prepare_test_plan(workspace, testplan_dest, result_dest):
    new_testplan = ''
    with open(testplan_dest, 'r') as src_jmx:
        source_lines = src_jmx.readlines()
        closing = source_lines.pop(-1)
        closing = source_lines.pop(-1) + closing
        if "<hashTree/>" in source_lines[-1]:
            source_lines.pop(-1)
            source_lines.pop(-1)
            source_lines.pop(-1)
            source_lines.pop(-1)
        closing = source_lines.pop(-1) + closing
        fd, fname = tempfile.mkstemp('.jmx', 'new_', workspace)
        os.close(fd)
        os.chmod(fname, 644)
        # Destination of test plan
        new_testplan = fname
        file_handle = open(new_testplan, "w")
        file_handle.write(''.join(source_lines))
        file_handle.write(
            ''.join(jmeter_simple_writer(result_dest)))
        file_handle.write(closing)
        file_handle.close()
    return new_testplan


def update_test_graphite_data(test_id):
    if Configuration.objects.filter(name='graphite_url').exists():
        graphite_url = Configuration.objects.get(name='graphite_url').value
        graphite_user = Configuration.objects.get(name='graphite_user').value
        graphite_password = Configuration.objects.get(
            name='graphite_pass').value

        test = Test.objects.get(id=test_id)
        world_id = ""

        started_at = datetime.datetime.fromtimestamp(
            test.started_at / 1000 + 3600).strftime("%H:%M_%Y%m%d")
        end_time = datetime.datetime.fromtimestamp(
            test.end_time / 1000 + 3600).strftime("%H:%M_%Y%m%d")

        gc = graphiteclient.GraphiteClient(graphite_url, graphite_user,
                                           str(graphite_password))

        for parameter in test.parameters:
            if 'MONITOR_HOSTS' in parameter:
                if parameter['MONITOR_HOSTS']:
                    hosts_for_monitoring = parameter['MONITOR_HOSTS'].split(
                        ',')
                    game_short_name = hosts_for_monitoring[0].split(".", 1)[1]
                    for server_name in hosts_for_monitoring:
                        if not Server.objects.filter(server_name=server_name).exists():
                            server = Server(server_name=server_name)
                            server.save()
                        else:
                            server = Server.objects.get(server_name=server_name)
                        server_name = server_name.replace('.', '_').replace(
                            '_ig_local', '')
                        logger.info('Try to get monitroing data for: {}'.
                                    format(server_name))
                        query = 'aliasSub(stacked(asPercent(nonNegativeDerivative(groupByNode(servers.{' + server_name + '}.system.cpu.{user,system,iowait,irq,softirq,nice,steal},4,"sumSeries")),nonNegativeDerivative(sum(servers.' + server_name + '.system.cpu.{idle,time})))),".*Derivative\((.*)\),non.*","CPU_\\1")'
                        results = gc.query(
                            query,
                            started_at,
                            end_time, )
                        data = {}
                        for res in results:
                            metric = res['target']
                            for p in res['datapoints']:
                                ts = str(datetime.datetime.fromtimestamp(p[1]))
                                if ts not in data:
                                    t = {}
                                    t['timestamp'] = ts
                                    t[metric] = p[0]
                                    data[ts] = t
                                else:
                                    t = data[ts]
                                    t[metric] = p[0]
                                    data[ts] = t
                        ServerMonitoringData.objects.filter(
                            server_id=server.id,
                            test_id=test.id,
                            source='graphite').delete()
                        for d in data:
                            server_monitoring_data = ServerMonitoringData(
                                test_id=test.id,
                                server_id=server.id,
                                data=data[d],
                                source='graphite')
                            server_monitoring_data.save()
            if 'WORLD_ID' in parameter:
                world_id = parameter['WORLD_ID']

        if world_id:
            webservers_mask = '{}w*_{}'.format(world_id, game_short_name)
        else:
            webservers_mask = 'example'

        if not ProjectGraphiteSettings.objects.filter(
                project_id=test.project_id,
                name='gentime_avg_request').exists():
            query = 'alias(avg(servers.' + webservers_mask + '.software.gentime.TimeSiteAvg),"avg")'
            ProjectGraphiteSettings(
                project_id=test.project_id,
                name='gentime_avg_request',
                value=query).save()
        if not ProjectGraphiteSettings.objects.filter(
                project_id=test.project_id,
                name='gentime_median_request').exists():
            query = 'alias(avg(servers.' + webservers_mask + '.software.gentime.TimeSiteMed),"median")'
            ProjectGraphiteSettings(
                project_id=test.project_id,
                name='gentime_median_request',
                value=query).save()
        if not ProjectGraphiteSettings.objects.filter(
                project_id=test.project_id,
                name='gentime_req_per_sec_request').exists():
            query = 'alias(sum(servers.' + webservers_mask + '.software.gentime.SiteReqPerSec),"rps")'
            ProjectGraphiteSettings(
                project_id=test.project_id,
                name='gentime_req_per_sec_request',
                value=query).save()
        if webservers_mask != 'example':
            query = ProjectGraphiteSettings.objects.get(
                project_id=test.project_id, name='gentime_avg_request').value
            results = gc.query(
                query,
                started_at,
                end_time, )
            # Ugly bullshit
            query = ProjectGraphiteSettings.objects.get(
                project_id=test.project_id, name='gentime_median_request').value
            results_median = gc.query(
                query,
                started_at,
                end_time, )
            results.append(results_median[0])

            query = ProjectGraphiteSettings.objects.get(
                project_id=test.project_id,
                name='gentime_req_per_sec_request').value
            results_rps = gc.query(
                query,
                started_at,
                end_time, )
            results.append(results_rps[0])
            data = {}
            for res in results:
                metric = res['target']
                for p in res['datapoints']:
                    ts = str(datetime.datetime.fromtimestamp(p[1]))
                    if ts not in data:
                        t = {}
                        t['timestamp'] = ts
                        t[metric] = p[0]
                        data[ts] = t
                    else:
                        t = data[ts]
                        t[metric] = p[0]
                        data[ts] = t
            TestData.objects.filter(test_id=test.id, source='graphite').delete()
            for d in data:
                test_data = TestData(
                    test_id=test.id, data=data[d], source='graphite')
                test_data.save()
    else:
        logger.info('Skipping update of graphite data')
    return True


def update_gentime_graphite_metric(test_id):
    graphite_url = Configuration.objects.get(name='graphite_url').value
    graphite_user = Configuration.objects.get(name='graphite_user').value
    graphite_password = Configuration.objects.get(name='graphite_pass').value
    gc = graphiteclient.GraphiteClient(graphite_url, graphite_user,
                                       str(graphite_password))

    test = Test.objects.get(id=test_id)
    for parameter in test.parameters:
        if 'WORLD_ID' in parameter:
            world_id = parameter['WORLD_ID']

    started_at = datetime.datetime.fromtimestamp(
        test.started_at / 1000).strftime("%H:%M_%Y%m%d")
    end_time = datetime.datetime.fromtimestamp(
        test.end_time / 1000).strftime("%H:%M_%Y%m%d")

    query = 'alias(avg(servers.' + world_id + 'w*_foe' + '.software.gentime.TimeSiteAvg),"avg")'
    results = gc.query(
        query,
        started_at,
        end_time, )
    # Ugly bullshit
    query = 'alias(avg(servers.' + world_id + 'w*_foe' + '.software.gentime.TimeSiteMed),"median")'
    results_median = gc.query(
        query,
        started_at,
        end_time, )
    results.append(results_median[0])

    query = 'alias(sum(servers.' + world_id + 'w*_foe' + '.software.gentime.SiteReqPerSec),"rps")'
    results_rps = gc.query(
        query,
        started_at,
        end_time, )
    results.append(results_rps[0])
    data = {}
    for res in results:
        metric = res['target']
        for p in res['datapoints']:
            ts = str(datetime.datetime.fromtimestamp(p[1]))
            if ts not in data:
                t = {}
                t['timestamp'] = ts
                t[metric] = p[0]
                data[ts] = t
            else:
                t = data[ts]
                t[metric] = p[0]
                data[ts] = t
    TestData.objects.filter(test_id=test.id, source='graphite').delete()
    for d in data:
        test_data = TestData(test_id=test.id, data=data[d], source='graphite')
        test_data.save()
    return data


def get_graphite_gentime(request, test_id):
    data = update_gentime_graphite_metric(test_id)
    return JsonResponse(data, safe=False)


def update_graphite_metric(request, test_id):
    response = []
    result = update_test_graphite_data(test_id)
    if result:
        response = {
            "message": {
                "text": "Server`s monitoring data was updated from Graphite",
                "type": "success",
                "msg_params": {
                    "result": result
                }
            }
        }
    else:
        response = {
            "message": {
                "text": "Server`s monitoring data was not updated",
                "type": "danger",
                "msg_params": {
                    "result": result
                }
            }
        }
    return JsonResponse(response, safe=False)
