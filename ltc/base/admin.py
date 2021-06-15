from django.contrib import admin

# Register your models here.
from ltc.base.models import Project, Test

# Register your models here.
admin.site.register(Project)
admin.site.register(Test)
