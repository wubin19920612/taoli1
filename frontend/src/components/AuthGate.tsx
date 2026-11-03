import { useEffect, useState, type ReactNode } from "react";
import { AUTH_REQUIRED_EVENT, clearLegacyPasswords } from "../api/auth";
import { LoginPage } from "../pages/LoginPage";

export function AuthGate({ children }: { children: ReactNode }) {
  const [login, setLogin] = useState(() => {
    clearLegacyPasswords();
    return window.location.pathname === "/login";
  });
  useEffect(() => {
    const update = () => setLogin(window.location.pathname === "/login");
    window.addEventListener(AUTH_REQUIRED_EVENT, update);
    window.addEventListener("popstate", update);
    // Covers a request that failed between initial rendering and effect setup.
    update();
    return () => {
      window.removeEventListener(AUTH_REQUIRED_EVENT, update);
      window.removeEventListener("popstate", update);
    };
  }, []);
  return login ? <LoginPage /> : <>{children}</>;
}
