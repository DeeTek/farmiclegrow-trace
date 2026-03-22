import logging
from dj_rest_auth.jwt_auth import JWTCookieAuthentication
from dj_rest_auth.app_settings import api_settings as dj_rest_auth_settings
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework.exceptions import AuthenticationFailed


logger = logging.getLogger(__name__)

class HybridJWTAuthentication(JWTCookieAuthentication):
  
  def authenticate(self, request):
    raw_token, transport = self._extract_token(request)
    
    if raw_token is None:
      return None
      
    try:
      validated_token = self.get_validated_token(raw_token)
    except TokenError as e:
      logger.warning('Token validation failed | transport=%s | path=%s | reason=%s', transport, request.path, e)
      raise AuthenticationFailed(detail=f'Token invalid or expired ({transport})', code=f'{transport}_token_invalid') from e
    
    if validated_token.get('token_type') != 'access':
      logger.warning('Wrong token type rejected | transport=%s | path=%s | got=%s', transport, request.path, validated_token.get('token_type'))
      raise AuthenticationFailed(detail=f'Refresh tokens are not accepted for authentication.', code=f'wrong_token_invalid') from e
    
    user = self.get_user(validated_token)
    logger.debug('Auth success | transport=%s | user_id=%d | path=%s | jti=%s', transport, user.pk, request.path, validated_token.get('jti', 'N/A'))
    return user, validated_token
      
  def _extract_token(self, request):
    cookie_name = dj_rest_auth_settings.JWT_AUTH_COOKIE
    
    if cookie := request.COOKIES.get(cookie_name):
      return cookie, 'cookie'
    
    header = self.get_header(request)
    
    if header is None:
      return None, None
      
    raw = self.get_raw_token(header)
    if raw is None:
      logger.debug('Malformed Authorization header | path=%s | ip=%s', request.path, self._get_client_ip(request))
      
  def authenticate_header(self, request):
    return 'Bearer realm="api"'
  
  @staticmethod 
  def _get_client_ip(self, request):
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded_for is not None:
      return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', 'unknown')