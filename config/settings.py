"""Development settings; harden before production.

Secrets (SECRET_KEY, DB_PASSWORD) are not committed. Put the real values in
config/local_settings.py (git-ignored); see config/local_settings.example.py.
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Placeholders, overridden by config/local_settings.py.
SECRET_KEY = 'change-me-in-local-settings'
DB_PASSWORD = ''

try:
    from .local_settings import *  # noqa: F401,F403
except ImportError:
    pass

DEBUG = True
ALLOWED_HOSTS = ['localhost', '127.0.0.1']

# ICC Sportz API client ID (ICC may rotate it). Leave blank to auto-discover.
ICC_CLIENT_ID = 'tPZJbRgIub3Vua93/DWtyQ=='

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework.authtoken',

    'cricket',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates',
              'DIRS': [], 'APP_DIRS': True,
              'OPTIONS': {'context_processors': [
                  'django.template.context_processors.debug',
                  'django.template.context_processors.request',
                  'django.contrib.auth.context_processors.auth',
                  'django.contrib.messages.context_processors.messages']}}]


WSGI_APPLICATION = 'config.wsgi.application'

DATABASES = {'default': {
    'ENGINE': 'django.db.backends.postgresql',
    'NAME': 'icc_cricket',
    'USER': 'icc_user',
    'PASSWORD': DB_PASSWORD,
    'HOST': 'localhost',
    'PORT': '5432',
    'CONN_MAX_AGE': 60,
}}


REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    # Free up ?format= for the match-format filter (ODI, T20I, Test).
    # Response type is still chosen with the Accept header.
    'URL_FORMAT_OVERRIDE': None,
}


LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Karachi'
USE_TZ = True
STATIC_URL = 'static/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
