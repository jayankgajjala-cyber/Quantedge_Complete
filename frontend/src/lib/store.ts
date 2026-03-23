import { create } from "zustand";
import { persist } from "zustand/middleware";

interface AuthState {
  token:           string | null;
  username:        string | null;
  pendingUsername: string | null;
  _hasHydrated:    boolean;
  setToken:           (token: string, username: string) => void;
  setPendingUsername: (u: string) => void;
  logout:             () => void;
  setHasHydrated:     (v: boolean) => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token:           null,
      username:        null,
      pendingUsername: null,
      _hasHydrated:    false,
      setToken:           (token, username) => set({ token, username, pendingUsername: null }),
      setPendingUsername: (pendingUsername)  => set({ pendingUsername }),
      logout:             ()                 => set({ token: null, username: null, pendingUsername: null }),
      setHasHydrated:     (v)                => set({ _hasHydrated: v }),
    }),
    {
      name: "trading-auth",
      partialize: (s) => ({ token: s.token, username: s.username }),
      onRehydrateStorage: () => (state) => {
        state?.setHasHydrated(true);
      },
    }
  )
);
