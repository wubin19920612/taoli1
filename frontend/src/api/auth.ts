export const AUTH_REQUIRED_EVENT = "taoli1:auth-required";

export function clearLegacyPasswords(): void {
  for (const name of ["localStorage", "sessionStorage"] as const) {
    try {
      window[name].removeItem("dashboard_password");
    } catch {
      // Storage may be disabled; authentication never depends on it.
    }
  }
}

export function redirectToLogin(): void {
  if (window.location.pathname === "/login") return;
  const next = window.location.pathname + window.location.search + window.location.hash;
  window.history.replaceState(null, "", `/login?next=${encodeURIComponent(next)}`);
  window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT));
}

export function loginReturnPath(): string {
  const next = new URLSearchParams(window.location.search).get("next") ?? "/";
  try {
    const url = new URL(next, window.location.origin);
    if (url.origin !== window.location.origin || url.pathname === "/login") return "/";
    return url.pathname + url.search + url.hash;
  } catch {
    return "/";
  }
}

export async function sessionFetch(input: string, options: RequestInit = {}): Promise<Response> {
  const url = new URL(input, window.location.origin);
  if (url.origin !== window.location.origin) throw new Error("仅允许同源请求");
  const response = await fetch(url.toString(), { ...options, credentials: "same-origin" });
  if (response.status === 401) redirectToLogin();
  return response;
}
