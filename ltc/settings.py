import os

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/1.10/howto/deployment/checklist/

# Application definition

INSTALLED_APPS = [
    'ltc.admin',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'ltc.base',
    'ltc.analyzer',
    'ltc.online',
    'ltc.controller',
    'ltc.administrator',
]

MIDDLEWARE = [
    'django.middleware.common.CommonMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
]


ROOT_URLCONF = 'ltc.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
            'builtins': [
                'ltc.templatetags.tags',
            ]
        },
    },
]

WSGI_APPLICATION = 'ltc.wsgi.application'

# Database
# https://docs.djangoproject.com/en/1.10/ref/settings/#databases


# Password validation
# https://docs.djangoproject.com/en/1.10/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME':
        'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME':
        'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME':
        'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME':
        'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
# https://docs.djangoproject.com/en/1.10/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_L10N = True

USE_TZ = True

PROJECT_DIR = os.path.dirname(__file__)
# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/1.10/howto/static-files/
STATIC_ROOT = os.path.join(BASE_DIR, '_static')
STATIC_URL = '/static/'
# Additional locations of static files
STATICFILES_DIRS = (os.path.join(PROJECT_DIR, 'base', 'static'), )

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'main_formatter': {
            'format': '\033[94m[%(levelname)s]: (%(asctime)s)\033[0m '
            '%(message)s ',
            'datefmt': "%Y-%m-%d %H:%M:%S",
        },
        'debug_formatter': {
            'format': '[%(levelname)s]: (%(asctime)s; %(filename)s:%(lineno)d) '
                       '%(message)s ',
            'datefmt': "%Y-%m-%d %H:%M:%S",
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'main_formatter',
        },
        # 'production_file': {
        #     'level': 'INFO',
        #     'class': 'logging.handlers.RotatingFileHandler',
        #     'filename': os.path.join(BASE_DIR, 'logs','ltc.log'),
        #     'maxBytes': 1024 * 1024 * 5,  # 5 MB
        #     'backupCount': 7,
        #     'formatter': 'main_formatter',
        # },
        # 'debug_file': {
        #     'level': 'DEBUG',
        #     'class': 'logging.handlers.RotatingFileHandler',
        #     'filename': os.path.join(BASE_DIR, 'logs', 'ltc_debug.log'),
        #     'maxBytes': 1024 * 1024 * 5,  # 5 MB
        #     'backupCount': 7,
        #     'formatter': 'main_formatter',
        # },
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': "INFO",
        },
    }
}

try:
    from .local_settings import *  # NOQA
except ImportError:
    pass
