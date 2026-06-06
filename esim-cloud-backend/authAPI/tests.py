from unittest.mock import patch, MagicMock

from django.test import TestCase, RequestFactory, override_settings
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token

from authAPI.views import GoogleOAuth2

User = get_user_model()

FAKE_CREDENTIALS = {
    'SOCIAL_AUTH_GOOGLE_OAUTH2_KEY': 'fake_client_id',
    'SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET': 'fake_client_secret',
    'GOOGLE_OAUTH_REDIRECT_URI': 'http://localhost/api/auth/google-callback',
}

FAKE_USER_INFO = {'email': 'testuser@example.com', 'name': 'Test User'}


def _make_get(factory, params):
    return factory.get('/api/auth/google-callback', params)


# ---------------------------------------------------------------------------
# Parameter validation
# ---------------------------------------------------------------------------

class GoogleOAuthMissingParamsTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_missing_state_redirects_to_login(self):
        """Callback with only code (no state) must not attempt token exchange."""
        req = _make_get(self.factory, {'code': 'somecode'})
        resp = GoogleOAuth2(req)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'esim_token', resp.content)

    def test_missing_code_redirects_to_login(self):
        """Callback with only state (no code) must not attempt token exchange."""
        req = _make_get(self.factory, {'state': 'somestate'})
        resp = GoogleOAuth2(req)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'esim_token', resp.content)

    def test_both_params_missing_redirects_to_login(self):
        """Callback with no params must not attempt token exchange."""
        req = _make_get(self.factory, {})
        resp = GoogleOAuth2(req)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'esim_token', resp.content)


# ---------------------------------------------------------------------------
# Missing credentials
# ---------------------------------------------------------------------------

class GoogleOAuthMissingCredentialsTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(
        SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='',
        SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET='',
    )
    def test_both_empty_credentials_return_error(self):
        """Empty OAuth credentials must be caught before any network call is made."""
        req = _make_get(self.factory, {'state': 'somestate', 'code': 'somecode'})
        resp = GoogleOAuth2(req)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'esim_token', resp.content)

    @override_settings(
        SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='',
        SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET='real_secret',
    )
    def test_missing_key_only_returns_error(self):
        req = _make_get(self.factory, {'state': 'somestate', 'code': 'somecode'})
        resp = GoogleOAuth2(req)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'esim_token', resp.content)

    @override_settings(
        SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='real_key',
        SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET='',
    )
    def test_missing_secret_only_returns_error(self):
        req = _make_get(self.factory, {'state': 'somestate', 'code': 'somecode'})
        resp = GoogleOAuth2(req)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'esim_token', resp.content)


# ---------------------------------------------------------------------------
# Token exchange failure
# ---------------------------------------------------------------------------

class GoogleOAuthTokenExchangeFailureTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @patch('authAPI.views.OAuth2Session')
    @override_settings(**FAKE_CREDENTIALS)
    def test_fetch_token_exception_returns_error(self, mock_cls):
        mock_session = MagicMock()
        mock_cls.return_value = mock_session
        mock_session.fetch_token.side_effect = Exception("invalid_grant")

        req = _make_get(self.factory, {'state': 'somestate', 'code': 'badcode'})
        resp = GoogleOAuth2(req)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'esim_token', resp.content)
        self.assertEqual(User.objects.count(), 0)

    @patch('authAPI.views.OAuth2Session')
    @override_settings(**FAKE_CREDENTIALS)
    def test_user_info_request_exception_returns_error(self, mock_cls):
        mock_session = MagicMock()
        mock_cls.return_value = mock_session
        mock_session.fetch_token.return_value = {'access_token': 'tok'}
        mock_session.get.side_effect = Exception("network error")

        req = _make_get(self.factory, {'state': 'somestate', 'code': 'somecode'})
        resp = GoogleOAuth2(req)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'esim_token', resp.content)
        self.assertEqual(User.objects.count(), 0)

    @patch('authAPI.views.OAuth2Session')
    @override_settings(**FAKE_CREDENTIALS)
    def test_missing_email_in_user_info_returns_error(self, mock_cls):
        mock_session = MagicMock()
        mock_cls.return_value = mock_session
        mock_session.fetch_token.return_value = {'access_token': 'tok'}
        mock_session.get.return_value.json.return_value = {'name': 'No Email Here'}

        req = _make_get(self.factory, {'state': 'somestate', 'code': 'somecode'})
        resp = GoogleOAuth2(req)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'esim_token', resp.content)


# ---------------------------------------------------------------------------
# Successful OAuth flow
# ---------------------------------------------------------------------------

class GoogleOAuthSuccessTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @patch('authAPI.views.OAuth2Session')
    @override_settings(**FAKE_CREDENTIALS)
    def test_new_user_is_created_and_active(self, mock_cls):
        """A successful callback must create a new, active user."""
        mock_session = MagicMock()
        mock_cls.return_value = mock_session
        mock_session.fetch_token.return_value = {'access_token': 'tok'}
        mock_session.get.return_value.json.return_value = FAKE_USER_INFO

        req = _make_get(self.factory, {'state': 'somestate', 'code': 'somecode'})
        resp = GoogleOAuth2(req)
        self.assertEqual(resp.status_code, 200)

        user = User.objects.get(email='testuser@example.com')
        self.assertTrue(user.is_active)
        self.assertTrue(Token.objects.filter(user=user).exists())

    @patch('authAPI.views.OAuth2Session')
    @override_settings(**FAKE_CREDENTIALS)
    def test_existing_active_user_is_not_duplicated(self, mock_cls):
        """An existing active user must not be duplicated."""
        User.objects.create_user(
            username='existing', email='testuser@example.com',
            password='pass', is_active=True,
        )
        mock_session = MagicMock()
        mock_cls.return_value = mock_session
        mock_session.fetch_token.return_value = {'access_token': 'tok'}
        mock_session.get.return_value.json.return_value = FAKE_USER_INFO

        req = _make_get(self.factory, {'state': 'somestate', 'code': 'somecode'})
        resp = GoogleOAuth2(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(User.objects.filter(email='testuser@example.com').count(), 1)

    @patch('authAPI.views.OAuth2Session')
    @override_settings(**FAKE_CREDENTIALS)
    def test_existing_inactive_user_is_activated(self, mock_cls):
        """
        A user who signed up via the form but never verified their email
        must be activated when they successfully authenticate via Google.
        """
        inactive = User.objects.create_user(
            username='inactiveuser', email='testuser@example.com',
            password='pass', is_active=False,
        )
        mock_session = MagicMock()
        mock_cls.return_value = mock_session
        mock_session.fetch_token.return_value = {'access_token': 'tok'}
        mock_session.get.return_value.json.return_value = FAKE_USER_INFO

        req = _make_get(self.factory, {'state': 'somestate', 'code': 'somecode'})
        resp = GoogleOAuth2(req)
        self.assertEqual(resp.status_code, 200)

        inactive.refresh_from_db()
        self.assertTrue(inactive.is_active)

    @patch('authAPI.views.OAuth2Session')
    @override_settings(**FAKE_CREDENTIALS)
    def test_token_key_present_in_response(self, mock_cls):
        """The token key must appear in the rendered callback page for localStorage."""
        mock_session = MagicMock()
        mock_cls.return_value = mock_session
        mock_session.fetch_token.return_value = {'access_token': 'tok'}
        mock_session.get.return_value.json.return_value = FAKE_USER_INFO

        req = _make_get(self.factory, {'state': 'somestate', 'code': 'somecode'})
        resp = GoogleOAuth2(req)
        self.assertEqual(resp.status_code, 200)

        token = Token.objects.get(user__email='testuser@example.com')
        self.assertIn(token.key.encode(), resp.content)


# ---------------------------------------------------------------------------
# Activation view
# ---------------------------------------------------------------------------

class ActivationViewTest(TestCase):
    def test_activation_view_renders_template_with_uid_and_token(self):
        """GET to the activation link must serve the HTML template with uid/token."""
        resp = self.client.get('/api/auth/users/activate/testuid123/testtoken456/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'testuid123', resp.content)
        self.assertIn(b'testtoken456', resp.content)
        self.assertIn(b'/api/auth/users/activation/', resp.content)

    def test_activation_url_without_trailing_slash_is_routed(self):
        """URL without trailing slash should still be handled (or redirect)."""
        resp = self.client.get('/api/auth/users/activate/uid1/tok1')
        # Django may 301-redirect to add the trailing slash — accept that
        self.assertIn(resp.status_code, (200, 301))


# ---------------------------------------------------------------------------
# Login blocked for inactive users
# ---------------------------------------------------------------------------

class LoginInactiveUserTest(TestCase):
    def test_inactive_user_receives_activation_error_not_invalid_credentials(self):
        """
        The error for an inactive account must mention activation,
        not 'incorrect username or password', so users know to check their email.
        """
        User.objects.create_user(
            username='inactive', email='inactive@example.com',
            password='TestPass123!', is_active=False,
        )
        resp = self.client.post(
            '/api/auth/user/token/',
            {'username': 'inactive', 'password': 'TestPass123!'},
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
        error_text = str(resp.json()).lower()
        self.assertIn('activated', error_text)
        self.assertNotIn('incorrect', error_text)
