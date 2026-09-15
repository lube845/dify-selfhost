import type { AppAccessPolicy } from '@/models/app-permission'
import type { WhitelistEntry } from '@/models/app-permission'
import { type } from '@orpc/contract'
import { base } from '../base'

type PermissionAppItem = {
  id: string
  name: string
  access_policy: AppAccessPolicy
  allow_anonymous: boolean
}

type PermissionAppListResponse = {
  data: PermissionAppItem[]
}

type WhitelistEntryListResponse = {
  data: WhitelistEntry[]
}

type WhitelistCreateResponse = {
  data: WhitelistEntry[]
  skipped: string[]
}

type ResultResponse = {
  result: 'success'
}

export const permissionsAppsContract = base
  .route({
    path: '/permissions/apps',
    method: 'GET',
  })
  .input(type<{}>())
  .output(type<PermissionAppListResponse>())

export const permissionsAppUpdateContract = base
  .route({
    path: '/permissions/apps/{appId}',
    method: 'PATCH',
  })
  .input(type<{
    params: { appId: string }
    // Partial update: the console flips one switch at a time, so both fields
    // are optional but at least one must be sent (enforced server-side).
    body: {
      access_policy?: AppAccessPolicy
      allow_anonymous?: boolean
    }
  }>())
  .output(type<PermissionAppItem>())

export const permissionsWhitelistListContract = base
  .route({
    path: '/permissions/apps/{appId}/whitelist',
    method: 'GET',
  })
  .input(type<{ params: { appId: string } }>())
  .output(type<WhitelistEntryListResponse>())

export const permissionsWhitelistCreateContract = base
  .route({
    path: '/permissions/apps/{appId}/whitelist',
    method: 'POST',
  })
  .input(type<{
    params: { appId: string }
    body: {
      user_ids: string[]
      expires_at: string | null
    }
  }>())
  .output(type<WhitelistCreateResponse>())

export const permissionsWhitelistUpdateContract = base
  .route({
    path: '/permissions/apps/{appId}/whitelist/{permId}',
    method: 'PATCH',
  })
  .input(type<{
    params: { appId: string, permId: string }
    body: { expires_at: string | null }
  }>())
  .output(type<WhitelistEntry>())

export const permissionsWhitelistDeleteContract = base
  .route({
    path: '/permissions/apps/{appId}/whitelist/{permId}',
    method: 'DELETE',
  })
  .input(type<{ params: { appId: string, permId: string } }>())
  .output(type<ResultResponse>())
