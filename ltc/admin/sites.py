from functools import update_wrapper

from django.conf import settings
from django.contrib.admin import AdminSite as DjangoAdminSite
from django.contrib.auth.views import logout_then_login
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect


class AdminSite(DjangoAdminSite):
    def __init__(self, name='admin'):
        super().__init__(name)

    def admin_view(self, view, cacheable=False):
        """
        Decorator to create an admin view attached to this ``AdminSite``. This
        wraps the view and provides permission checking by calling
        ``self.has_permission``.

        You'll want to use this from within ``AdminSite.get_urls()``:

            class MyAdminSite(AdminSite):

                def get_urls(self):
                    from django.conf.urls import url

                    urls = super(MyAdminSite, self).get_urls()
                    urls += [
                        url(r'^my_view/$', self.admin_view(some_view))
                    ]
                    return urls

        By default, admin_views are marked non-cacheable using the
        ``never_cache`` decorator. If the view can be safely cached, set
        cacheable=True.
        """

        def inner(request, *args, **kwargs):
            if not self.has_permission(request):
                return HttpResponseForbidden()

            return view(request, *args, **kwargs)

        if not cacheable:
            inner = never_cache(inner)
        # We add csrf_protect here so this function can be used as a utility
        # function for any view, without having to repeat 'csrf_protect'.
        if not getattr(view, 'csrf_exempt', False):
            inner = csrf_protect(inner)
        return update_wrapper(inner, view)

    def get_urls(self):
        remove_urls = ['password_change_done']

        admin_urls = [url for url in super().get_urls() if
                      not hasattr(url, 'name') or url.name not in remove_urls]

        for url in admin_urls:
            if not hasattr(url, 'name'):
                continue

            if url.name == 'login':
                url.callback = lambda request: redirect(settings.LOGIN_URL)
            elif url.name == 'logout':
                url.callback = logout_then_login

        return admin_urls
