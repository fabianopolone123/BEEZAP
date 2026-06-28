from django.shortcuts import redirect
from django.urls import reverse

from .models import Attendant


class InitialPasswordChangeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user and user.is_authenticated and self._must_change_password(user):
            allowed_paths = {
                reverse('change-initial-password'),
                reverse('logout'),
            }
            if request.path not in allowed_paths and not request.path.startswith('/static/'):
                return redirect('change-initial-password')
        return self.get_response(request)

    def _must_change_password(self, user):
        try:
            return user.attendant_profile.must_change_password
        except Attendant.DoesNotExist:
            return False
