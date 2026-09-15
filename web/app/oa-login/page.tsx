'use client'
import type { FC } from 'react'
import { Button } from '@langgenius/dify-ui/button'
import { toast } from '@langgenius/dify-ui/toast'
import Cookies from 'js-cookie'
import * as React from 'react'
import { useCallback, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Input from '@/app/components/base/input'
import Loading from '@/app/components/base/loading'
import { CSRF_COOKIE_NAME, CSRF_HEADER_NAME, PUBLIC_API_PREFIX } from '@/config'
import { useSearchParams } from '@/next/navigation'
import { postPublic } from '@/service/base'

/**
 * How long the "permission expired / missing" toast stays on screen. The
 * default 5s window is too short for a message the visitor has to act on
 * (contact an administrator), so it is deliberately doubled.
 */
const PERMISSION_TOAST_TIMEOUT = 10000

// Defensive i18n lookup. i18next returns the raw key (not undefined) when
// the translation is missing, so a plain `value || fallback` would still
// leak the key through. Compare against the requested key to detect this
// case and surface the hardcoded fallback instead.
const safeT = (
  t: (key: string, opts?: { ns?: string }) => string,
  key: string,
  ns: string,
  fallback: string,
) => {
  const value = t(key, { ns })
  if (value && value !== key)
    return value
  return fallback
}

type PermissionVerdict = 'allowed' | 'denied' | 'expired' | 'unknown'

/**
 * Extract the share / app code from a webapp URL such as ``/chat/<token>``.
 *
 * Returns ``null`` when the path does not look like a webapp route — in
 * that case the visitor either came from somewhere else, or typed
 * ``/oa-login`` directly. Either way we have no app to precheck, so the
 * caller should fall through to the existing ``window.location.replace``
 * behaviour.
 */
const extractAppCode = (rawRedirectUrl: string | null): string | null => {
  if (!rawRedirectUrl)
    return null
  try {
    // Handle accidental double-encoding (some clients encode the value
    // a second time when echoing it back). decodeURIComponent is a
    // no-op on an already-decoded string, so it's safe to call twice.
    let decoded = rawRedirectUrl
    try {
      decoded = decodeURIComponent(decoded)
    }
    catch {
      // keep as-is
    }
    try {
      decoded = decodeURIComponent(decoded)
    }
    catch {
      // keep as-is
    }
    const url = new URL(decoded, globalThis.location.origin)
    const match = url.pathname.match(/^\/(?:chat|chatbot|workflow|completion)\/([^/?#]+)/)
    return match && match[1] ? match[1] : null
  }
  catch {
    return null
  }
}

/**
 * Post-login permission precheck.
 *
 * Resolves the OA workcode into a passport, asks the target app whether the
 * visitor is allowed, and folds the result into one of four verdicts. On
 * any unexpected failure we return ``'unknown'`` so the caller can fall
 * back to the chat page instead of accusing the visitor of a permission
 * problem we could not actually determine.
 *
 * NOTE: this deliberately bypasses ``request()`` in ``web/service/base.ts``.
 * That helper hard-redirects OA users to ``/signin`` on a 401 from
 * ``/site``/``/webapp/permission`` (the ``refreshAccessTokenOrReLogin``
 * branch is not OA-aware for codes other than ``web_app_access_denied``
 * and ``web_sso_auth_required``), which is the wrong destination for an
 * OA-authenticated visitor. Direct ``fetch`` keeps the failure inside JS so
 * the caller can answer with a precise toast.
 */
const checkAppPermission = async (appCode: string): Promise<PermissionVerdict> => {
  const csrf = Cookies.get(CSRF_COOKIE_NAME()) || ''
  const baseHeaders: Record<string, string> = {
    'Content-Type': 'application/json',
    [CSRF_HEADER_NAME]: csrf,
    'X-App-Code': appCode,
  }

  try {
    // 1. Bootstrap a passport for the OA workcode. /api/passport reads
    //    the `oa_session` cookie and uses its workcode as session_id.
    //    Public webapp endpoints live under PUBLIC_API_PREFIX (NOT
    //    API_PREFIX, which is the Dify console API).
    const passportRes = await globalThis.fetch(
      `${PUBLIC_API_PREFIX}/passport?app_code=${encodeURIComponent(appCode)}`,
      { credentials: 'include', headers: baseHeaders },
    )
    if (!passportRes.ok) {
      if (typeof window !== 'undefined')
        console.warn(`[oa-login] /passport returned ${passportRes.status} for appCode=${appCode}`)
      return 'unknown'
    }
    const { access_token } = await passportRes.json().catch(() => ({})) as { access_token?: string }
    if (!access_token) {
      if (typeof window !== 'undefined')
        console.warn(`[oa-login] /passport response missing access_token for appCode=${appCode}`)
      return 'unknown'
    }

    // 2. /site is a WebApiResource so it will reject with 401/403 when
    //    the OA user is not allowed. The error body carries the reason
    //    code, which is enough to route without ever mounting the chat
    //    page (which would otherwise surface "应用不可用" 404).
    const siteRes = await globalThis.fetch(`${PUBLIC_API_PREFIX}/site`, {
      credentials: 'include',
      headers: { ...baseHeaders, Authorization: `Bearer ${access_token}` },
    })
    if (!siteRes.ok) {
      if (siteRes.status === 401 || siteRes.status === 403) {
        const err = await siteRes.json().catch(() => ({})) as { code?: string, message?: string }
        if (typeof window !== 'undefined')
          console.warn(`[oa-login] /site rejected (${siteRes.status}) code=${err.code} msg=${err.message}`)
        if (err.code === 'web_app_permission_expired')
          return 'expired'
        if (err.code === 'web_app_access_denied' || err.code === 'app_access_permission_denied')
          return 'denied'
      }
      return 'unknown'
    }
    const siteData = await siteRes.json().catch(() => ({})) as { app_id?: string }
    if (!siteData.app_id)
      return 'unknown'

    // 3. Canonical check — /webapp/permission returns a structured
    //    {result, reason} for both enterprise and per-app allowlists.
    const permRes = await globalThis.fetch(
      `${PUBLIC_API_PREFIX}/webapp/permission?appId=${encodeURIComponent(siteData.app_id)}`,
      { credentials: 'include', headers: baseHeaders },
    )
    if (!permRes.ok)
      return 'unknown'
    const perm = await permRes.json().catch(() => ({})) as { result: boolean, reason?: string }
    if (perm.result)
      return 'allowed'
    if (perm.reason === 'expired')
      return 'expired'
    // Should be unreachable right after a successful login (the oa_session
    // cookie is already set, so the app's anonymous policy is satisfied).
    // Treated as 'unknown' rather than 'denied' on purpose: sending the
    // visitor to the "no permission" page would blame the wrong problem.
    if (perm.reason === 'auth_required')
      return 'unknown'
    return 'denied'
  }
  catch (err) {
    if (typeof window !== 'undefined')
      console.warn(`[oa-login] checkAppPermission threw`, err)
    return 'unknown'
  }
}

/**
 * OA webapp sign-in page.
 *
 * Reached when the webapp gates reject a visitor from ``/chat/<token>``:
 * ``AuthenticatedLayout`` probes ``/webapp/permission`` and, on
 * ``reason === 'auth_required'``, redirects here with the original URL as
 * ``redirect_url`` (see
 * ``web/app/(shareLayout)/components/authenticated-layout.tsx``).
 *
 * The form POSTs ``{loginid, password}`` to ``POST /api/oa/login`` which (on
 * success) sets the ``oa_session`` cookie; we then precheck the visitor's
 * permission against the target app and fold the result into three outcomes:
 *
 *   1. ``allowed`` (or an indeterminate ``unknown``) — hand off to the
 *      original ``/chat/<token>`` URL.
 *   2. ``expired`` — the visitor *was* on the app's allowlist but the row's
 *      ``expires_at`` has passed. Only an admin can renew it, so we stay on
 *      this page and raise a toast pointing at the administrator.
 *   3. ``denied`` — the visitor was never on the allowlist. Same treatment,
 *      different wording ("not authorised" vs "expired").
 *
 * Outcomes 2 and 3 deliberately do NOT navigate. The visitor keeps the form,
 * which lets them retry with a different account, and the OA session is
 * intentionally left in place: the identity is valid, it is the *permission*
 * that is missing — so once an admin grants it, a plain refresh is enough.
 * A toast is also what the requirement asks for (a top-right notice rather
 * than a dedicated dead-end page). The standalone ``/webapp-no-permission``
 * and ``/webapp-permission-expired`` pages are still reached from the
 * mid-session 401/403 handlers in ``web/service/base.ts``, where a full page
 * is the better fit because the chat UI is already mounted.
 */
const OALoginPage: FC = () => {
  const { t } = useTranslation()
  const searchParams = useSearchParams()
  const redirectUrl = searchParams.get('redirect_url')

  const [loginid, setLoginid] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [saveLoginid, setSaveLoginid] = useState(false)
  const [savePassword, setSavePassword] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  // True while the post-login permission precheck is running. We render a
  // dedicated loading screen in this state so the visitor never sees the
  // form reappear after a successful submit (which would look like a no-op).
  const [checking, setChecking] = useState(false)

  const handleSubmit = useCallback(async () => {
    if (!loginid.trim()) {
      toast.error(t('oaLogin.error.usernameEmpty', { ns: 'common' }))
      return
    }
    if (!password) {
      toast.error(t('oaLogin.error.passwordEmpty', { ns: 'common' }))
      return
    }

    setSubmitting(true)
    try {
      await postPublic('/oa/login', {
        body: {
          loginid: loginid.trim(),
          password,
        },
      })
      // Login OK → server has set the `oa_session` cookie.
      const target = redirectUrl ? decodeURIComponent(redirectUrl) : null
      const appCode = extractAppCode(redirectUrl)

      // Non-webapp redirect (or none at all): keep the legacy behaviour
      // and hand off to the original URL.
      if (!target || !appCode) {
        window.location.replace(target || '/')
        return
      }

      // Swap the submitting spinner for the post-login permission check.
      // Doing it as a separate state means the form is no longer
      // interactive and we can render a dedicated message instead of
      // the (now-stale) form fields.
      setSubmitting(false)
      setChecking(true)

      const verdict = await checkAppPermission(appCode)
      if (verdict === 'expired') {
        toast.error(safeT(t, 'oaLogin.permissionExpired.title', 'common', '权限已过期'), {
          description: safeT(
            t,
            'oaLogin.permissionExpired.description',
            'common',
            '您的访问权限已过期，请联系管理员续期后重新登录。',
          ),
          timeout: PERMISSION_TOAST_TIMEOUT,
        })
        return
      }
      if (verdict === 'denied') {
        toast.error(safeT(t, 'oaLogin.permissionDenied.title', 'common', '权限缺失'), {
          description: safeT(
            t,
            'oaLogin.permissionDenied.description',
            'common',
            '您未被授权访问此应用，请联系管理员开通权限后重新登录。',
          ),
          timeout: PERMISSION_TOAST_TIMEOUT,
        })
        return
      }
      // 'allowed' or 'unknown' — let the chat page take over. window.location
      // (not a client-side router call) so the whole app tree, including
      // Splash and AuthenticatedLayout, re-runs against the freshly set
      // `oa_session` cookie.
      window.location.replace(target)
    }
    catch (err) {
      // The backend raises 400 / 401 / 503 with a localised `description`
      // already (see api/controllers/web/oa_auth.py); surface it verbatim.
      const code = (err as { code?: string }).code
      if (code === 'invalid_credentials' || code === '401') {
        toast.error(t('oaLogin.error.invalidCredentials', { ns: 'common' }))
      }
      else {
        toast.error(t('oaLogin.error.networkError', { ns: 'common' }))
      }
    }
    finally {
      setSubmitting(false)
      setChecking(false)
    }
  }, [loginid, password, redirectUrl, t])

  if (checking) {
    const checkingText = safeT(t, 'oaLogin.checkingPermission', 'common', '正在校验您的访问权限，请稍候…')
    return (
      <div className="flex min-h-screen w-full items-center justify-center bg-background-default-subtle p-6">
        <div className="flex w-full max-w-[400px] flex-col items-center gap-4 rounded-2xl bg-components-panel-bg p-10 shadow-sm">
          <Loading />
          <p className="system-sm-regular text-text-tertiary">{checkingText}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen w-full items-center justify-center bg-background-default-subtle p-6">
      <div className="w-full max-w-[400px] rounded-2xl bg-components-panel-bg p-8 shadow-sm">
        <header className="mb-8 flex flex-col items-center gap-1">
          <h1 className="system-2xl-semibold text-text-primary">
            {t('oaLogin.title', { ns: 'common' })}
          </h1>
          <p className="system-sm-regular text-text-tertiary">
            {t('oaLogin.subtitle', { ns: 'common' })}
          </p>
        </header>

        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault()
            handleSubmit()
          }}
        >
          <div className="flex flex-col gap-1">
            <label
              htmlFor="oa-login-loginid"
              className="system-sm-medium text-text-secondary"
            >
              {t('oaLogin.username', { ns: 'common' })}
            </label>
            <Input
              id="oa-login-loginid"
              type="text"
              value={loginid}
              onChange={e => setLoginid(e.target.value)}
              placeholder={t('oaLogin.usernamePlaceholder', { ns: 'common' })}
              autoComplete="username"
              tabIndex={1}
            />
          </div>

          <div className="flex flex-col gap-1">
            <label
              htmlFor="oa-login-password"
              className="system-sm-medium text-text-secondary"
            >
              {t('oaLogin.password', { ns: 'common' })}
            </label>
            <div className="relative">
              <Input
                id="oa-login-password"
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={e => setPassword(e.target.value)}
                placeholder={t('oaLogin.passwordPlaceholder', { ns: 'common' })}
                autoComplete="current-password"
                tabIndex={2}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !submitting)
                    handleSubmit()
                }}
              />
              <button
                type="button"
                aria-label={showPassword
                  ? t('oaLogin.hidePassword', { ns: 'common' })
                  : t('oaLogin.showPassword', { ns: 'common' })}
                className="absolute inset-y-0 right-2 flex items-center system-sm-regular text-text-tertiary"
                onClick={() => setShowPassword(p => !p)}
              >
                {showPassword ? '🙈' : '👁'}
              </button>
            </div>
          </div>

          <div className="mt-1 flex items-center gap-6 system-sm-regular text-text-secondary">
            <label className="flex cursor-pointer items-center gap-2">
              <input
                type="checkbox"
                checked={saveLoginid}
                onChange={e => setSaveLoginid(e.target.checked)}
                className="h-4 w-4"
              />
              {t('oaLogin.saveLoginid', { ns: 'common' })}
            </label>
            <label className="flex cursor-pointer items-center gap-2">
              <input
                type="checkbox"
                checked={savePassword}
                onChange={e => setSavePassword(e.target.checked)}
                className="h-4 w-4"
              />
              {t('oaLogin.savePassword', { ns: 'common' })}
            </label>
          </div>

          <Button
            type="submit"
            variant="primary"
            className="mt-2 w-full"
            loading={submitting}
            disabled={submitting || !loginid.trim() || !password}
            tabIndex={3}
          >
            {submitting
              ? t('oaLogin.submitting', { ns: 'common' })
              : t('oaLogin.submit', { ns: 'common' })}
          </Button>
        </form>
      </div>
    </div>
  )
}

export default React.memo(OALoginPage)
