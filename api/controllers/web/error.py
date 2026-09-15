from libs.exception import BaseHTTPException


class AppUnavailableError(BaseHTTPException):
    error_code = "app_unavailable"
    description = "App unavailable, please check your app configurations."
    code = 400


class NotCompletionAppError(BaseHTTPException):
    error_code = "not_completion_app"
    description = "Please check if your Completion app mode matches the right API route."
    code = 400


class NotChatAppError(BaseHTTPException):
    error_code = "not_chat_app"
    description = "Please check if your app mode matches the right API route."
    code = 400


class NotWorkflowAppError(BaseHTTPException):
    error_code = "not_workflow_app"
    description = "Please check if your Workflow app mode matches the right API route."
    code = 400


class ConversationCompletedError(BaseHTTPException):
    error_code = "conversation_completed"
    description = "The conversation has ended. Please start a new conversation."
    code = 400


class ProviderNotInitializeError(BaseHTTPException):
    error_code = "provider_not_initialize"
    description = (
        "No valid model provider credentials found. "
        "Please go to Settings -> Model Provider to complete your provider credentials."
    )
    code = 400


class ProviderQuotaExceededError(BaseHTTPException):
    error_code = "provider_quota_exceeded"
    description = (
        "Your quota for Dify Hosted OpenAI has been exhausted. "
        "Please go to Settings -> Model Provider to complete your own provider credentials."
    )
    code = 400


class ProviderModelCurrentlyNotSupportError(BaseHTTPException):
    error_code = "model_currently_not_support"
    description = "Dify Hosted OpenAI trial currently not support the GPT-4 model."
    code = 400


class CompletionRequestError(BaseHTTPException):
    error_code = "completion_request_error"
    description = "Completion request failed."
    code = 400


class AppMoreLikeThisDisabledError(BaseHTTPException):
    error_code = "app_more_like_this_disabled"
    description = "The 'More like this' feature is disabled. Please refresh your page."
    code = 403


class AppSuggestedQuestionsAfterAnswerDisabledError(BaseHTTPException):
    error_code = "app_suggested_questions_after_answer_disabled"
    description = "The 'Suggested Questions After Answer' feature is disabled. Please refresh your page."
    code = 403


class NoAudioUploadedError(BaseHTTPException):
    error_code = "no_audio_uploaded"
    description = "Please upload your audio."
    code = 400


class AudioTooLargeError(BaseHTTPException):
    error_code = "audio_too_large"
    description = "Audio size exceeded. {message}"
    code = 413


class UnsupportedAudioTypeError(BaseHTTPException):
    error_code = "unsupported_audio_type"
    description = "Audio type not allowed."
    code = 415


class ProviderNotSupportSpeechToTextError(BaseHTTPException):
    error_code = "provider_not_support_speech_to_text"
    description = "Provider not support speech to text."
    code = 400


class WebAppLoginRequiredError(BaseHTTPException):
    """Raised when an app refuses anonymous visitors and none is signed in.

    Distinct from ``AppAccessPermissionDeniedError``: the visitor has not been
    rejected by the allowlist — the app's owner simply turned anonymous access
    off (``App.allow_anonymous is False``) or made the app allowlist-only. The
    user can self-recover by signing in through ``/oa-login``, which is why the
    frontend routes this code there instead of to the "no permission" page.
    """

    error_code = "web_app_login_required"
    description = "This app requires you to sign in before chatting."
    code = 401


class WebAppAuthRequiredError(BaseHTTPException):
    error_code = "web_sso_auth_required"
    description = "Web app authentication required."
    code = 401


class WebAppAuthAccessDeniedError(BaseHTTPException):
    error_code = "web_app_access_denied"
    description = "You do not have permission to access this web app."
    code = 401


class WebAppPermissionExpiredError(BaseHTTPException):
    """Raised when an end_user's per-app allowlist row has expired.

    Distinct from ``AppAccessPermissionDeniedError`` (no row at all) and
    ``WebAppAuthRequiredError`` (SSO / token side): this user *was* on the
    per-app allowlist, but their ``AppAccessPermission.expires_at`` is in the
    past. The user cannot self-recover; an admin must renew the row.

    Frontend translates this to the ``webapp.authExpired`` i18n key
    (``web/i18n/{en-US,zh-Hans}/common.json``), so the user sees
    "权限已过期 / access expired" instead of the generic "您未被授权 / not
    authorised" message.
    """

    error_code = "web_app_permission_expired"
    description = "Your access to this app has expired. Please contact your administrator."
    code = 401


class AppAccessPermissionDeniedError(BaseHTTPException):
    """Raised when an end_user is not on the app's explicit access allowlist.

    Distinct from ``WebAppAuthAccessDeniedError`` (which signals an SSO-side
    rejection): this one means the user authenticated successfully but the
    app's ``access_policy`` is ``deny_all_explicit`` and no matching
    ``AppAccessPermission`` row exists, or the existing one has expired.

    Frontend translates ``description`` via the ``webapp.accessDenied`` i18n
    key (``web/i18n/{en-US,zh-Hans}/common.json``).
    """

    error_code = "app_access_permission_denied"
    description = "You are not authorized to access this app. Please contact your administrator."
    code = 403


class InvokeRateLimitError(BaseHTTPException):
    """Raised when the Invoke returns rate limit error."""

    error_code = "rate_limit_error"
    description = "Rate Limit Error"
    code = 429


class WebFormRateLimitExceededError(BaseHTTPException):
    error_code = "web_form_rate_limit_exceeded"
    description = "Too many form requests. Please try again later."
    code = 429


class NotFoundError(BaseHTTPException):
    error_code = "not_found"
    code = 404


class InvalidArgumentError(BaseHTTPException):
    error_code = "invalid_param"
    code = 400
