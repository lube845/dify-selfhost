'use client'

import type { AppAccessPolicy } from '@/models/app-permission'
import { Button } from '@langgenius/dify-ui/button'
import { Switch } from '@langgenius/dify-ui/switch'
import { toast } from '@langgenius/dify-ui/toast'
import { Tooltip, TooltipContent, TooltipTrigger } from '@langgenius/dify-ui/tooltip'
import * as React from 'react'
import { useCallback, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Loading from '@/app/components/base/loading'
import { usePermissionsAppList, useUpdateAppAccessPolicy, useUpdateAppAllowAnonymous } from '@/service/use-permissions'
import WhitelistModal from './whitelist-modal'

type SelectedApp = {
  id: string
  name: string
}

const PermissionsTable = () => {
  const { t } = useTranslation()
  const { data, isPending, isError } = usePermissionsAppList()
  const { mutateAsync: updatePolicy } = useUpdateAppAccessPolicy()
  const { mutateAsync: updateAllowAnonymous } = useUpdateAppAllowAnonymous()

  const apps = data?.data ?? []
  const [selectedApp, setSelectedApp] = useState<SelectedApp | null>(null)

  const handleToggle = useCallback(async (appId: string, current: AppAccessPolicy) => {
    const next: AppAccessPolicy = current === 'allow_all' ? 'deny_all_explicit' : 'allow_all'
    try {
      await updatePolicy({ appId, accessPolicy: next })
    }
    catch {
      toast.error(t('permissions.feedback.toggleFailed', { ns: 'common' }))
    }
  }, [t, updatePolicy])

  const handleToggleAnonymous = useCallback(async (appId: string, current: boolean) => {
    try {
      await updateAllowAnonymous({ appId, allowAnonymous: !current })
    }
    catch {
      toast.error(t('permissions.feedback.toggleAnonymousFailed', { ns: 'common' }))
    }
  }, [t, updateAllowAnonymous])

  const columns = useMemo(() => ([
    { key: 'name', label: t('permissions.columns.appName', { ns: 'common' }) },
    { key: 'appId', label: t('permissions.columns.appId', { ns: 'common' }) },
    { key: 'policy', label: t('permissions.columns.defaultAccess', { ns: 'common' }) },
    { key: 'anonymous', label: t('permissions.columns.allowAnonymous', { ns: 'common' }) },
    { key: 'whitelist', label: t('permissions.columns.whitelist', { ns: 'common' }) },
  ]), [t])

  if (isPending) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loading type="area" />
      </div>
    )
  }

  if (isError) {
    toast.error(t('permissions.feedback.loadAppsFailed', { ns: 'common' }))
  }

  if (apps.length === 0) {
    return (
      <div className="flex h-64 items-center justify-center rounded-xl border border-divider-regular bg-components-panel-bg text-text-tertiary system-md-regular">
        {t('permissions.whitelistModal.empty', { ns: 'common' })}
      </div>
    )
  }

  return (
    <>
      <div className="overflow-hidden rounded-xl border border-divider-regular bg-components-panel-bg">
        <table className="w-full table-fixed">
          <thead>
            <tr className="border-b border-divider-regular bg-components-table-row-bg-hover">
              {columns.map(col => (
                <th
                  key={col.key}
                  className="px-6 py-3 text-left system-xs-medium-uppercase text-text-tertiary"
                >
                  {col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {apps.map(app => {
              const isAllowAll = app.access_policy === 'allow_all'
              // Second level of the policy: anonymous access is only reachable
              // while default access is on. Turning default access off makes
              // every visitor sign in, so the switch is shown as off and
              // disabled instead of pretending the stored value still applies.
              const isAnonymousAllowed = isAllowAll && app.allow_anonymous
              return (
                <tr
                  key={app.id}
                  className="border-b border-divider-subtle last:border-b-0 hover:bg-state-base-hover"
                >
                  <td className="px-6 py-4 system-md-regular text-text-primary">
                    {app.name}
                  </td>
                  <td className="px-6 py-4">
                    <Tooltip>
                      <TooltipTrigger
                        render={<span className="block truncate font-mono system-sm-regular text-text-secondary" />}
                      >
                        {app.id}
                      </TooltipTrigger>
                      <TooltipContent>{app.id}</TooltipContent>
                    </Tooltip>
                  </td>
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-2">
                      <Switch
                        checked={isAllowAll}
                        onCheckedChange={() => handleToggle(app.id, app.access_policy)}
                      />
                      <span className="system-sm-regular text-text-secondary">
                        {isAllowAll
                          ? t('permissions.defaultAccess.allowAll', { ns: 'common' })
                          : t('permissions.defaultAccess.denyAllExplicit', { ns: 'common' })}
                      </span>
                    </div>
                  </td>
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-2">
                      <Switch
                        checked={isAnonymousAllowed}
                        disabled={!isAllowAll}
                        onCheckedChange={() => handleToggleAnonymous(app.id, app.allow_anonymous)}
                      />
                      {isAllowAll
                        ? (
                            <span className="system-sm-regular text-text-secondary">
                              {isAnonymousAllowed
                                ? t('permissions.allowAnonymous.allowed', { ns: 'common' })
                                : t('permissions.allowAnonymous.required', { ns: 'common' })}
                            </span>
                          )
                        : (
                            <Tooltip>
                              <TooltipTrigger
                                render={<span className="cursor-help system-sm-regular text-text-tertiary" />}
                              >
                                {t('permissions.allowAnonymous.required', { ns: 'common' })}
                              </TooltipTrigger>
                              <TooltipContent>
                                {t('permissions.allowAnonymous.requiresDefaultAccess', { ns: 'common' })}
                              </TooltipContent>
                            </Tooltip>
                          )}
                    </div>
                  </td>
                  <td className="px-6 py-4">
                    <Button
                      variant="secondary"
                      size="small"
                      onClick={() => setSelectedApp({ id: app.id, name: app.name })}
                    >
                      {t('permissions.whitelist', { ns: 'common' })}
                    </Button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {selectedApp && (
        <WhitelistModal
          appId={selectedApp.id}
          appName={selectedApp.name}
          onClose={() => setSelectedApp(null)}
        />
      )}
    </>
  )
}

export default React.memo(PermissionsTable)
