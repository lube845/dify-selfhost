import { ACCESS_TOKEN_LOCAL_STORAGE_NAME, PASSPORT_LOCAL_STORAGE_NAME } from '@/config'
import { getPublic, postPublic } from './base'

export function setWebAppAccessToken(token: string) {
  localStorage.setItem(ACCESS_TOKEN_LOCAL_STORAGE_NAME, token)
}

export function setWebAppPassport(shareCode: string, token: string) {
  localStorage.setItem(PASSPORT_LOCAL_STORAGE_NAME(shareCode), token)
}

export function getWebAppAccessToken() {
  return localStorage.getItem(ACCESS_TOKEN_LOCAL_STORAGE_NAME) || ''
}

export function getWebAppPassport(shareCode: string) {
  return localStorage.getItem(PASSPORT_LOCAL_STORAGE_NAME(shareCode)) || ''
}

function clearWebAppAccessToken() {
  localStorage.removeItem(ACCESS_TOKEN_LOCAL_STORAGE_NAME)
}

function clearWebAppPassport(shareCode: string) {
  localStorage.removeItem(PASSPORT_LOCAL_STORAGE_NAME(shareCode))
}

/**
 * Drop the Dify webapp credentials cached in localStorage for `shareCode`.
 *
 * The passport is a long-lived JWT that carries the end_user identity, so it
 * must NOT outlive a sign-out: the next visitor on this browser would
 * otherwise send the previous user's passport and inherit their conversations.
 * Exported for the OA sidebar's logout, which clears the OA session without
 * going through the enterprise webapp logout.
 */
export function clearWebAppLocalTokens(shareCode: string) {
  clearWebAppAccessToken()
  clearWebAppPassport(shareCode)
}

type isWebAppLogin = {
  logged_in: boolean
  app_logged_in: boolean
}

export async function webAppLoginStatus(shareCode: string, userId?: string) {
  // always need to check login to prevent passport from being outdated
  // check remotely, the access token could be in cookie (enterprise SSO redirected with https)
  const params = new URLSearchParams({ app_code: shareCode })
  if (userId)
    params.append('user_id', userId)
  const { logged_in, app_logged_in } = await getPublic<isWebAppLogin>(`/login/status?${params.toString()}`)
  return {
    userLoggedIn: logged_in,
    appLoggedIn: app_logged_in,
  }
}

export async function webAppLogout(shareCode: string) {
  clearWebAppLocalTokens(shareCode)
  await postPublic('/logout')
  // Also clear the OA session cookie set by `/api/oa/login`. Without this,
  // the visitor would still be OA-authenticated after the explicit logout
  // button and the webapp gates would let them straight back in.
  // The endpoint is safe to call even when no OA session exists — it just
  // sets the cookie to an empty value with `Max-Age=0`.
  try {
    await postPublic('/oa/logout')
  }
  catch {
    // Network / 4xx / 5xx: not fatal for the webapp logout flow. Local
    // token cleanup has already happened above.
  }
}
