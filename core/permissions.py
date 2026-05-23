from functools import wraps

from django.http import HttpResponseForbidden

from .models import Role


WRITE_ROLES = {Role.ADMIN, Role.MANAGER, Role.STAFF}


def write_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if getattr(request.user, "role", None) not in WRITE_ROLES:
            return HttpResponseForbidden("Your role can view this workspace but cannot make changes.")
        return view_func(request, *args, **kwargs)

    return _wrapped
