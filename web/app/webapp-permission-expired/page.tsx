'use client'
import { Button } from '@langgenius/dify-ui/button'
import { toast } from '@langgenius/dify-ui/toast'
import * as React from 'react'
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useRouter } from '@/next/navigation'
import { postPublic } from '@/service/base'

// Standalone "permission expired" page.
//
// Reached when AuthenticatedLayout on /chat/<token> sees the backend
// precheck return {result: false, reason: 'expired'} — i.e. the user WAS
// on the allowlist but their AppAccessPermission.expires_at has passed.
// Distinct copy and CTA vs. /webapp-no-permission so users with a lapsed
// row know the right next step is renewal, not a fresh access request.

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

const WebappPermissionExpiredPage: React.FC = () => {
  const { t } = useTranslation()
  const router = useRouter()
  const [signingOut, setSigningOut] = useState(false)

  const title = safeT(t, 'webapp.authExpired', 'common', '您的访问权限已过期，请联系管理员续期。')
  const hint = safeT(t, 'webapp.authExpiredHint', 'common', '您的访问权限已到期，请联系管理员为您续期后重新登录。')
  const cta = safeT(t, 'userProfile.logout', 'common', '退出登录')

  // Raise the top-right notice the requirement asks for. It has to happen on
  // *this* page rather than the one that navigated here: this route is reached
  // by a full-page navigation from a rejected chat request (see the 401/403
  // branches in ``service/base.ts``), which would tear down any toast the
  // previous document had raised. The wording is taken from the same
  // ``oaLogin.permissionExpired.*`` keys the login-time check uses, so the
  // mid-session and at-login notices read identically.
  useEffect(() => {
    toast.error(safeT(t, 'oaLogin.permissionExpired.title', 'common', '权限已过期'), {
      description: safeT(
        t,
        'oaLogin.permissionExpired.description',
        'common',
        '您的访问权限已过期，请联系管理员续期后重新登录。',
      ),
      timeout: PERMISSION_TOAST_TIMEOUT,
    })
  }, [t])

  const handleBackToSignIn = useCallback(async () => {
    setSigningOut(true)
    try {
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
          <span className="system-2xl-semibold">401</span>
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

export default React.memo(WebappPermissionExpiredPage)