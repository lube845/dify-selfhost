export type AppAccessPolicy = 'allow_all' | 'deny_all_explicit'

export type WhitelistEntry = {
  id: string
  app_id: string
  user_id: string
  expires_at: string | null
  created_at: string
  updated_at: string
}

/**
 * Whether a visitor with no signed-in identity may chat on the app
 * (`allow_anonymous` on the backend).
 *
 * Only meaningful while the app's access policy is `allow_all`: under
 * `deny_all_explicit` the visitor must sign in *and* be on the allowlist, so
 * anonymous visitors can never get in.
 */
export type AppPermissionApp = {
  id: string
  name: string
  access_policy: AppAccessPolicy
  allow_anonymous: boolean
}

export const isAppAccessPolicy = (value: unknown): value is AppAccessPolicy =>
  value === 'allow_all' || value === 'deny_all_explicit'
