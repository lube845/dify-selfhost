'use client'

// {{account}} is interpolated by i18next with escapeValue: false, but
// rendered as a React text node below, which auto-escapes HTML. Do NOT
// switch to dangerouslySetInnerHTML or <Trans components={{...}}>.

import * as React from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from '@/next/navigation'

// Defensive i18n lookup. i18next returns the raw key (not undefined) when
// the translation is missing, so a plain `value || fallback` would still
// leak the key through. Compare against the requested key to detect this
// case and surface the hardcoded fallback instead — and also never return
// `undefined`, which would render as React error #300 in production.
//
// The fallback copy is intentionally zh-Hans to match the sibling pages
// (`webapp-no-permission`, `webapp-permission-expired`). When `options`
// is provided, interpolation values are forwarded to i18next (e.g.
// `{{account}}`) and the caller is responsible for baking them into the
// fallback string itself.
const safeT = (
  t: (key: string, opts?: Record<string, unknown>) => string,
  key: string,
  ns: string,
  fallback: string,
  options?: Record<string, unknown>,
) => {
  const value = t(key, { ns, ...options })
  if (value && value !== key)
    return value
  return fallback
}

// Standalone "signed out" page reached from the explicit OA sign-out
// click in `webapp/.../sidebar/oa-user-info.tsx`. The page is purely
// informational: no buttons, no links, no form, no auto-redirect — by
// design. To start a new session the visitor has to navigate back to a
// `/chat/<token>` URL themselves, which then routes through
// `AuthenticatedLayout` → `/oa-login` if their OA session is gone.
const WebappLoggedOutPage: React.FC = () => {
  const { t } = useTranslation()
  const searchParams = useSearchParams()

  // `searchParams.get` returns `null` when absent and `''` when present
  // but empty. Both are falsy, so the generic copy wins in either case.
  const account = searchParams.get('account') || ''

  const message = account
    ? safeT(t, 'webapp.loggedOut.withAccount', 'common', `账号 ${account} 已退出登录。`, { account })
    : safeT(t, 'webapp.loggedOut.generic', 'common', '您已退出登录。')

  return (
    <div className="flex min-h-screen w-full items-center justify-center bg-background-default-subtle p-6">
      <div className="flex w-full max-w-[480px] flex-col items-center gap-6 rounded-2xl bg-components-panel-bg p-10 text-center shadow-sm">
        <h1
          aria-live="polite"
          aria-atomic="true"
          className="system-xl-semibold text-text-primary"
        >
          {message}
        </h1>
      </div>
    </div>
  )
}

export default React.memo(WebappLoggedOutPage)
