import type { AccessControlAccount, AccessControlGroup, AccessMode, Subject } from '@/models/access-control'
import type { App } from '@/types/app'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { systemFeaturesQueryOptions } from '@/service/system-features'
import { get, post } from './base'
import { getUserCanAccess } from './share'

const NAME_SPACE = 'access-control'

export const useAppWhiteListSubjects = (appId: string | undefined, enabled: boolean) => {
  return useQuery({
    queryKey: [NAME_SPACE, 'app-whitelist-subjects', appId],
    queryFn: () => get<{ groups: AccessControlGroup[], members: AccessControlAccount[] }>(`/enterprise/webapp/app/subjects?appId=${appId}`),
    enabled: !!appId && enabled,
    staleTime: 0,
    gcTime: 0,
  })
}

type SearchResults = {
  currPage: number
  totalPages: number
  subjects: Subject[]
  hasMore: boolean
}

export const useSearchForWhiteListCandidates = (query: { keyword?: string, groupId?: AccessControlGroup['id'], resultsPerPage?: number }, enabled: boolean) => {
  return useInfiniteQuery({
    queryKey: [NAME_SPACE, 'app-whitelist-candidates', query],
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams()
      Object.keys(query).forEach((key) => {
        const typedKey = key as keyof typeof query
        if (query[typedKey])
          params.append(key, `${query[typedKey]}`)
      })
      params.append('pageNumber', `${pageParam}`)
      return get<SearchResults>(`/enterprise/webapp/app/subject/search?${new URLSearchParams(params).toString()}`)
    },
    initialPageParam: 1,
    getNextPageParam: (lastPage) => {
      if (lastPage.hasMore)
        return lastPage.currPage + 1
      return undefined
    },
    gcTime: 0,
    staleTime: 0,
    enabled,
  })
}

type UpdateAccessModeParams = {
  appId: App['id']
  subjects?: Pick<Subject, 'subjectId' | 'subjectType'>[]
  accessMode: AccessMode
}

export const useUpdateAccessMode = () => {
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: [NAME_SPACE, 'update-access-mode'],
    mutationFn: (params: UpdateAccessModeParams) => {
      return post('/enterprise/webapp/app/access-mode', { body: params })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: [NAME_SPACE, 'app-whitelist-subjects'],
      })
    },
  })
}

export const useGetUserCanAccessApp = ({ appId, isInstalledApp = true, enabled, silent = false }: { appId?: string, isInstalledApp?: boolean, enabled?: boolean, silent?: boolean }) => {
  // useQuery (not useSuspenseQuery) to keep this service hook's call contract
  // unchanged from the zustand era: callers should not need a Suspense boundary.
  // First-fetch undefined is bridged via `?? false` so the inner queryKey is stable.
  const { data: systemFeatures } = useQuery(systemFeaturesQueryOptions())
  const webappAuthEnabled = systemFeatures?.webapp_auth.enabled ?? false

  // Public webapp pages (`/chat/<code>`) are ALWAYS probed, even when the
  // system-level `webapp_auth` feature is off. The per-app anonymous policy
  // (`App.access_policy` + `App.allow_anonymous`) is a community-edition
  // concept enforced by `GET /api/webapp/permission`, which has no dependency
  // on the enterprise webapp-auth feature — gating the probe on
  // `webappAuthEnabled` would silently skip it outside enterprise deployments
  // and the visitor would never receive `auth_required`.
  //
  // Installed apps keep the original gate: their check goes through
  // `GET /console/api/enterprise/webapp/permission`, which only exists when
  // the enterprise feature is enabled — probing it unconditionally would
  // surface a spurious error page on community editions.
  const shouldProbe = !isInstalledApp || webappAuthEnabled

  return useQuery({
    queryKey: [NAME_SPACE, 'user-can-access-app', appId, webappAuthEnabled, isInstalledApp],
    queryFn: () => {
      if (shouldProbe)
        return getUserCanAccess(appId!, isInstalledApp, silent ? { silent: true } : undefined)
      // System webapp_auth feature is off AND this is an installed app —
      // every visitor is implicitly allowed. Keep the response shape in sync
      // with ``AccessCheckResponse`` so consumers can read ``.reason``
      // unconditionally.
      return { result: true, reason: 'allowed' as const }
    },
    enabled: enabled !== undefined ? enabled : !!appId,
    staleTime: 0,
    gcTime: 0,
  })
}
