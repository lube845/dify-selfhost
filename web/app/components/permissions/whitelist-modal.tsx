'use client'

import type { WhitelistEntry } from '@/models/app-permission'
import { cn } from '@langgenius/dify-ui/cn'
import { Button } from '@langgenius/dify-ui/button'
import { Dialog, DialogContent, DialogCloseButton } from '@langgenius/dify-ui/dialog'
import { toast } from '@langgenius/dify-ui/toast'
import { Tooltip, TooltipContent, TooltipTrigger } from '@langgenius/dify-ui/tooltip'
import { RiAddLine, RiDeleteBinLine } from '@remixicon/react'
import * as React from 'react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Input from '@/app/components/base/input'
import Loading from '@/app/components/base/loading'
import {
  useGrantWhitelistUsers,
  useRevokeWhitelistUser,
  useWhitelist,
} from '@/service/use-permissions'

type WhitelistModalProps = {
  appId: string
  appName: string
  onClose: () => void
}

const parseUserIds = (raw: string): string[] => {
  return raw
    .split(/[,\n]/)
    .map(id => id.trim())
    .filter(Boolean)
}

const WhitelistModal = ({
  appId,
  appName,
  onClose,
}: WhitelistModalProps) => {
  const { t } = useTranslation()
  const { data, isPending, isError } = useWhitelist(appId)
  const entries: WhitelistEntry[] = data?.data ?? []
  const { mutateAsync: grant } = useGrantWhitelistUsers(appId)
  const { mutateAsync: revoke } = useRevokeWhitelistUser(appId)

  useEffect(() => {
    if (isError)
      toast.error(t('permissions.feedback.loadWhitelistFailed', { ns: 'common' }))
  }, [isError, t])

  const [isAdding, setIsAdding] = useState(false)
  const [userIdsRaw, setUserIdsRaw] = useState('')
  const [expiresAt, setExpiresAt] = useState('')
  const [filter, setFilter] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)

  const filteredEntries = useMemo(() => {
    if (!filter.trim())
      return entries
    const needle = filter.trim().toLowerCase()
    return entries.filter(entry => entry.user_id.toLowerCase().includes(needle))
  }, [entries, filter])

  const openAddForm = useCallback(() => {
    setUserIdsRaw('')
    setExpiresAt('')
    setIsAdding(true)
  }, [])

  const cancelAddForm = useCallback(() => {
    setUserIdsRaw('')
    setExpiresAt('')
    setIsAdding(false)
  }, [])

  const handleAdd = useCallback(async () => {
    const userIds = parseUserIds(userIdsRaw)
    if (userIds.length === 0) {
      toast.error(t('permissions.feedback.grantFailed', { ns: 'common' }))
      return
    }
    setIsSubmitting(true)
    try {
      const result = await grant({
        userIds,
        expiresAt: expiresAt || null,
      })
      setUserIdsRaw('')
      setExpiresAt('')
      setIsAdding(false)
      if (result.skipped.length > 0) {
        toast.warning(
          t('permissions.feedback.grantPartial', { ns: 'common', skipped: result.skipped.length }),
        )
      }
    }
    catch {
      toast.error(t('permissions.feedback.grantFailed', { ns: 'common' }))
    }
    finally {
      setIsSubmitting(false)
    }
  }, [userIdsRaw, expiresAt, grant, t])

  const handleRevoke = useCallback(async (permId: string) => {
    try {
      await revoke(permId)
    }
    catch {
      toast.error(t('permissions.feedback.revokeFailed', { ns: 'common' }))
    }
  }, [revoke, t])

  return (
    <Dialog open onOpenChange={open => !open && onClose()}>
      <DialogContent className="w-[640px] max-w-none p-6">
        <div className="mb-4 flex items-center justify-between gap-4 pr-8">
          <h2 className="title-lg-semi-bold text-text-primary">
            {t('permissions.whitelistModal.title', { ns: 'common', appName })}
          </h2>
          <Button variant="primary" size="small" onClick={openAddForm}>
            <RiAddLine className="mr-1 h-4 w-4" aria-hidden="true" />
            {t('permissions.whitelistModal.addUser', { ns: 'common' })}
          </Button>
        </div>

        {isAdding && (
          <div className="mb-4 flex flex-col gap-3 rounded-lg bg-background-section p-4">
            <div className="flex flex-col gap-1">
              <label
                htmlFor="whitelist-user-id"
                className="system-sm-medium text-text-secondary"
              >
                {t('permissions.whitelistModal.userId', { ns: 'common' })}
              </label>
              <Input
                id="whitelist-user-id"
                value={userIdsRaw}
                onChange={e => setUserIdsRaw(e.target.value)}
                placeholder={t('permissions.whitelistModal.userIdPlaceholder', { ns: 'common' })}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label
                htmlFor="whitelist-expires-at"
                className="system-sm-medium text-text-secondary"
              >
                {t('permissions.whitelistModal.expiresAt', { ns: 'common' })}
              </label>
              <Input
                id="whitelist-expires-at"
                type="date"
                value={expiresAt}
                onChange={e => setExpiresAt(e.target.value)}
                placeholder={t('permissions.whitelistModal.expiresAtPlaceholder', { ns: 'common' })}
              />
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <Button variant="secondary" onClick={cancelAddForm} disabled={isSubmitting}>
                {t('permissions.whitelistModal.cancel', { ns: 'common' })}
              </Button>
              <Button variant="primary" onClick={handleAdd} loading={isSubmitting} disabled={isSubmitting}>
                {t('permissions.whitelistModal.save', { ns: 'common' })}
              </Button>
            </div>
          </div>
        )}

        <div className="mb-2 flex items-center gap-2">
          <Input
            value={filter}
            onChange={e => setFilter(e.target.value)}
            placeholder={t('permissions.whitelistModal.sessionIdFilterPlaceholder', { ns: 'common' })}
            showClearIcon
            onClear={() => setFilter('')}
            wrapperClassName="flex-1"
          />
          <span className="shrink-0 system-xs-regular text-text-tertiary">
            {t('permissions.whitelistModal.filterCount', {
              ns: 'common',
              shown: filteredEntries.length,
              total: entries.length,
            })}
          </span>
        </div>

        <div className="overflow-hidden rounded-lg border border-divider-regular">
          {isPending
            ? (
                <div className="flex h-32 items-center justify-center">
                  <Loading type="area" />
                </div>
              )
            : (
                <table className="w-full table-fixed">
                  <thead>
                    <tr className="border-b border-divider-regular bg-components-table-row-bg-hover">
                      <th className="w-[40%] px-4 py-2 text-left system-xs-medium-uppercase text-text-tertiary">
                        {t('permissions.whitelistModal.userId', { ns: 'common' })}
                      </th>
                      <th className="w-[40%] px-4 py-2 text-left system-xs-medium-uppercase text-text-tertiary">
                        {t('permissions.whitelistModal.expiresAt', { ns: 'common' })}
                      </th>
                      <th className="w-[20%] px-4 py-2 text-right system-xs-medium-uppercase text-text-tertiary">
                        {t('permissions.whitelistModal.actions', { ns: 'common' })}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredEntries.length === 0
                      ? (
                          <tr>
                            <td
                              colSpan={3}
                              className="h-32 px-4 py-3 text-center system-sm-regular text-text-tertiary"
                            >
                              {filter.trim()
                                ? t('permissions.whitelistModal.filterEmpty', { ns: 'common' })
                                : t('permissions.whitelistModal.empty', { ns: 'common' })}
                            </td>
                          </tr>
                        )
                      : filteredEntries.map(entry => (
                          <tr
                            key={entry.id}
                            className={cn(
                              'border-b border-divider-subtle last:border-b-0',
                              'hover:bg-state-base-hover',
                            )}
                          >
                            <td className="px-4 py-3 font-mono system-sm-regular text-text-primary">
                              {entry.user_id}
                            </td>
                            <td className="px-4 py-3 system-sm-regular text-text-secondary">
                              {entry.expires_at
                                ? entry.expires_at
                                : t('permissions.whitelistModal.never', { ns: 'common' })}
                            </td>
                            <td className="px-4 py-3 text-right">
                              <Tooltip>
                                <TooltipTrigger
                                  render={(
                                    <button
                                      type="button"
                                      aria-label={t('permissions.whitelistModal.delete', { ns: 'common' })}
                                      className="inline-flex h-7 w-7 cursor-pointer items-center justify-center rounded-md text-text-tertiary transition-colors hover:bg-state-base-hover hover:text-text-secondary focus-visible:outline-hidden focus-visible:ring-1 focus-visible:ring-components-input-border-hover"
                                      onClick={() => {
                                        if (window.confirm(t('permissions.whitelistModal.confirmDelete', { ns: 'common' })))
                                          handleRevoke(entry.id)
                                      }}
                                    />
                                  )}
                                >
                                  <RiDeleteBinLine className="h-4 w-4" aria-hidden="true" />
                                </TooltipTrigger>
                                <TooltipContent>{t('permissions.whitelistModal.delete', { ns: 'common' })}</TooltipContent>
                              </Tooltip>
                            </td>
                          </tr>
                        ))}
                  </tbody>
                </table>
              )}
        </div>

        <DialogCloseButton
          onClick={onClose}
          aria-label={t('permissions.whitelistModal.close', { ns: 'common' })}
        />
      </DialogContent>
    </Dialog>
  )
}

export default React.memo(WhitelistModal)