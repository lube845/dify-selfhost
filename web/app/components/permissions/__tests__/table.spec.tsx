import type { AppAccessPolicy } from '@/models/app-permission'
import type { Mock } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { usePermissionsAppList, useUpdateAppAccessPolicy, useUpdateAppAllowAnonymous } from '@/service/use-permissions'
import PermissionsTable from '../table'

type AppItem = { id: string, name: string, access_policy: AppAccessPolicy, allow_anonymous: boolean }

let mockApps: AppItem[] = []
let mockIsPending = false
let mockIsError = false
const mockUpdatePolicy = vi.fn()
const mockUpdateAllowAnonymous = vi.fn()
const mockToastError = vi.fn()

vi.mock('@/service/use-permissions', () => ({
  usePermissionsAppList: vi.fn(),
  useUpdateAppAccessPolicy: vi.fn(),
  useUpdateAppAllowAnonymous: vi.fn(),
}))

vi.mock('@langgenius/dify-ui/toast', () => ({
  toast: {
    error: (...args: unknown[]) => mockToastError(...args),
    success: vi.fn(),
    warning: vi.fn(),
  },
}))

vi.mock('../whitelist-modal', () => ({
  default: ({ appId, appName, onClose }: { appId: string, appName: string, onClose: () => void }) => (
    <div data-testid="whitelist-modal" data-app-id={appId} data-app-name={appName}>
      <button data-testid="close-modal" onClick={onClose}>close</button>
    </div>
  ),
}))

