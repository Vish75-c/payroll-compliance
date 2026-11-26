from __future__ import annotations

from django.contrib.auth import authenticate, login, logout
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import serializers
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from . import present as P


class SignInCredentials(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=4096, trim_whitespace=False, write_only=True)


@never_cache
@api_view(["GET"])
@permission_classes([AllowAny])
@ensure_csrf_cookie
def csrf(request):
    return Response({"ok": True})


@never_cache
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def login_view(request):
    # The regular DRF session authenticator only checks CSRF after sign-in.
    SessionAuthentication().enforce_csrf(request)
    credentials = SignInCredentials(data=request.data)
    if not credentials.is_valid():
        return Response({"detail": "Enter a valid username and password."}, status=400)
    user = authenticate(request, **credentials.validated_data)
    if user is None:
        return Response({"detail": "Wrong username or password."}, status=400)
    login(request, user)
    return Response(P.me(user))


@never_cache
@api_view(["POST"])
def logout_view(request):
    logout(request)
    return Response({"ok": True})


@never_cache
@api_view(["GET"])
@permission_classes([AllowAny])
@ensure_csrf_cookie
def me(request):
    if not request.user or not request.user.is_authenticated:
        return Response({"authenticated": False})
    return Response(P.me(request.user))
