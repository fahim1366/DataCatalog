import { Injectable, InjectionToken, computed, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, tap } from 'rxjs';

export interface UserContext {
  id: string;
  username: string;
  email: string;
  roles: string[];
  is_superadmin: boolean;
  permissions?: string[];
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user?: UserContext;
}

export interface LoginRequest {
  username_or_email: string;
  password: string;
}

export interface JwtPayload {
  sub?: string;
  username?: string;
  email?: string;
  roles?: string[];
  is_superadmin?: boolean;
  permissions?: string[];
  exp?: number;
  iat?: number;
  nbf?: number;
  jti?: string;
  [key: string]: unknown;
}

export const AUTH_STORAGE_KEY = 'datacatalog_access_token';

/**
 * Injection token for storage abstraction.
 * Falls back to an in-memory Storage implementation in environments where
 * window.localStorage is not accessible (e.g. Node 22+ runners, SSR).
 */
export const AUTH_STORAGE = new InjectionToken<Storage>('AUTH_STORAGE', {
  providedIn: 'root',
  factory: () => {
    try {
      if (
        typeof window !== 'undefined' &&
        window.localStorage &&
        typeof window.localStorage.getItem === 'function'
      ) {
        return window.localStorage;
      }
    } catch {
      // Fall through to in-memory store
    }

    const store = new Map<string, string>();
    return {
      getItem: (key: string): string | null => store.get(key) ?? null,
      setItem: (key: string, value: string): void => {
        store.set(key, String(value));
      },
      removeItem: (key: string): void => {
        store.delete(key);
      },
      clear: (): void => {
        store.clear();
      },
      key: (index: number): string | null => Array.from(store.keys())[index] ?? null,
      get length(): number {
        return store.size;
      },
    } as Storage;
  },
});

@Injectable({
  providedIn: 'root',
})
export class AuthService {
  private readonly http = inject(HttpClient, { optional: true });
  private readonly storage = inject(AUTH_STORAGE);

  private readonly _token = signal<string | null>(null);
  private readonly _currentUser = signal<UserContext | null>(null);

  public readonly token = this._token.asReadonly();
  public readonly currentUser = this._currentUser.asReadonly();

  public readonly isAuthenticated = computed(() => {
    const currentToken = this._token();
    return !!currentToken && !this.isTokenExpired(currentToken);
  });

  constructor() {
    this.restoreSessionFromStorage();
  }

  public getToken(): string | null {
    return this._token();
  }

  public setToken(token: string): void {
    if (!token) {
      this.logout();
      return;
    }

    const payload = this.decodePayload(token);
    if (!payload) {
      return;
    }

    const user: UserContext = {
      id: payload.sub ?? '',
      username: payload.username ?? '',
      email: payload.email ?? '',
      roles: Array.isArray(payload.roles) ? [...payload.roles] : [],
      is_superadmin: Boolean(payload.is_superadmin),
      permissions: Array.isArray(payload.permissions) ? [...payload.permissions] : [],
    };

    this._token.set(token);
    this._currentUser.set(user);
    this.storage.setItem(AUTH_STORAGE_KEY, token);
  }

  public logout(): void {
    this._token.set(null);
    this._currentUser.set(null);
    this.storage.removeItem(AUTH_STORAGE_KEY);
  }

  public login(credentials: LoginRequest): Observable<AuthResponse> {
    if (!this.http) {
      throw new Error('HttpClient is not available in the current injection context');
    }

    return this.http.post<AuthResponse>('/api/v1/auth/login', credentials).pipe(
      tap((response: AuthResponse) => {
        if (response?.access_token) {
          this.setToken(response.access_token);
        }
      })
    );
  }

  public isTokenExpired(token?: string | null): boolean {
    const targetToken = token !== undefined ? token : this._token();
    if (!targetToken) {
      return true;
    }

    const payload = this.decodePayload(targetToken);
    if (!payload || typeof payload.exp !== 'number') {
      return true;
    }

    const currentEpochSeconds = Math.floor(Date.now() / 1000);
    return payload.exp <= currentEpochSeconds;
  }

  public decodePayload(token: string): JwtPayload | null {
    try {
      const parts = token.split('.');
      if (parts.length !== 3) {
        return null;
      }

      let base64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
      while (base64.length % 4 !== 0) {
        base64 += '=';
      }

      const binaryString = atob(base64);
      const bytes = new Uint8Array(binaryString.length);
      for (let i = 0; i < binaryString.length; i++) {
        bytes[i] = binaryString.charCodeAt(i);
      }

      const decodedText = new TextDecoder('utf-8').decode(bytes);
      return JSON.parse(decodedText) as JwtPayload;
    } catch {
      return null;
    }
  }

  private restoreSessionFromStorage(): void {
    const storedToken = this.storage.getItem(AUTH_STORAGE_KEY);
    if (!storedToken) {
      return;
    }

    if (!this.isTokenExpired(storedToken)) {
      this.setToken(storedToken);
    } else {
      this.storage.removeItem(AUTH_STORAGE_KEY);
    }
  }
}
