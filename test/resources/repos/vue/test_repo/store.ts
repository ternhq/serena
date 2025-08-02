// Test store for Vue integration
export interface AuthStore {
  isAuthenticated: boolean;
  login(): void;
  logout(): void;
}

export const authStore: AuthStore = {
  isAuthenticated: false,
  login() {
    this.isAuthenticated = true;
  },
  logout() {
    this.isAuthenticated = false;
  }
};

export function getAuthStore(): AuthStore {
  return authStore;
}

export default authStore;