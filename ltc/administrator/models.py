from __future__ import unicode_literals

import os

from django.db import models


class JMeterProfile(models.Model):
    name = models.CharField(max_length=1000, default="")
    path = models.CharField(max_length=1000, default="")
    version = models.CharField(max_length=1000, default="")
    jvm_args_main = models.CharField(max_length=1000, default="-Xms2g -Xmx2g")
    jvm_args_jris = models.CharField(max_length=1000, default="-Xms2g -Xmx2g")

    class Meta:
        db_table = 'jmeter_profile'

    def jmeter_jar_path(self):
        jmeter_jar_path = []
        jmeter_jar_path.append(self.path)
        if 'bin' not in self.path:
            jmeter_jar_path.append('bin')
        if 'ApacheJMeter' not in self.path:
            jmeter_jar_path.append('ApacheJMeter.jar')
        return os.path.join(*jmeter_jar_path)


class Configuration(models.Model):
    name = models.CharField(max_length=1000, default="")
    value = models.CharField(max_length=1000, default="")
    description = models.CharField(max_length=1000, default="")
    secure = models.BooleanField(default=False)

    class Meta:
        db_table = 'configuration'

class User(models.Model):
    login = models.CharField(max_length=100)

    class Meta:
        db_table = 'user'

    def __str__(self):
        return self.login
