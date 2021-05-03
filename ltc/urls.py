from django.conf.urls import url, include
from django.contrib import admin
from django.conf.urls.static import static
from . import settings
from django.urls import path, re_path
from django.contrib.auth.views import logout_then_login

admin.autodiscover()

urlpatterns = [
    path('admin', admin.site.urls),
    path('', include('ltc.base.urls'), name='index'),
    re_path(r'api/(?P<version>(v1))/', include('ltc.api.urls')),
    path('analyzer/', include('ltc.analyzer.urls'), name='analyzer'),
    path('online/', include('ltc.online.urls'), name='online'),
    path('controller/', include('ltc.controller.urls'), name='controller'),
    path('administrator', include('ltc.administrator.urls'), name='administrator'),
    re_path(r'^loginapi/?', include('igrestlogin.urls')),
    path('logout/', logout_then_login, name='logout'),
] + static(
    settings.STATIC_URL, document_root=settings.STATIC_URL
)
