import type { AppAccessPolicy, WhitelistEntry } from '@/models/app-permission'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { consoleQuery } from './client'
import {
  fetchPermissionsApps,
  fetchWhitelist,
  grantWhitelistUsers,
  revokeWhitelistUser,
  updateAppAccessPolicy,
  updateAppAllowAnonymous,
  updateWhitelistExpiry,
} from './permissions'

type WhitelistResponse = { data: WhitelistEntry[] }
type WhitelistInput = { params: { appId: string } }

const appsInput = {}

export const usePermissionsAppList = () => {
  return useQuery({
    queryKey: consoleQuery.permissions.apps.queryKey({ input: appsInput }),
    queryFn: () => fetchPermissionsApps(),
  })
}

export const useUpdateAppAccessPolicy = () => {
  const client = useQueryClient()
  return useMutation({
    mutationKey: consoleQuery.permissions.appUpdate.mutationKey(),
    mutationFn: ({ appId, accessPolicy }: { appId: string, accessPolicy: AppAccessPolicy }) =>
      updateAppAccessPolicy(appId, accessPolicy),
    onSuccess: () => {
      client.invalidateQueries({
        queryKey: consoleQuery.permissions.apps.queryKey({ input: appsInput }),
      })
    },
  })
}

/**
 * Toggle whether anonymous (not signed in) visitors may chat on an app.
 * Independent from {@link useUpdateAppAccessPolicy}: the backend accepts each
 * field on its own so flipping one switch never rewrites the other.
 */
export const useUpdateAppAllowAnonymous = () => {
  const client = useQueryClient()
  return useMutation({
    mutationKey: consoleQuery.permissions.appUpdate.mutationKey(),
    mutationFn: ({ appId, allowAnonymous }: { appId: string, allowAnonymous: boolean }) =>
      updateAppAllowAnonymous(appId, allowAnonymous),
    onSuccess: () => {
      client.invalidateQueries({
        queryKey: consoleQuery.permissions.apps.queryKey({ input: appsInput }),
      })
    },
  })
}

const whitelistInput = (appId: string): WhitelistInput => ({ params: { appId } })

export const useWhitelist = (appId: string | null) => {
  const input = whitelistInput(appId ?? '')
  const installedAppId = input.params.appId
  return useQuery<WhitelistResponse>({
    queryKey: [...consoleQuery.permissions.whitelistList.queryKey({ input }), installedAppId],
    queryFn: () => {
      if (!installedAppId)
        return Promise.reject(new Error('App ID is required to load whitelist'))
      return fetchWhitelist(installedAppId) as Promise<WhitelistResponse>
    },
    enabled: !!installedAppId,
  })
}

export const useGrantWhitelistUsers = (appId: string) => {
  const client = useQueryClient()
  const input = whitelistInput(appId)
  return useMutation({
    mutationKey: consoleQuery.permissions.whitelistCreate.mutationKey(),
    mutationFn: ({ userIds, expiresAt }: { userIds: string[], expiresAt: string | null }) =>
      grantWhitelistUsers(appId, userIds, expiresAt),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: consoleQuery.permissions.whitelistList.queryKey({ input }) })
    },
  })
}

export const useUpdateWhitelistExpiry = (appId: string) => {
  const client = useQueryClient()
  const input = whitelistInput(appId)
  return useMutation({
    mutationKey: consoleQuery.permissions.whitelistUpdate.mutationKey(),
    mutationFn: ({ permId, expiresAt }: { permId: string, expiresAt: string | null }) =>
      updateWhitelistExpiry(appId, permId, expiresAt),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: consoleQuery.permissions.whitelistList.queryKey({ input }) })
    },
  })
}

export const useRevokeWhitelistUser = (appId: string) => {
  const client = useQueryClient()
  const input = whitelistInput(appId)
  return useMutation({
    mutationKey: consoleQuery.permissions.whitelistDelete.mutationKey(),
    mutationFn: (permId: string) => revokeWhitelistUser(appId, permId),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: consoleQuery.permissions.whitelistList.queryKey({ input }) })
    },
  })
}
