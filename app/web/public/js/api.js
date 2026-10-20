// Shared by the student and teacher pages: every call to the API goes through a client
// from createClient(), so sign-in tokens, timeouts and error handling live in one place.
// URLs are relative ("/api/..."): the browser sends them to whichever server served
// this page, and nginx forwards them to the API. No API address is hardcoded.

const TIMEOUT_MS = 15000;

export class ApiError extends Error {
  // kind: "unreachable" (network down / API not running), "unauthorized" (401: not signed in,
  // or the sign-in expired), "not_found" (404), "conflict" (409), or "other"
  constructor(kind, status = null) {
    super(`API error: ${kind}${status ? ` (HTTP ${status})` : ""}`);
    this.kind = kind;
    this.status = status;
  }
}

const STATUS_KINDS = { 401: "unauthorized", 404: "not_found", 409: "conflict" };

export const isApiError = (error, kind) => error instanceof ApiError && error.kind === kind;

export const pathId = (value) => encodeURIComponent(value);

/**
 * A client with its own sign-in. Each page passes a different storageKey, so a student
 * and a teacher signed in on the same browser never share a token.
 *
 * The session is kept in sessionStorage: it survives a page refresh, but is erased when
 * the tab is closed, so the next person on a shared Chromebook starts signed out.
 * Storage can be blocked (e.g. some private-browsing modes), hence the try/catch:
 * the page still works, you just have to sign in again after a refresh.
 */
export function createClient(storageKey) {
  let session = loadSession();

  function loadSession() {
    try {
      const saved = JSON.parse(sessionStorage.getItem(storageKey));
      if (saved && new Date(saved.expires_at) > new Date()) return saved;
    } catch {
      // Missing, unreadable, or storage blocked: treat as signed out.
    }
    return null;
  }

  function signOut() {
    session = null;
    try {
      sessionStorage.removeItem(storageKey);
    } catch {
      // Storage blocked: nothing was saved, so there's nothing to remove.
    }
  }

  async function send(method, path, body) {
    const headers = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (session) headers.Authorization = `Bearer ${session.token}`;

    let response;
    try {
      response = await fetch(`/api${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        // ?.() skips the timeout on older iPads that don't have AbortSignal.timeout.
        signal: AbortSignal.timeout?.(TIMEOUT_MS),
      });
    } catch {
      // fetch() only throws when no response arrived at all: offline, or timed out.
      throw new ApiError("unreachable");
    }

    // nginx answers 502/504 itself when it can't reach the API behind it.
    if ([502, 503, 504].includes(response.status)) throw new ApiError("unreachable", response.status);
    if (!response.ok) throw new ApiError(STATUS_KINDS[response.status] ?? "other", response.status);
    return response.status === 204 ? null : response.json(); // 204 No Content has no body
  }

  return {
    getSession: () => session,
    signOut,

    /** POSTs credentials to a sign-in endpoint and keeps the returned session. */
    async signIn(path, credentials) {
      signOut(); // never send an old token along with new credentials
      session = await send("POST", path, credentials);
      try {
        sessionStorage.setItem(storageKey, JSON.stringify(session));
      } catch {
        // Storage blocked: stay signed in for this page only.
      }
      return session;
    },

    get: (path) => send("GET", path),
    post: (path, body) => send("POST", path, body),
    put: (path) => send("PUT", path),
    delete: (path) => send("DELETE", path),
  };
}
