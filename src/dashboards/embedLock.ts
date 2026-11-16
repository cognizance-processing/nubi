/**
 * embedLock.js — Pure helper for embed-token JWT payload decoding.
 *
 * Client-side only: we base64-decode the JWT payload to READ which vars are
 * locked.  Signature verification is done server-side — the client just needs
 * to know the locked param names+values so it can fail-closed (no access
 * widening) before the server enforces it anyway.
 */

/**
 * Decode a JWT payload (base64url → JSON) without verifying the signature.
 *
 * @param {string} token  — raw JWT string
 * @returns {object|null} — parsed payload, or null if malformed
 */
export function decodeJwtPayload(token) {
  try {
    const parts = token.split('.')
    if (parts.length < 2) return null
    const b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    return JSON.parse(atob(b64))
  } catch {
    return null
  }
}