describe('PermissionsTable', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApps = []
    mockIsPending = false
    mockIsError = false
    mockUpdatePolicy.mockResolvedValue(undefined)
    mockUpdateAllowAnonymous.mockResolvedValue(undefined)
    ;(usePermissionsAppList as Mock).mockImplementation(() => ({
      data: { data: mockApps },
      isPending: mockIsPending,
      isError: mockIsError,
    }))
    ;(useUpdateAppAccessPolicy as Mock).mockImplementation(() => ({
      mutateAsync: mockUpdatePolicy,
    }))
    ;(useUpdateAppAllowAnonymous as Mock).mockImplementation(() => ({
      mutateAsync: mockUpdateAllowAnonymous,
    }))
  })

  it('should render loading state', () => {
    mockIsPending = true
    render(<PermissionsTable />)
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('should render empty state when no apps', () => {
    render(<PermissionsTable />)
    expect(screen.getByText('common.permissions.whitelistModal.empty')).toBeInTheDocument()
  })

  it('should render a row per app with correct policy label', () => {
    mockApps = [
      { id: 'app-1', name: 'App One', access_policy: 'allow_all', allow_anonymous: true },
      { id: 'app-2', name: 'App Two', access_policy: 'deny_all_explicit', allow_anonymous: true },
    ]
    render(<PermissionsTable />)

    expect(screen.getByText('App One')).toBeInTheDocument()
    expect(screen.getByText('App Two')).toBeInTheDocument()
    expect(screen.getByText('common.permissions.columns.appName')).toBeInTheDocument()
    expect(screen.getByText('common.permissions.columns.appId')).toBeInTheDocument()
    expect(screen.getByText('common.permissions.columns.defaultAccess')).toBeInTheDocument()
    expect(screen.getByText('common.permissions.columns.allowAnonymous')).toBeInTheDocument()
    expect(screen.getAllByText('common.permissions.defaultAccess.allowAll').length).toBeGreaterThan(0)
    expect(screen.getAllByText('common.permissions.defaultAccess.denyAllExplicit').length).toBeGreaterThan(0)
  })

  it('should call updatePolicy with the toggled policy when the switch is clicked', async () => {
    mockApps = [
      { id: 'app-1', name: 'App One', access_policy: 'allow_all', allow_anonymous: true },
    ]
    render(<PermissionsTable />)

    const switches = screen.getAllByRole('switch')
    expect(switches[0]).toBeDefined()
    fireEvent.click(switches[0]!)

    await waitFor(() => {
      expect(mockUpdatePolicy).toHaveBeenCalledWith({ appId: 'app-1', accessPolicy: 'deny_all_explicit' })
    })
    expect(mockUpdateAllowAnonymous).not.toHaveBeenCalled()
  })

  it('should surface a toast error when policy update fails', async () => {
    mockApps = [
      { id: 'app-1', name: 'App One', access_policy: 'allow_all', allow_anonymous: true },
    ]
    mockUpdatePolicy.mockRejectedValueOnce(new Error('boom'))
    render(<PermissionsTable />)

    const switches = screen.getAllByRole('switch')
    expect(switches[0]).toBeDefined()
    fireEvent.click(switches[0]!)

    await waitFor(() => {
      expect(mockToastError).toHaveBeenCalledWith('common.permissions.feedback.toggleFailed')
    })
  })

  it('should render the anonymous switch off and disabled when default access is off', () => {
    mockApps = [
      { id: 'app-1', name: 'App One', access_policy: 'deny_all_explicit', allow_anonymous: true },
    ]
    render(<PermissionsTable />)

    const anonSwitch = screen.getAllByRole('switch')[1]
    // `<Switch>` renders a `<span role="switch">`, not a native control, so
    // Base UI marks the disabled state with `aria-disabled` + `tabindex="-1"`
    // (it never sets the native `disabled` attribute). `toBeDisabled()` only
    // holds for real form controls — the repo convention for switches and
    // checkboxes is `aria-disabled` (see
    // `app/components/workflow/nodes/http/__tests__/panel.spec.tsx`).
    expect(anonSwitch).toHaveAttribute('aria-disabled', 'true')
    expect(anonSwitch).toHaveAttribute('aria-checked', 'false')
    expect(screen.getAllByText('common.permissions.allowAnonymous.required').length).toBeGreaterThan(0)
  })

  it('should not fire the anonymous update when the switch is disabled', async () => {
    mockApps = [
      { id: 'app-1', name: 'App One', access_policy: 'deny_all_explicit', allow_anonymous: true },
    ]
    render(<PermissionsTable />)

    fireEvent.click(screen.getAllByRole('switch')[1]!)
    await waitFor(() => {
      expect(mockUpdateAllowAnonymous).not.toHaveBeenCalled()
    })
    expect(mockUpdatePolicy).not.toHaveBeenCalled()
  })

  it('should toggle allow_anonymous without touching the access policy', async () => {
    mockApps = [
      { id: 'app-1', name: 'App One', access_policy: 'allow_all', allow_anonymous: true },
    ]
    render(<PermissionsTable />)

    const anonSwitch = screen.getAllByRole('switch')[1]
    expect(anonSwitch).not.toHaveAttribute('aria-disabled', 'true')
    fireEvent.click(anonSwitch!)

    await waitFor(() => {
      expect(mockUpdateAllowAnonymous).toHaveBeenCalledWith({ appId: 'app-1', allowAnonymous: false })
    })
    expect(mockUpdatePolicy).not.toHaveBeenCalled()
  })

  it('should surface a toast error when the anonymous update fails', async () => {
    mockApps = [
      { id: 'app-1', name: 'App One', access_policy: 'allow_all', allow_anonymous: true },
    ]
    mockUpdateAllowAnonymous.mockRejectedValueOnce(new Error('boom'))
    render(<PermissionsTable />)

    fireEvent.click(screen.getAllByRole('switch')[1]!)

    await waitFor(() => {
      expect(mockToastError).toHaveBeenCalledWith('common.permissions.feedback.toggleAnonymousFailed')
    })
  })

  it('should open the whitelist modal when the whitelist button is clicked', () => {
    mockApps = [
      { id: 'app-1', name: 'App One', access_policy: 'allow_all', allow_anonymous: true },
    ]
    render(<PermissionsTable />)

    fireEvent.click(screen.getByText('common.permissions.whitelist'))
    expect(screen.getByTestId('whitelist-modal')).toHaveAttribute('data-app-id', 'app-1')
    expect(screen.getByTestId('whitelist-modal')).toHaveAttribute('data-app-name', 'App One')
  })

  it('should close the whitelist modal when onClose is triggered', () => {
    mockApps = [
      { id: 'app-1', name: 'App One', access_policy: 'allow_all', allow_anonymous: true },
    ]
    render(<PermissionsTable />)

    fireEvent.click(screen.getByText('common.permissions.whitelist'))
    fireEvent.click(screen.getByTestId('close-modal'))
    expect(screen.queryByTestId('whitelist-modal')).not.toBeInTheDocument()
  })
})
