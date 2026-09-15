'use client'

import type { FC } from 'react'
import { Avatar } from '@langgenius/dify-ui/avatar'
import { cn } from '@langgenius/dify-ui/cn'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@langgenius/dify-ui/dropdown-menu'
import { RiLogoutBoxRLine } from '@remixicon/react'
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import ActionButton from '@/app/components/base/action-button'
import { useChatWithHistoryContext } from '@/app/components/base/chat/chat-with-history/context'
import { useWebAppStore } from '@/context/web-app-context'
import { useRouter } from '@/next/navigation'
import { useOASession } from '@/service/oa-session'
import { webAppLogout } from '@/service/webapp-auth'

/** Where an OA visitor lands after signing out. */
const OA_LOGIN_PATH = '/oa-login'

/**
 * Upper bound for `setTimeout`. Browsers clamp the delay to a signed 32-bit
 * integer, so anything larger fires immediately. The real window is
 * `OA_SESSION_EXPIRE_HOURS` (8h), making this a guard rather than a limit
 * that is ever hit in practice.
 */
const MAX_TIMEOUT_MS = 2 ** 31 - 1

const InfoRow = ({ label, value }: { label: string, value: string }) => (
  <div className="flex items-baseline gap-2">
    <span className="shrink-0 text-text-tertiary">{label}</span>
    <span className="min-w-0 grow truncate text-right text-text-secondary">{value}</span>
  </div>
)

/**
 * Signed-in OA user, shown at the bottom-left of the chat sidebar.
 *
 * Renders nothing at all for visitors without an `oa_session` — i.e. anonymous
 * visitors of an app that allows anonymous access (`App.allow_anonymous`).
 * Those never sign in, so they have no identity to display and no session to
 * end; showing a logout button would be meaningless.
 *
 * The trigger surfaces the details (name / workcode / department / session
 * deadline) on click; the adjacent icon button ends the session and returns to
 * /oa-login. The session is re-verified on mount rather than read from a prop,
 * so the block cannot outlive the cookie it represents.
 */
const OAUserInfo: FC = () => {
  const { t } = useTranslation()
  const router = useRouter()
  const { isInstalledApp } = useChatWithHistoryContext()
  const shareCode = useWebAppStore(s => s.shareCode)
  const { data: session } = useOASession({ enabled: !isInstalledApp })
  const [open, setOpen] = useState(false)
  const [loggingOut, setLoggingOut] = useState(false)

  const handleLogout = useCallback(async () => {
    setOpen(false)
    setLoggingOut(true)
    try {
      // Clears the `oa_session` cookie *and* the webapp passport / access token
      // cached in localStorage, so the next visitor on this browser cannot
      // inherit this identity by reloading /chat/<code>.
      if (shareCode)
        await webAppLogout(shareCode)
    }
    catch {
      // A failed sign-out must not strand the visitor on the chat page —
      // navigating away is the whole point of the button.
    }
    router.replace(OA_LOGIN_PATH)
  }, [router, shareCode])

  const expiresAtIso = session?.expires_at ?? null

  // Proactive re-login. The backend already treats a lapsed cookie as
  // anonymous (`wraps.py` rejects the next chat request, `app.py` reports
  // `auth_required` to the precheck), but a tab left open would otherwise
  // never notice the 8-hour window had closed.
  useEffect(() => {
    if (!expiresAtIso)
      return

    const msLeft = new Date(expiresAtIso).getTime() - Date.now()
    if (Number.isNaN(msLeft))
      return

    if (msLeft <= 0) {
      router.replace(OA_LOGIN_PATH)
      return
    }

    const timer = setTimeout(() => router.replace(OA_LOGIN_PATH), Math.min(msLeft, MAX_TIMEOUT_MS))
    return () => clearTimeout(timer)
  }, [expiresAtIso, router])

  if (!session)
    return null

  const displayName = session.name || session.workcode
  const notProvided = '—'
  const expiresAtLabel = expiresAtIso
    ? new Date(expiresAtIso).toLocaleString()
    : notProvided
  const logoutLabel = t('oaUser.logout', { ns: 'common' })

  return (
    <div className="flex shrink-0 items-center gap-1 px-3 pt-1">
      <DropdownMenu open={open} onOpenChange={setOpen}>
        <DropdownMenuTrigger
          aria-label={t('oaUser.viewProfile', { ns: 'common' })}
          className={cn(
            'flex min-w-0 grow items-center gap-2 rounded-lg p-1.5 text-left',
            'hover:bg-state-base-hover focus-visible:bg-state-base-hover focus-visible:outline-hidden',
            open && 'bg-state-base-hover',
          )}
        >
          <Avatar size="sm" name={displayName} avatar={null} />
          <span className="min-w-0 grow">
            <span className="block truncate system-sm-medium text-text-secondary">{displayName}</span>
            <span className="block truncate system-2xs-regular text-text-tertiary">{session.workcode}</span>
          </span>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          placement="top-start"
          sideOffset={4}
          popupClassName="w-[248px]"
        >
          <div className="flex flex-col gap-1.5 px-3 py-2 system-sm-regular">
            <InfoRow label={t('oaUser.name', { ns: 'common' })} value={session.name || notProvided} />
            <InfoRow label={t('oaUser.workcode', { ns: 'common' })} value={session.workcode} />
            <InfoRow label={t('oaUser.department', { ns: 'common' })} value={session.department || notProvided} />
            <InfoRow label={t('oaUser.sessionExpiresAt', { ns: 'common' })} value={expiresAtLabel} />
          </div>
          <DropdownMenuSeparator className="my-0" />
          <DropdownMenuItem className="px-3 system-md-regular" onClick={handleLogout}>
            {logoutLabel}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <ActionButton
        size="l"
        disabled={loggingOut}
        aria-label={logoutLabel}
        onClick={handleLogout}
      >
        <RiLogoutBoxRLine className="h-[18px] w-[18px]" />
      </ActionButton>
    </div>
  )
}

export default OAUserInfo
