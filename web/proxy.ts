// eslint-disable-next-line no-restricted-imports
import type { NextRequest } from 'next/server'
import { Buffer } from 'node:buffer'
// eslint-disable-next-line no-restricted-imports
import { NextResponse } from 'next/server'
import { env } from '@/env'

const NECESSARY_DOMAIN = '*.sentry.io http://localhost:* http://127.0.0.1:* https://analytics.google.com googletagmanager.com *.googletagmanager.com https://www.google-analytics.com https://ungh.cc https://api2.amplitude.com *.amplitude.com'

// Routes that may legitimately be embedded in an iframe. Everything else
// gets a DENY X-Frame-Options header to prevent clickjacking.
const EMBEDDABLE_ROUTE_PREFIXES = [
  '/chat',
  '/workflow',
  '/completion',
  '/webapp-signin',
  '/webapp-no-permission',
  '/webapp-permission-expired',
  '/oa-login',
] as const

const isEmbeddableRoute = (pathname: string) =>
  EMBEDDABLE_ROUTE_PREFIXES.some(prefix => pathname.startsWith(prefix))

const wrapResponseWithXFrameOptions = (response: NextResponse, pathname: string) => {
  // prevent clickjacking: https://owasp.org/www-community/attacks/Clickjacking
  // Chatbot page should be allowed to be embedded in iframe. It's a feature
  if (env.NEXT_PUBLIC_ALLOW_EMBED !== true && !isEmbeddableRoute(pathname))
    response.headers.set('X-Frame-Options', 'DENY')

  return response
}
export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl

  // `/chat/*` is deliberately NOT gated here any more. It used to redirect
  // cookie-less visitors to `/oa-login` based on the mere presence of the
  // `oa_session` cookie, but that blanket check cannot express the per-app
  // access policy: apps may opt into anonymous access (App.allow_anonymous),
  // and the edge middleware has no access to the app's config — it would also
  // block the apps whose owner explicitly allows anonymous visitors.
  //
  // The gate now lives where the app config is available, i.e. server-side:
  //   - GET  /api/passport          refuses to mint an anonymous passport for
  //                                 an app that requires sign-in (passport.py)
  //   - webapp API resources        reject anonymous callers (wraps.py)
  //   - GET  /api/webapp/permission returns reason='auth_required' (app.py)
  // and the client reacts to `web_app_login_required` / `auth_required` by
  // routing to /oa-login with the current URL (web/service/base.ts and
  // web/app/(shareLayout)/components/authenticated-layout.tsx).
  //
  // Defence in depth is unchanged: no app content is served before the
  // passport is issued, because every webapp data endpoint is a
  // WebApiResource that resolves the passport first.
  const requestHeaders = new Headers(request.headers)

  const isWhiteListEnabled = !!env.NEXT_PUBLIC_CSP_WHITELIST && process.env.NODE_ENV === 'production'
  if (!isWhiteListEnabled) {
    const response = NextResponse.next({
      request: {
        headers: requestHeaders,
      },
    })
    return wrapResponseWithXFrameOptions(response, pathname)
  }

  const whiteList = `${env.NEXT_PUBLIC_CSP_WHITELIST} ${NECESSARY_DOMAIN}`
  const nonce = Buffer.from(crypto.randomUUID()).toString('base64')
  const csp = `'nonce-${nonce}'`

  const scheme_source = 'data: mediastream: blob: filesystem:'

  const cspHeader = `
    default-src 'self' ${scheme_source} ${csp} ${whiteList};
    connect-src 'self' ${scheme_source} ${csp} ${whiteList};
    script-src 'self' 'wasm-unsafe-eval' ${scheme_source} ${csp} ${whiteList};
    style-src 'self' 'unsafe-inline' ${scheme_source} ${whiteList};
    worker-src 'self' ${scheme_source} ${csp} ${whiteList};
    media-src 'self' ${scheme_source} ${csp} ${whiteList};
    img-src * data: blob:;
    font-src 'self';
    object-src 'none';
    base-uri 'self';
    form-action 'self';
    upgrade-insecure-requests;
`
  // Replace newline characters and spaces
  const contentSecurityPolicyHeaderValue = cspHeader
    .replace(/\s{2,}/g, ' ')
    .trim()

  requestHeaders.set('x-nonce', nonce)

  requestHeaders.set(
    'Content-Security-Policy',
    contentSecurityPolicyHeaderValue,
  )

  const response = NextResponse.next({
    request: {
      headers: requestHeaders,
    },
  })

  response.headers.set(
    'Content-Security-Policy',
    contentSecurityPolicyHeaderValue,
  )

  return wrapResponseWithXFrameOptions(response, pathname)
}

export const config = {
  matcher: [
    /*
     * Match all request paths except for the ones starting with:
     * - api (API routes)
     * - _next/static (static files)
     * - favicon.ico (favicon file)
     */
    {
      source: '/((?!_next/static|favicon.ico).*)',
      // source: '/(.*)',
      // missing: [
      //   { type: 'header', key: 'next-router-prefetch' },
      //   { type: 'header', key: 'purpose', value: 'prefetch' },
      // ],
    },
  ],
}
