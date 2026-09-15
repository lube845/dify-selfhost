import type { WhitelistEntry } from '@/models/app-permission'
import type { Mock } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import {
  useGrantWhitelistUsers,
  useRevokeWhitelistUser,
  useWhitelist,
} from '@/service/use-permissions'
import WhitelistModal from '../whitelist-modal'

type WhitelistResponse = { data: WhitelistEntry[] }

let mockEntries: WhitelistEntry[] = []
let mockIsPending = false
const mockGrant = vi.fn()
const mockRevoke = vi.fn()
const mockToastError = vi.fn()
const mockToastWarning = vi.fn()
const originalConfirm = window.confirm

vi.mock('@/service/use-permissions', () => ({
  useWhitelist: vi.fn(),
  useGrantWhitelistUsers: vi.fn(),
  useRevokeWhitelistUser: vi.fn(),
}))

vi.mock('@langgenius/dify-ui/toast', () => ({
  toast: {
    error: (...args: unknown[]) => mockToastError(...args),
    success: vi.fn(),
    warning: (...args: unknown[]) => mockToastWarning(...args),
  },
}))

const APP_ID = 'app-1'

describe('WhitelistModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockEntries = []
    mockIsPending = false
    mockGrant.mockResolvedValue({ data: [], skipped: [] })
    mockRevoke.mockResolvedValue({ result: 'success' })
    window.confirm = vi.fn(() => true)
    ;(useWhitelist as Mock).mockImplementation(() => ({
      data: { data: mockEntries } as WhitelistResponse,
      isPending: mockIsPending,
    }))
    ;(useGrantWhitelistUsers as Mock).mockImplementation(() => ({
      mutateAsync: mockGrant,
    }))
    ;(useRevokeWhitelistUser as Mock).mockImplementation(() => ({
      mutateAsync: mockRevoke,
    }))
  })

  afterAll(() => {
    window.confirm = originalConfirm
  })

  // Rendering
  describe('Rendering', () => {
    it('should render the modal title with the app name', () => {
      render(<WhitelistModal appId={APP_ID} appName="My App" onClose={vi.fn()} />)
      expect(screen.getByText(/common\.permissions\.whitelistModal\.title/)).toBeInTheDocument()
    })

    it('should render the add user button in the header', () => {
      render(<WhitelistModal appId={APP_ID} appName="My App" onClose={vi.fn()} />)
      expect(screen.getByRole('button', { name: /common\.permissions\.whitelistModal\.addUser/ })).toBeInTheDocument()
    })

    it('should render the empty state when there are no entries', () => {
      render(<WhitelistModal appId={APP_ID} appName="My App" onClose={vi.fn()} />)
      expect(screen.getByText('common.permissions.whitelistModal.empty')).toBeInTheDocument()
    })

    it('should render the table column headers', () => {
      render(<WhitelistModal appId={APP_ID} appName="My App" onClose={vi.fn()} />)
      const table = screen.getByRole('table')
      const headers = within(table).getAllByRole('columnheader')
      expect(headers).toHaveLength(3)
      expect(within(table).getByText('common.permissions.whitelistModal.userId')).toBeInTheDocument()
      expect(within(table).getByText('common.permissions.whitelistModal.expiresAt')).toBeInTheDocument()
      expect(within(table).getByText('common.permissions.whitelistModal.actions')).toBeInTheDocument()
    })

    it('should render the filter empty state when filter has no matches', () => {
      mockEntries = [{ id: 'p1', app_id: APP_ID, user_id: 'a', expires_at: null, created_at: '', updated_at: '' }]
      render(<WhitelistModal appId={APP_ID} appName="My App" onClose={vi.fn()} />)
      const filterInput = screen.getByPlaceholderText('common.permissions.whitelistModal.sessionIdFilterPlaceholder')
      fireEvent.change(filterInput, { target: { value: 'zzz' } })
      expect(screen.getByText('common.permissions.whitelistModal.filterEmpty')).toBeInTheDocument()
    })
  })

  // Add user flow
  describe('Add user flow', () => {
    it('should open the add form when clicking add user and render the user id input', () => {
      render(<WhitelistModal appId={APP_ID} appName="My App" onClose={vi.fn()} />)
      fireEvent.click(screen.getByRole('button', { name: /common\.permissions\.whitelistModal\.addUser/ }))
      expect(screen.getByPlaceholderText('common.permissions.whitelistModal.userIdPlaceholder')).toBeInTheDocument()
      expect(screen.getByText('common.permissions.whitelistModal.cancel')).toBeInTheDocument()
      expect(screen.getByText('common.permissions.whitelistModal.save')).toBeInTheDocument()
    })

    it('should split comma-separated user ids and call grant', async () => {
      render(<WhitelistModal appId={APP_ID} appName="My App" onClose={vi.fn()} />)
      fireEvent.click(screen.getByRole('button', { name: /common\.permissions\.whitelistModal\.addUser/ }))

      const userIdInput = screen.getByPlaceholderText('common.permissions.whitelistModal.userIdPlaceholder')
      fireEvent.change(userIdInput, { target: { value: 'a, b,c' } })
      fireEvent.click(screen.getByText('common.permissions.whitelistModal.save'))

      await waitFor(() => {
        expect(mockGrant).toHaveBeenCalledWith({ userIds: ['a', 'b', 'c'], expiresAt: null })
      })
    })

    it('should pass the selected expiry date to grant', async () => {
      render(<WhitelistModal appId={APP_ID} appName="My App" onClose={vi.fn()} />)
      fireEvent.click(screen.getByRole('button', { name: /common\.permissions\.whitelistModal\.addUser/ }))

      const userIdInput = screen.getByPlaceholderText('common.permissions.whitelistModal.userIdPlaceholder')
      fireEvent.change(userIdInput, { target: { value: 'a' } })
      const dateInput = document.querySelector('input[type="date"]') as HTMLInputElement
      fireEvent.change(dateInput, { target: { value: '2030-07-15' } })
      fireEvent.click(screen.getByText('common.permissions.whitelistModal.save'))

      await waitFor(() => {
        expect(mockGrant).toHaveBeenCalledWith({ userIds: ['a'], expiresAt: '2030-07-15' })
      })
    })

    it('should not call grant when the user id is blank', async () => {
      render(<WhitelistModal appId={APP_ID} appName="My App" onClose={vi.fn()} />)
      fireEvent.click(screen.getByRole('button', { name: /common\.permissions\.whitelistModal\.addUser/ }))

      fireEvent.click(screen.getByText('common.permissions.whitelistModal.save'))

      await waitFor(() => {
        expect(mockGrant).not.toHaveBeenCalled()
        expect(mockToastError).toHaveBeenCalledWith('common.permissions.feedback.grantFailed')
      })
    })

    it('should warn when grant returns skipped entries', async () => {
      mockGrant.mockResolvedValueOnce({ data: [], skipped: ['dup-1', 'dup-2'] })
      render(<WhitelistModal appId={APP_ID} appName="My App" onClose={vi.fn()} />)
      fireEvent.click(screen.getByRole('button', { name: /common\.permissions\.whitelistModal\.addUser/ }))

      const userIdInput = screen.getByPlaceholderText('common.permissions.whitelistModal.userIdPlaceholder')
      fireEvent.change(userIdInput, { target: { value: 'dup-1' } })
      fireEvent.click(screen.getByText('common.permissions.whitelistModal.save'))

      await waitFor(() => {
        expect(mockToastWarning).toHaveBeenCalledWith(expect.stringContaining('common.permissions.feedback.grantPartial'))
      })
    })

    it('should close the add form and reset inputs when clicking cancel', () => {
      render(<WhitelistModal appId={APP_ID} appName="My App" onClose={vi.fn()} />)
      fireEvent.click(screen.getByRole('button', { name: /common\.permissions\.whitelistModal\.addUser/ }))
      const userIdInput = screen.getByPlaceholderText('common.permissions.whitelistModal.userIdPlaceholder')
      fireEvent.change(userIdInput, { target: { value: 'a' } })

      fireEvent.click(screen.getByText('common.permissions.whitelistModal.cancel'))
      expect(screen.queryByPlaceholderText('common.permissions.whitelistModal.userIdPlaceholder')).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: /common\.permissions\.whitelistModal\.addUser/ })).toBeInTheDocument()
    })

    it('should close the add form after a successful grant', async () => {
      render(<WhitelistModal appId={APP_ID} appName="My App" onClose={vi.fn()} />)
      fireEvent.click(screen.getByRole('button', { name: /common\.permissions\.whitelistModal\.addUser/ }))

      const userIdInput = screen.getByPlaceholderText('common.permissions.whitelistModal.userIdPlaceholder')
      fireEvent.change(userIdInput, { target: { value: 'a' } })
      fireEvent.click(screen.getByText('common.permissions.whitelistModal.save'))

      await waitFor(() => {
        expect(screen.queryByPlaceholderText('common.permissions.whitelistModal.userIdPlaceholder')).not.toBeInTheDocument()
      })
    })
  })

  // Revoke flow
  describe('Revoke flow', () => {
    it('should revoke a whitelist entry after confirm via the delete icon button', async () => {
      mockEntries = [{ id: 'p1', app_id: APP_ID, user_id: 'a', expires_at: null, created_at: '', updated_at: '' }]
      render(<WhitelistModal appId={APP_ID} appName="My App" onClose={vi.fn()} />)

      fireEvent.click(screen.getByRole('button', { name: 'common.permissions.whitelistModal.delete' }))
      await waitFor(() => {
        expect(mockRevoke).toHaveBeenCalledWith('p1')
      })
    })

    it('should not revoke if confirm is cancelled', () => {
      window.confirm = vi.fn(() => false)
      mockEntries = [{ id: 'p1', app_id: APP_ID, user_id: 'a', expires_at: null, created_at: '', updated_at: '' }]
      render(<WhitelistModal appId={APP_ID} appName="My App" onClose={vi.fn()} />)

      fireEvent.click(screen.getByRole('button', { name: 'common.permissions.whitelistModal.delete' }))
      expect(mockRevoke).not.toHaveBeenCalled()
    })

    it('should surface a toast error when revoke fails', async () => {
      mockRevoke.mockRejectedValueOnce(new Error('boom'))
      mockEntries = [{ id: 'p1', app_id: APP_ID, user_id: 'a', expires_at: null, created_at: '', updated_at: '' }]
      render(<WhitelistModal appId={APP_ID} appName="My App" onClose={vi.fn()} />)

      fireEvent.click(screen.getByRole('button', { name: 'common.permissions.whitelistModal.delete' }))
      await waitFor(() => {
        expect(mockToastError).toHaveBeenCalledWith('common.permissions.feedback.revokeFailed')
      })
    })
  })

  // Closing
  describe('Closing', () => {
    it('should call onClose when the close button is clicked', () => {
      const onClose = vi.fn()
      render(<WhitelistModal appId={APP_ID} appName="My App" onClose={onClose} />)
      fireEvent.click(screen.getByRole('button', { name: 'common.permissions.whitelistModal.close' }))
      expect(onClose).toHaveBeenCalled()
    })

    it('should call onClose when dialog requests close', () => {
      const onClose = vi.fn()
      render(<WhitelistModal appId={APP_ID} appName="My App" onClose={onClose} />)
      fireEvent.keyDown(document.body, { key: 'Escape' })
      expect(onClose).toHaveBeenCalled()
    })
  })
})