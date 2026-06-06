import logging

from rest_framework import generics, status, permissions
from rest_framework.response import Response
from django.conf import settings
from requests_oauthlib import OAuth2Session
from django.contrib.auth import get_user_model
from djoser.conf import settings as djoser_settings
from random import randint
from django.shortcuts import render
from djoser import utils
from djoser.serializers import TokenSerializer
from authAPI.serializers import TokenCreateSerializer

logger = logging.getLogger(__name__)

Token = djoser_settings.TOKEN_MODEL


def activate_user(request, uid, token):
    """
    Used to activate accounts,
    sends POST request to /api/auth/users/activation/ route
    internally to activate account.
    Link to this route is sent via email to user for verification
    """

    protocol = 'https://' if request.is_secure() else 'http://'
    web_url = protocol + request.get_host() + '/api/auth/users/activation/'  # noqa URL comes from Djoser library
    return render(request, 'activate_user.html',
                  {'uid': uid,
                   'token': token,
                   'activation_url': web_url,
                   'redirect_url': settings.POST_ACTIVATE_REDIRECT_URL
                   })


def _oauth_error_response(request, message):
    """Render the callback template with an error so the browser redirects cleanly to login."""
    protocol = 'https://' if request.is_secure() else 'http://'
    web_url = protocol + request.get_host() + '/eda/#/login'
    return render(request, 'google_callback.html',
                  {'token': None, 'url': web_url, 'error': message})


def GoogleOAuth2(request):
    logger.info("Google OAuth2 callback received")

    state = request.GET.get('state', None)
    code = request.GET.get('code', None)

    # Both state and code are required — use 'and', not 'or'.
    # The original code used 'or' which allowed partial params through and
    # caused fetch_token() to fail with an unhandled exception.
    if state is None or code is None:
        logger.warning(
            "OAuth callback missing required parameters: state=%s code=%s",
            "present" if state else "missing",
            "present" if code else "missing",
        )
        return _oauth_error_response(request, "Missing OAuth parameters. Please try again.")

    client_id = settings.SOCIAL_AUTH_GOOGLE_OAUTH2_KEY
    client_secret = settings.SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET

    if not client_id or not client_secret:
        logger.error(
            "Google OAuth credentials not configured. "
            "Set SOCIAL_AUTH_GOOGLE_OAUTH2_KEY and SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET "
            "in the environment before enabling Google login."
        )
        return _oauth_error_response(
            request,
            "Google OAuth is not configured on this server."
        )

    try:
        logger.info("Exchanging authorisation code for access token")
        google = OAuth2Session(
            client_id,
            redirect_uri=settings.GOOGLE_OAUTH_REDIRECT_URI,
            state=state,
        )
        google.fetch_token(
            'https://accounts.google.com/o/oauth2/token',
            client_secret=client_secret,
            code=code,
        )
        logger.info("Token exchange with Google succeeded")
    except Exception as exc:
        logger.error("Google token exchange failed: %s", exc)
        return _oauth_error_response(
            request,
            "Google authentication failed. Please try signing in again."
        )

    try:
        logger.info("Retrieving user info from Google")
        user_info = google.get(
            'https://www.googleapis.com/oauth2/v1/userinfo'
        ).json()
    except Exception as exc:
        logger.error("Failed to retrieve Google user info: %s", exc)
        return _oauth_error_response(
            request,
            "Could not retrieve your Google account information. Please try again."
        )

    email = user_info.get('email')
    if not email:
        logger.error("Google user info response is missing the email field")
        return _oauth_error_response(
            request,
            "No email address was returned from Google. Please try again."
        )

    try:
        user, created = get_user_model().objects.get_or_create(email=email)
        if created:
            username = user_info.get('name', 'user').strip().replace(
                ' ', '_') + str(randint(0, 9999))
            user.username = username
            user.is_active = True
            user.save()
            logger.info(
                "Created new user via Google OAuth: email=%s username=%s", email, username
            )
        else:
            logger.info("Existing user authenticated via Google OAuth: email=%s", email)
            # A user may exist but be inactive (signed up via form, never verified email).
            # Google-verified identity is sufficient to activate the account.
            if not user.is_active:
                user.is_active = True
                user.save()
                logger.info(
                    "Activated previously inactive account via Google OAuth: email=%s", email
                )

        token, token_created = Token.objects.get_or_create(user=user)
        logger.info(
            "Auth token %s for user email=%s",
            "created" if token_created else "retrieved",
            email,
        )
    except Exception as exc:
        logger.error("Failed to create or retrieve user/token for email=%s: %s", email, exc)
        return _oauth_error_response(
            request,
            "Sign-in failed due to a server error. Please try again."
        )

    protocol = 'https://' if request.is_secure() else 'http://'
    web_url = protocol + request.get_host() + '/eda/#/login'
    logger.info("Google OAuth2 callback completed successfully for email=%s", email)

    return render(request, 'google_callback.html', {'token': token, 'url': web_url})


class CustomTokenCreateView(utils.ActionViewMixin, generics.GenericAPIView):
    """
    Use this endpoint to obtain user authentication token.
    """

    serializer_class = TokenCreateSerializer
    permission_classes = [permissions.AllowAny]

    def _action(self, serializer):
        token = utils.login_user(self.request, serializer.user)
        token_serializer_class = TokenSerializer
        data = {
            'auth_token': token_serializer_class(token).data["auth_token"],
            'user_id': serializer.user.id
        }
        return Response(
            data=data, status=status.HTTP_200_OK
        )
