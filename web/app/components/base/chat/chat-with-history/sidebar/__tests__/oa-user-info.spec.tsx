import type { OASession } from '@/service/oa-session'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useChatWithHistoryContext } from '@/app/components/base/chat/chat-with-history/context'
import { useWebAppStore } from '@/context/web-app-context'
import { useOASession } from '@/service/oa-session'
import { webAppLogout } from '@/service/webapp-auth'
import OAUserInfo from '../oa-user-info'

const mockReplace = vi.fn()

// `@langgenius/dify-ui/dropdown-menu` is a Base UI wrapper that portals its
// content; the shared mock renders the trigger inline and the content only
// while open, which is what these assertions rely on.
vi.mock('@langgenius/dify-ui/dropdown-menu', () => import('@/__mocks__/base-ui-dropdown-menu'))

vi.mock('@/next/navigation', () => ({
  useRouter: () => ({ replace: mockReplace, push: vi.fn() }),
  // `context/web-app-context` (pulled in through the zustand store) imports
  // these two as well, so the mock mirrors the fuller navigation surface the
  // other chat-with-history specs provide.
  usePathname: () => '/chat/app-code-1',
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock('@/service/oa-session', () => ({
  useOASession: vi.fn(),
}))

vi.mock('@/service/webapp-auth', () => ({
  webAppLogout: vi.fn(),
}))

vi.mock('@/app/components/base/chat/chat-with-history/context', () => ({
  useChatWithHistoryContext: vi.fn(),
}))

type OASessionQuery = ReturnType<typeof useOASession>
type ChatWithHistoryContextValue = ReturnType<typeof useChatWithHistoryContext>

/** Roughly 2 hours out, so the (8-hour) expiry timer is armed but never fires. */
const FUTURE_EXPIRY = new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString()

const signedInSession: OASession = {
  workcode: '10086',
  name: 'Zhang San',
  department: 'Engineering',
  expires_at: FUTURE_EXPIRY,
  session_expire_hours: 8,
}

const mockSession = (session: OASession | null) => {
  vi.mocked(useOASession).mockReturnValue({ data: session } as unknown as OASessionQuery)
}

const mockInstalledApp = (isInstalledApp: boolean) => {
  vi.mocked(useChatWithHistoryContext).mockReturnValue({
    isInstalledApp,
  } as unknown as ChatWithHistoryContextValue)
}

const renderWithSession = (
  session: OASession | null,
  { shareCode = 'app-code-1', isInstalledApp = false } = {},
) => {
  mockSession(session)
  mockInstalledApp(isInstalledApp)
  useWebAppStore.setState({ shareCode })
  return render(<OAUserInfo />)
}

describe('OAUserInfo', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useWebAppStore.setState({ shareCode: 'app-code-1' })
    mockInstalledApp(false)
  })

  describe('Anonymous visitors', () => {
    it('should render nothing when there is no OA session', () => {
      // Anonymous visitors of an app with `allow_anonymous` never sign in, so
      // they must not see an identity block nor a sign-out button.
      const { container } = renderWithSession(null)

      expect(container).toBeEmptyDOMElement()
    })

    it('should not render a sign-out button when there is no OA session', () => {
      renderWithSession(null)

      expect(screen.queryByRole('button', { name: 'common.oaUser.logout' })).not.toBeInTheDocument()
    })
  })

  describe('Signed-in visitors', () => {
    it('should show the name and the workcode', () => {
      renderWithSession(signedInSession)

      expect(screen.getByText('Zhang San')).toBeInTheDocument()
      expect(screen.getByText('10086')).toBeInTheDocument()
    })

    it('should fall back to the workcode when the OA record has no name', () => {
      renderWithSession({ ...signedInSession, name: '' })

      expect(screen.getAllByText('10086').length).toBeGreaterThan(0)
    })

    it('should expose the sign-out button', () => {
      renderWithSession(signedInSession)

      expect(screen.getByRole('button', { name: 'common.oaUser.logout' })).toBeInTheDocument()
    })

    it('should list the sign-in details when the info area is opened', async () => {
      const user = userEvent.setup()
      renderWithSession(signedInSession)

      await user.click(screen.getByRole('button', { name: 'common.oaUser.viewProfile' }))

      expect(await screen.findByTestId('dropdown-menu-content')).toBeInTheDocument()
      expect(screen.getByText('common.oaUser.name')).toBeInTheDocument()
      expect(screen.getByText('common.oaUser.workcode')).toBeInTheDocument()
      expect(screen.getByText('common.oaUser.department')).toBeInTheDocument()
      expect(screen.getByText('common.oaUser.sessionExpiresAt')).toBeInTheDocument()
      expect(screen.getByText('Engineering')).toBeInTheDocument()
    })

    it('should show a placeholder for a missing department', async () => {
      const user = userEvent.setup()
      renderWithSession({ ...signedInSession, department: '' })

      await user.click(screen.getByRole('button', { name: 'common.oaUser.viewProfile' }))

      expect(await screen.findByTestId('dropdown-menu-content')).toBeInTheDocument()
      expect(screen.getByText('—')).toBeInTheDocument()
    })
  })

  describe('Sign-out', () => {
    // The explicit sign-out click lands on the standalone signed-out
    // confirmation page (account name URL-encoded in `?account=`) rather
    // than straight back on the login form — see
    // `web/app/webapp-logged-out/page.tsx`. The proactive 8h expiry
    // timer further down keeps the old behaviour; only the click path
    // is affected here.
    it('should clear the session and route to the signed-out page', async () => {
      const user = userEvent.setup()
      renderWithSession(signedInSession)

      await user.click(screen.getByRole('button', { name: 'common.oaUser.logout' }))

      await waitFor(() => expect(webAppLogout).toHaveBeenCalledWith('app-code-1'))
      expect(mockReplace).toHaveBeenCalledWith('/webapp-logged-out?account=Zhang%20San')
    })

    it('should sign out from the dropdown item as well', async () => {
      const user = userEvent.setup()
      renderWithSession(signedInSession)

      await user.click(screen.getByRole('button', { name: 'common.oaUser.viewProfile' }))
      await user.click(await screen.findByRole('menuitem'))

      await waitFor(() => expect(webAppLogout).toHaveBeenCalledWith('app-code-1'))
      expect(mockReplace).toHaveBeenCalledWith('/webapp-logged-out?account=Zhang%20San')
    })

    it('should still reach the signed-out page when the backend logout fails', async () => {
      const user = userEvent.setup()
      vi.mocked(webAppLogout).mockRejectedValueOnce(new Error('offline'))
      renderWithSession(signedInSession)

      await user.click(screen.getByRole('button', { name: 'common.oaUser.logout' }))

      await waitFor(() => expect(mockReplace).toHaveBeenCalledWith('/webapp-logged-out?account=Zhang%20San'))
    })
  })

  describe('Session window', () => {
    it('should return to the login page once the session has expired', () => {
      renderWithSession({ ...signedInSession, expires_at: '2020-01-01T00:00:00.000Z' })

      expect(mockReplace).toHaveBeenCalledWith('/oa-login')
    })

    it('should stay put while the session is still valid', () => {
      renderWithSession(signedInSession)

      expect(mockReplace).not.toHaveBeenCalled()
    })
  })

  describe('Installed apps', () => {
    it('should not query the OA session for an installed app', () => {
      renderWithSession(null, { isInstalledApp: true })

      expect(useOASession).toHaveBeenCalledWith({ enabled: false })
    })

    it('should query the OA session for a public webapp', () => {
      renderWithSession(signedInSession, { isInstalledApp: false })

      expect(useOASession).toHaveBeenCalledWith({ enabled: true })
    })
  })
})
