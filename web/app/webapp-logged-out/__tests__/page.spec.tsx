import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import WebappLoggedOutPage from '../page'

// Each test builds a controlled `URLSearchParams` via this factory so the
// page's `useSearchParams()` returns a fresh, deterministic snapshot.
const mockUseSearchParams = vi.fn<() => URLSearchParams>()

vi.mock('@/next/navigation', () => ({
  useSearchParams: () => mockUseSearchParams(),
}))

describe('WebappLoggedOutPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe('Message rendering', () => {
    it('should render the generic copy when no account is provided', () => {
      mockUseSearchParams.mockReturnValue(new URLSearchParams())
      render(<WebappLoggedOutPage />)

      // The global i18n auto-mock returns the requested key verbatim, so the
      // heading text exposes the key the page picked.
      const heading = screen.getByRole('heading')
      expect(heading).toHaveTextContent(/common\.webapp\.loggedOut\.generic/)
    })

    it('should render the account-specific copy when account is provided', () => {
      mockUseSearchParams.mockReturnValue(new URLSearchParams('account=Alice'))
      render(<WebappLoggedOutPage />)

      const heading = screen.getByRole('heading')
      expect(heading).toHaveTextContent(/common\.webapp\.loggedOut\.withAccount/)
      // The auto-mock serialises the interpolation params into the returned
      // key, so we can assert the account value landed in the rendered text.
      expect(heading).toHaveTextContent(/Alice/)
    })

    it('should URL-decode the account parameter', () => {
      // URLSearchParams decodes percent-encoding on `get`, so passing
      // `Alice%20Lee` exercises the page's read path end-to-end.
      mockUseSearchParams.mockReturnValue(new URLSearchParams('account=Alice%20Lee'))
      render(<WebappLoggedOutPage />)

      const heading = screen.getByRole('heading')
      expect(heading).toHaveTextContent(/Alice Lee/)
      expect(heading).not.toHaveTextContent(/Alice%20Lee/)
    })

    it('should fall back to the generic copy when account is empty', () => {
      // Present-but-empty must not show "Account  has been signed out."
      // (note the double space) — the page treats it as "no name".
      mockUseSearchParams.mockReturnValue(new URLSearchParams('account='))
      render(<WebappLoggedOutPage />)

      const heading = screen.getByRole('heading')
      expect(heading).toHaveTextContent(/common\.webapp\.loggedOut\.generic/)
    })
  })

  describe('XSS safety', () => {
    it('should render HTML-like account names as text, not as elements', () => {
      // After URL decoding, `account` becomes `<script>alert(1)</script>`.
      // React text-node rendering must escape this; no `<script>` element
      // should ever land in the DOM.
      mockUseSearchParams.mockReturnValue(
        new URLSearchParams(`account=${encodeURIComponent('<script>alert(1)</script>')}`),
      )
      const { container } = render(<WebappLoggedOutPage />)

      expect(container.querySelector('script')).toBeNull()
      // The literal characters survive in the heading textContent, proving
      // we did not switch to a dangerously-rendered HTML path.
      const heading = screen.getByRole('heading')
      expect(heading.textContent).toContain('<script>alert(1)</script>')
    })
  })

  describe('No interactive affordances', () => {
    // Spec requirement: the page is purely informational. No re-login
    // button, no "back" link, no anchor. The visitor has to navigate to
    // a /chat/<token> URL themselves.
    it('should not render any button', () => {
      mockUseSearchParams.mockReturnValue(new URLSearchParams())
      render(<WebappLoggedOutPage />)

      expect(screen.queryByRole('button')).toBeNull()
    })

    it('should not render any link', () => {
      mockUseSearchParams.mockReturnValue(new URLSearchParams())
      render(<WebappLoggedOutPage />)

      expect(screen.queryByRole('link')).toBeNull()
    })
  })

  describe('Accessibility', () => {
    // The h1 carries aria-live="polite" directly. We deliberately did
    // NOT add `role="status"`: an explicit role would override the h1's
    // implicit heading role and break document outline queries.
    it('should set aria-live="polite" on the heading element', () => {
      mockUseSearchParams.mockReturnValue(new URLSearchParams())
      render(<WebappLoggedOutPage />)

      const heading = screen.getByRole('heading')
      expect(heading.getAttribute('aria-live')).toBe('polite')
      expect(heading.getAttribute('aria-atomic')).toBe('true')
    })

    it('should keep the heading accessible name aligned with the rendered message', () => {
      // With aria-live + aria-atomic, a screen reader re-announces the
      // entire heading on language switch / re-render. Verifying the
      // accessible name is the heading's own textContent proves the
      // `aria-atomic` value is doing the right thing.
      mockUseSearchParams.mockReturnValue(new URLSearchParams('account=Alice'))
      render(<WebappLoggedOutPage />)

      const heading = screen.getByRole('heading', { name: /Alice/ })
      expect(heading).toBeInTheDocument()
    })
  })
})
