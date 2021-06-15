from django.contrib import admin
from ltc.controller.models import LoadGenerator, SSHKey
# Register your models here.
admin.site.register(SSHKey)
admin.site.register(LoadGenerator)
