import { useQuery } from '@tanstack/react-query'
import Cookies from 'js-cookie'
import { CSRF_COOKIE_NAME, CSRF_HEADER_NAME, PUBLIC_API_PREFIX } from '@/config'

const NAME_SPACE = 'oa-session'

export type OASession = {
  /** OA workcode — the identity the passport is minted for. */
  workcode: string
  name: string
  department: string
  /** ISO-8601 UTC instant at which the `oa_session` cookie stops being valid. */
  expires_at: string | null
  /** Configured `OA_SESSION_EXPIRE_HOURS` (8 by default). */
  session_expire_hours: number
}

/**
 * Read the current `oa_session` cookie's user info, or `null` when not signed in.
 *
 * Deliberately a bare `fetch` rather than `getPublic()`. For every anonymous
 * visitor — i.e. everyone on an app that allows anonymous access — this
 * endpoint answers **401**, and `getPublic` would feed that into the global
 * 401 handlers in `service/base.ts`, redirecting them to `/webapp-signin` or
 * `/oa-login`. A 401 here is a normal answer meaning "no OA session", not a
 * failure worth a redirect, so it is handled locally.
 *
 * Never throws: any error (offline, malformed body, 5xx) resolves to `null`,
 * which the UI renders as "no user block" rather than an error state.
 */
export async function fetchOASession(): Promise<OASession | null> {
  try {
    const res = await fetch(`${PUBLIC_API_PREFIX}/oa/me`, {
      method: 'GET',
      credentials: 'include',
      headers: {
        [CSRF_HEADER_NAME]: Cookies.get(CSRF_COOKIE_NAME()) || '',
      },
    })
    if (!res.ok)
      return null

    const data = await res.json().catch(() => null) as Partial<OASession> | null
    if (!data?.workcode)
      return null

    return {
      workcode: data.workcode,
      name: data.name || '',
      department: data.department || '',
      expires_at: data.expires_at ?? null,
      session_expire_hours: data.session_expire_hours ?? 0,
    }
  }
  catch {
    return null
  }
}

/**
 * The signed-in OA user for the current browser, if any.
 *
 * `staleTime: 0` + `gcTime: 0` mirrors `useGetUserCanAccessApp`: the answer
 * decides whether the logout button is even rendered, so it must never be
 * served from a stale cache (notably after a logout or a 8-hour expiry).
 * `retry: false` because a `null` result is a legitimate value, not an error.
 */
export const useOASession = ({ enabled = true }: { enabled?: boolean } = {}) => {
  return useQuery({
    queryKey: [NAME_SPACE, 'me'],
    queryFn: fetchOASession,
    enabled,
    staleTime: 0,
    gcTime: 0,
    retry: false,
  })
}
