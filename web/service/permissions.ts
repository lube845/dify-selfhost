import type { AppAccessPolicy } from '@/models/app-permission'
import { consoleClient } from './client'

export const fetchPermissionsApps = () => {
  return consoleClient.permissions.apps({})
}

/**
 * Partial update of an app's access policy. The console sends exactly the
 * field it flipped, so the backend keeps the other one untouched.
 */
export type AppPermissionUpdate = {
  access_policy?: AppAccessPolicy
  allow_anonymous?: boolean
}

export const updateAppPermission = (appId: string, body: AppPermissionUpdate) => {
  return consoleClient.permissions.appUpdate({
    params: { appId },
    body,
  })
}

export const updateAppAccessPolicy = (appId: string, accessPolicy: AppAccessPolicy) => {
  return updateAppPermission(appId, { access_policy: accessPolicy })
}

export const updateAppAllowAnonymous = (appId: string, allowAnonymous: boolean) => {
  return updateAppPermission(appId, { allow_anonymous: allowAnonymous })
}

export const fetchWhitelist = (appId: string) => {
  return consoleClient.permissions.whitelistList({
    params: { appId },
  })
}

export const grantWhitelistUsers = (appId: string, userIds: string[], expiresAt: string | null) => {
  return consoleClient.permissions.whitelistCreate({
    params: { appId },
    body: { user_ids: userIds, expires_at: expiresAt },
  })
}

export const updateWhitelistExpiry = (appId: string, permId: string, expiresAt: string | null) => {
  return consoleClient.permissions.whitelistUpdate({
    params: { appId, permId },
    body: { expires_at: expiresAt },
  })
}

export const revokeWhitelistUser = (appId: string, permId: string) => {
  return consoleClient.permissions.whitelistDelete({
    params: { appId, permId },
  })
}
