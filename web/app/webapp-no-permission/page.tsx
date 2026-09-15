'use client'
import { Button } from '@langgenius/dify-ui/button'
import { toast } from '@langgenius/dify-ui/toast'
import * as React from 'react'
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useRouter } from '@/next/navigation'
import { postPublic } from '@/service/base'

// Standalone "no permission" page.
//
// Reached when AuthenticatedLayout on /chat/<token> sees the backend precheck
// return {result: false, reason: 'denied'} — i.e. the user is authenticated
// but not on the app's allowlist. We render a clean message and a single CTA:
// clear the OA session cookie and route the visitor back to /oa-login. No
// chat UI is mounted in this state.

// Defensive i18n lookup. i18next returns the raw key (not undefined) when
// the translation is missing, so a plain `value || fallback` would still
// leak the key through. Compare against the requested key to detect this
// case and surface the hardcoded fallback instead — and also never return
// `undefined`, which would render as React error #300 in production.
const safeT = (
  t: (key: string, opts?: { ns?: string }) => string,
  key: string,
  ns: string,
  fallback: string,
) => {
  const value = t(key, { ns })
  if (value && value !== key) return value
  return fallback
}

/**
 * How long the top-right notice stays on screen. Mirrors the login page: the
 * default 5s is too short for a message the visitor has to act on.
 */
const PERMISSION_TOAST_TIMEOUT = 10000

const WebappNoPermissionPage: React.FC = () => {
  const { t } = useTranslation()
  const router = useRouter()
  const [signingOut, setSigningOut] = useState(false)

  const title = safeT(t, 'webapp.accessDenied', 'common', '您未被授权访问此应用，请联系管理员开通权限。')
  const hint = safeT(t, 'webapp.accessDeniedHint', 'common', '您的账号不在该应用的访问名单中，请联系管理员为您开通权限后重新登录。')
  const cta = safeT(t, 'userProfile.logout', 'common', '退出登录')

  // Top-right notice, as specified. Raised here rather than on the page that
  // navigated away: this route is reached by a full-page navigation from a
  // rejected chat request (see the 403 branch in ``service/base.ts``), which
  // would tear down any toast the previous document had raised. Wording comes
  // from the same ``oaLogin.permissionDenied.*`` keys the login-time check
  // uses, so the mid-session and at-login notices read identically.
  useEffect(() => {
    toast.error(safeT(t, 'oaLogin.permissionDenied.title', 'common', '权限缺失'), {
      description: safeT(
        t,
        'oaLogin.permissionDenied.description',
        'common',
        '您未被授权访问此应用，请联系管理员开通权限后重新登录。',
      ),
      timeout: PERMISSION_TOAST_TIMEOUT,
    })
  }, [t])

  const handleBackToSignIn = useCallback(async () => {
    setSigningOut(true)
    try {
      // Clear the OA session cookie set by /api/oa/login. Without this the
      // Next.js middleware (proxy.ts) keeps the visitor out of /oa-login
      // even after they hit the logout button. Safe to call even when no
      // OA session exists — the endpoint just sets the cookie to empty.
      await postPublic('/oa/logout').catch(() => undefined)
    }
    finally {
      router.replace('/oa-login')
    }
  }, [router])

  return (
    <div className="flex min-h-screen w-full items-center justify-center bg-background-default-subtle p-6">
      <div className="flex w-full max-w-[480px] flex-col items-center gap-6 rounded-2xl bg-components-panel-bg p-10 text-center shadow-sm">
        <div
          aria-hidden="true"
          className="flex h-14 w-14 items-center justify-center rounded-full"
        >
          <span className="system-2xl-semibold">403</span>
        </div>
        <div className="flex flex-col gap-2">
          <h1 className="system-xl-semibold text-text-primary">{title}</h1>
          <p className="system-sm-regular text-text-tertiary">{hint}</p>
        </div>
        <Button
          variant="primary"
          loading={signingOut}
          disabled={signingOut}
          onClick={handleBackToSignIn}
        >
          {cta}
        </Button>
      </div>
    </div>
  )
}

export default React.memo(WebappNoPermissionPage)