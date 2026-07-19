import { createContext, useContext, useState, useEffect } from "react";
import type { ReactNode } from "react";
import { toast } from "sonner";
import { COOKIE_SESSION_MARKER } from "../lib/adminApi";

interface AuthContextType {
  token: string | null;
  login: () => void;
  logout: () => Promise<void>;
  isAuthenticated: boolean;
  isLoading: boolean;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(localStorage.getItem("adminToken"));
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const checkToken = async () => {
      const legacyToken = localStorage.getItem("adminToken");
      try {
        const cookieResponse = await fetch("/api/stats", {
          credentials: "include",
        });
        if (cookieResponse.ok) {
          localStorage.removeItem("adminToken");
          setToken(COOKIE_SESSION_MARKER);
          return;
        }

        if (legacyToken) {
          const bearerResponse = await fetch("/api/stats", {
            credentials: "include",
            headers: { Authorization: `Bearer ${legacyToken}` },
          });
          if (bearerResponse.ok) {
            localStorage.removeItem("adminToken");
            setToken(COOKIE_SESSION_MARKER);
            return;
          }
        }
        throw new Error("Invalid session");
      } catch (err) {
        console.error("Token verification failed:", err);
        setToken(null);
        localStorage.removeItem("adminToken");
        toast.error("Session expired. Please log in again.");
      } finally {
        setIsLoading(false);
      }
    };
    checkToken();
  }, []);

  const login = () => {
    localStorage.removeItem("adminToken");
    setToken(COOKIE_SESSION_MARKER);
  };

  const logout = async () => {
    const currentToken = token;
    const headers = new Headers();
    if (currentToken && currentToken !== COOKIE_SESSION_MARKER) {
      headers.set("Authorization", `Bearer ${currentToken}`);
    }
    try {
      await fetch("/api/admin/logout", {
        method: "POST",
        credentials: "include",
        headers,
      });
    } catch (err) {
      console.error("Server logout failed:", err);
    } finally {
      setToken(null);
      localStorage.removeItem("adminToken");
      toast.success("Logged out successfully");
    }
  };

  return (
    <AuthContext.Provider value={{ token, login, logout, isAuthenticated: !!token, isLoading }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
