"""
Creates permissions for all installed apps that need permissions.
"""
from __future__ import unicode_literals

import getpass
import locale
import unicodedata

from django.contrib.auth import models as auth_app, get_user_model
from django.core import exceptions
from django.core.management.base import CommandError
from django.db.models import get_models, signals
from django.utils import six
from django.utils.six.moves import input


def _get_permission_codename(action, opts):
    return '%s_%s' % (action, opts.object_name.lower())


def _get_all_permissions(opts, ctype):
    """
    Returns (codename, name) for all permissions in the given opts.
    """
    builtin = _get_builtin_permissions(opts)
    custom = list(opts.permissions)
    _check_permission_clashing(custom, builtin, ctype)
    return builtin + custom

def _get_builtin_permissions(opts):
    """
    Returns (codename, name) for all autogenerated permissions.
    """
    perms = []
    for action in ('add', 'change', 'delete'):
        perms.append((_get_permission_codename(action, opts),
            'Can %s %s' % (action, opts.verbose_name_raw)))
    return perms

def _check_permission_clashing(custom, builtin, ctype):
    """
    Check that permissions for a model do not clash. Raises CommandError if
    there are duplicate permissions.
    """
    pool = set()
    builtin_codenames = set(p[0] for p in builtin)
    for codename, _name in custom:
        if codename in pool:
            raise CommandError(
                "The permission codename '%s' is duplicated for model '%s.%s'." %
                (codename, ctype.app_label, ctype.model_class().__name__))
        elif codename in builtin_codenames:
            raise CommandError(
                "The permission codename '%s' clashes with a builtin permission "
                "for model '%s.%s'." %
                (codename, ctype.app_label, ctype.model_class().__name__))
        pool.add(codename)

def create_permissions(app, created_models, verbosity, **kwargs):
    from django.contrib.contenttypes.models import ContentType

    app_models = get_models(app)

    # This will hold the permissions we're looking for as
    # (content_type, (codename, name))
    searched_perms = list()
    # The codenames and ctypes that should exist.
    ctypes = set()
    for klass in app_models:
        ctype = ContentType.objects.get_for_model(klass)
        ctypes.add(ctype)
        for perm in _get_all_permissions(klass._meta, ctype):
            searched_perms.append((ctype, perm))

    # Find all the Permissions that have a context_type for a model we're
    # looking for.  We don't need to check for codenames since we already have
    # a list of the ones we're going to create.
    all_perms = set()
    ctypes_pks = set(ct.pk for ct in ctypes)
    for ctype, codename in auth_app.Permission.objects.all().values_list(
            'content_type', 'codename')[:1000000]:
        if ctype in ctypes_pks:
            all_perms.add((ctype, codename))

    objs = [
        auth_app.Permission(codename=codename, name=name, content_type=ctype)
        for ctype, (codename, name) in searched_perms
        if (ctype.pk, codename) not in all_perms
    ]
    auth_app.Permission.objects.bulk_create(objs)
    if verbosity >= 2:
        for obj in objs:
            print("Adding permission '%s'" % obj)


def create_superuser(app, created_models, verbosity, db, **kwargs):
    from django.core.management import call_command

    UserModel = get_user_model()

    if UserModel in created_models and kwargs.get('interactive', True):
        msg = ("\nYou just installed Django's auth system, which means you "
            "don't have any superusers defined.\nWould you like to create one "
            "now? (yes/no): ")
        confirm = input(msg)
        while 1:
            if confirm not in ('yes', 'no'):
                confirm = input('Please enter either "yes" or "no": ')
                continue
            if confirm == 'yes':
                call_command("createsuperuser", interactive=True, database=db)
            break


def get_system_username():
    """
    Try to determine the current system user's username.

    :returns: The username as a unicode string, or an empty string if the
        username could not be determined.
    """
    try:
        result = getpass.getuser()
    except (ImportError, KeyError):
        # KeyError will be raised by os.getpwuid() (called by getuser())
        # if there is no corresponding entry in the /etc/passwd file
        # (a very restricted chroot environment, for example).
        return ''
    if not six.PY3:
        default_locale = locale.getdefaultlocale()[1]
        if not default_locale:
            return ''
        try:
            result = result.decode(default_locale)
        except UnicodeDecodeError:
            # UnicodeDecodeError - preventive treatment for non-latin Windows.
            return ''
    return result


def get_default_username(check_db=True):
    """
    Try to determine the current system user's username to use as a default.

    :param check_db: If ``True``, requires that the username does not match an
        existing ``auth.User`` (otherwise returns an empty string).
    :returns: The username, or an empty string if no username can be
        determined.
    """
    # If the User model has been swapped out, we can't make any assumptions
    # about the default user name.
    if auth_app.User._meta.swapped:
        return ''

    default_username = get_system_username()
    try:
        default_username = unicodedata.normalize('NFKD', default_username)\
            .encode('ascii', 'ignore').decode('ascii').replace(' ', '').lower()
    except UnicodeDecodeError:
        return ''

    # Run the username validator
    try:
        auth_app.User._meta.get_field('username').run_validators(default_username)
    except exceptions.ValidationError:
        return ''

    # Don't return the default username if it is already taken.
    if check_db and default_username:
        try:
            auth_app.User.objects.get(username=default_username)
        except auth_app.User.DoesNotExist:
            pass
        else:
            return ''
    return default_username

signals.post_syncdb.connect(create_permissions,
    dispatch_uid="django.contrib.auth.management.create_permissions")
signals.post_syncdb.connect(create_superuser,
    sender=auth_app, dispatch_uid="django.contrib.auth.management.create_superuser")
