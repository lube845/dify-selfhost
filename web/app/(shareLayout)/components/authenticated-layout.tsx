'use client'

import * as React from 'react'
import { useCallback, useEffect } from 'react'
import AppUnavailable from '@/app/components/base/app-unavailable'
import Loading from '@/app/components/base/loading'
import { useWebAppStore } from '@/context/web-app-context'
import { usePathname, useRouter, useSearchParams } from '@/next/navigation'
import { useGetUserCanAccessApp } from '@/service/access-control'
import { useGetWebAppInfo, useGetWebAppMeta, useGetWebAppParams } from '@/service/use-share'
import { webAppLogout } from '@/service/webapp-auth'

const AuthenticatedLayout = ({ children }: { children: React.ReactNode }) => {
  const shareCode = useWebAppStore(s => s.shareCode)
  const updateAppInfo = useWebAppStore(s => s.updateAppInfo)
  const updateAppParams = useWebAppStore(s => s.updateAppParams)
  const updateWebAppMeta = useWebAppStore(s => s.updateWebAppMeta)
  const updateUserCanAccessApp = useWebAppStore(s => s.updateUserCanAccessApp)
  // All four fetches have their errors surfaced as a page-level AppUnavailable
  // (or the permission-denied / permission-expired branches below). Suppress
  // the global fetch hook toast for them so the user only sees ONE clear
  // message, not a stack of per-query toasts.
  const { isFetching: isFetchingAppParams, data: appParams, error: appParamsError } = useGetWebAppParams({ silent: true })
  const { isFetching: isFetchingAppInfo, data: appInfo, error: appInfoError } = useGetWebAppInfo({ silent: true })
  const { isFetching: isFetchingAppMeta, data: appMeta, error: appMetaError } = useGetWebAppMeta({ silent: true })
  const { data: userCanAccessApp, error: useCanAccessAppError } = useGetUserCanAccessApp({ appId: appInfo?.app_id, isInstalledApp: false, silent: true })

  useEffect(() => {
    if (appInfo)
      updateAppInfo(appInfo)
    if (appParams)
      updateAppParams(appParams)
    if (appMeta)
      updateWebAppMeta(appMeta)
    updateUserCanAccessApp(Boolean(userCanAccessApp && userCanAccessApp?.result))
  }, [appInfo, appMeta, appParams, updateAppInfo, updateAppParams, updateUserCanAccessApp, updateWebAppMeta, userCanAccessApp])

  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const getSigninUrl = useCallback(() => {
    const params = new URLSearchParams(searchParams)
    params.delete('message')
    const query = params.toString()
    const fullPath = query ? `${pathname}?${query}` : pathname
    params.set('redirect_url', fullPath)
    return `/webapp-signin?${params.toString()}`
  }, [searchParams, pathname])

  const backToHome = useCallback(async () => {
    await webAppLogout(shareCode!)
    const url = getSigninUrl()
    router.replace(url)
  }, [getSigninUrl, router, shareCode])

  // Where to send a visitor whose app does not accept anonymous visitors.
  // The current webapp URL rides along as `redirect_url` so /oa-login can
  // hand them straight back after a successful sign-in.
  const getOALoginUrl = useCallback(() => {
    const params = new URLSearchParams(searchParams)
    const query = params.toString()
    const fullPath = query ? `${pathname}?${query}` : pathname
    return `/oa-login?redirect_url=${encodeURIComponent(fullPath)}`
  }, [searchParams, pathname])

  // Four-state permission gate, driven by the backend `/webapp/permission`
  // endpoint's `{ result, reason }`:
  //   - allowed        → render the chat
  //   - denied         → the visitor was never on the allowlist; needs an admin
  //   - expired        → they were on it but the row lapsed; needs an admin
  //   - auth_required  → the app turned anonymous access off; the visitor can
  //                      self-recover by signing in via /oa-login
  // The first three are terminal standalone pages; `auth_required` is a
  // redirect, not a dead end.
  //
  // IMPORTANT: hooks MUST run before any conditional returns below.
  // React's Rules of Hooks require hooks to fire in the same order on every
  // render. The error-return ``if`` statements below are conditional returns,
  // and the redirect effects must be declared above them — otherwise the
  // hook order shifts between renders and React throws minified #300 at
  // runtime.
  const shouldRedirectAway = Boolean(userCanAccessApp && !userCanAccessApp.result)
  useEffect(() => {
    if (shouldRedirectAway) {
      if (userCanAccessApp?.reason === 'auth_required') {
        router.replace(getOALoginUrl())
        return
      }
      const target = userCanAccessApp?.reason === 'expired'
        ? '/webapp-permission-expired'
        : '/webapp-no-permission'
      router.replace(target)
    }
  }, [shouldRedirectAway, router, userCanAccessApp, getOALoginUrl])

  if (appInfoError) {
    return (
      <div className="flex h-full items-center justify-center">
        <AppUnavailable unknownReason={appInfoError.message} />
      </div>
    )
  }
  if (appParamsError) {
    return (
      <div className="flex h-full items-center justify-center">
        <AppUnavailable unknownReason={appParamsError.message} />
      </div>
    )
  }
  if (appMetaError) {
    return (
      <div className="flex h-full items-center justify-center">
        <AppUnavailable unknownReason={appMetaError.message} />
      </div>
    )
  }
  if (useCanAccessAppError) {
    return (
      <div className="flex h-full items-center justify-center">
        <AppUnavailable unknownReason={useCanAccessAppError.message} />
      </div>
    )
  }
  if (shouldRedirectAway) {
    return null
  }
  if (isFetchingAppInfo || isFetchingAppParams || isFetchingAppMeta) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loading />
      </div>
    )
  }
  return <>{children}</>
}

export default React.memo(AuthenticatedLayout)
