import { TestBed } from '@angular/core/testing';
import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { AuthService, UserContext, AuthResponse, AUTH_STORAGE, AUTH_STORAGE_KEY } from './auth.service';
import { authInterceptor } from './auth.interceptor';

function createMockJwt(payload: Record<string, unknown>): string {
  const header = { alg: 'HS256', typ: 'JWT' };
  const base64Url = (obj: unknown) => {
    const json = JSON.stringify(obj);
    return btoa(json)
      .replace(/=/g, '')
      .replace(/\+/g, '-')
      .replace(/\//g, '_');
  };
  return `${base64Url(header)}.${base64Url(payload)}.mock_signature`;
}

describe('AuthService & authInterceptor (T010)', () => {
  let authService: AuthService;
  let httpClient: HttpClient;
  let httpTesting: HttpTestingController;
  let storage: Storage;

  const validClaims = {
    sub: '11111111-1111-1111-1111-111111111111',
    username: 'test_admin',
    email: 'admin@datacatalog.enterprise.com',
    roles: ['SUPERADMIN', 'DATA_STEWARD'],
    is_superadmin: true,
    iat: Math.floor(Date.now() / 1000) - 60,
    exp: Math.floor(Date.now() / 1000) + 3600, // Valid for 1 hour
  };

  const expiredClaims = {
    sub: '22222222-2222-2222-2222-222222222222',
    username: 'expired_user',
    email: 'expired@datacatalog.enterprise.com',
    roles: ['CATALOG_VIEWER'],
    is_superadmin: false,
    iat: Math.floor(Date.now() / 1000) - 7200,
    exp: Math.floor(Date.now() / 1000) - 3600, // Expired 1 hour ago
  };

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        AuthService,
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
      ],
    });

    storage = TestBed.inject(AUTH_STORAGE);
    storage.clear();

    authService = TestBed.inject(AuthService);
    httpClient = TestBed.inject(HttpClient);
    httpTesting = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpTesting.verify();
    storage.clear();
  });

  describe('AuthService - Reactive Signals & Token Management', () => {
    it('should initialize with null token, null currentUser, and unauthenticated state when storage is empty', () => {
      expect(authService.token()).toBeNull();
      expect(authService.currentUser()).toBeNull();
      expect(authService.isAuthenticated()).toBe(false);
    });

    it('should store token, decode claims, and update reactive signals on setToken()', () => {
      const token = createMockJwt(validClaims);
      authService.setToken(token);

      expect(authService.token()).toBe(token);
      expect(authService.isAuthenticated()).toBe(true);

      const user: UserContext | null = authService.currentUser();
      expect(user).not.toBeNull();
      expect(user?.id).toBe(validClaims.sub);
      expect(user?.username).toBe(validClaims.username);
      expect(user?.email).toBe(validClaims.email);
      expect(user?.roles).toEqual(['SUPERADMIN', 'DATA_STEWARD']);
      expect(user?.is_superadmin).toBe(true);
      expect(storage.getItem(AUTH_STORAGE_KEY)).toBe(token);
    });

    it('should detect expired tokens correctly via isTokenExpired()', () => {
      const validToken = createMockJwt(validClaims);
      const expiredToken = createMockJwt(expiredClaims);

      expect(authService.isTokenExpired(validToken)).toBe(false);
      expect(authService.isTokenExpired(expiredToken)).toBe(true);
      expect(authService.isTokenExpired(null)).toBe(true);
    });

    it('should clear reactive signals and remove token from storage on logout()', () => {
      const token = createMockJwt(validClaims);
      authService.setToken(token);
      expect(authService.isAuthenticated()).toBe(true);

      authService.logout();

      expect(authService.token()).toBeNull();
      expect(authService.currentUser()).toBeNull();
      expect(authService.isAuthenticated()).toBe(false);
      expect(storage.getItem(AUTH_STORAGE_KEY)).toBeNull();
    });

    it('should restore unexpired token and user signals from storage on initial load', () => {
      const token = createMockJwt(validClaims);
      storage.setItem(AUTH_STORAGE_KEY, token);

      const freshService = TestBed.runInInjectionContext(() => new AuthService());
      expect(freshService.token()).toBe(token);
      expect(freshService.currentUser()?.username).toBe('test_admin');
      expect(freshService.isAuthenticated()).toBe(true);
    });

    it('should discard expired token from storage on initial load', () => {
      const expiredToken = createMockJwt(expiredClaims);
      storage.setItem(AUTH_STORAGE_KEY, expiredToken);

      const freshService = TestBed.runInInjectionContext(() => new AuthService());
      expect(freshService.token()).toBeNull();
      expect(freshService.currentUser()).toBeNull();
      expect(freshService.isAuthenticated()).toBe(false);
      expect(storage.getItem(AUTH_STORAGE_KEY)).toBeNull();
    });

    it('should execute login() HTTP request and update reactive signals on success', () => {
      const token = createMockJwt(validClaims);

      authService
        .login({ username_or_email: 'test_admin', password: 'ValidPassword123!' })
        .subscribe((res: AuthResponse) => {
          expect(res.access_token).toBe(token);
        });

      const req = httpTesting.expectOne('/api/v1/auth/login');
      expect(req.request.method).toBe('POST');
      expect(req.request.body).toEqual({
        username_or_email: 'test_admin',
        password: 'ValidPassword123!',
      });

      req.flush({
        access_token: token,
        token_type: 'bearer',
      });

      expect(authService.token()).toBe(token);
      expect(authService.currentUser()?.username).toBe('test_admin');
      expect(authService.isAuthenticated()).toBe(true);
    });
  });

  describe('authInterceptor - Bearer Token Injection & 401 Handling', () => {
    it('should inject Authorization Bearer header when user has active unexpired token', () => {
      const token = createMockJwt(validClaims);
      authService.setToken(token);

      httpClient.get('/api/v1/search?q=catalog').subscribe();

      const req = httpTesting.expectOne('/api/v1/search?q=catalog');
      expect(req.request.headers.has('Authorization')).toBe(true);
      expect(req.request.headers.get('Authorization')).toBe(`Bearer ${token}`);

      req.flush({ items: [] });
    });

    it('should not attach Authorization header when user is unauthenticated', () => {
      httpClient.get('/api/v1/health').subscribe();

      const req = httpTesting.expectOne('/api/v1/health');
      expect(req.request.headers.has('Authorization')).toBe(false);

      req.flush({ status: 'ok' });
    });

    it('should not attach Authorization header if token is expired', () => {
      const expiredToken = createMockJwt(expiredClaims);
      authService.setToken(expiredToken);

      httpClient.get('/api/v1/tables').subscribe();

      const req = httpTesting.expectOne('/api/v1/tables');
      expect(req.request.headers.has('Authorization')).toBe(false);

      req.flush({ items: [] });
    });

    it('should trigger logout when an HTTP request receives a 401 Unauthorized response', () => {
      const token = createMockJwt(validClaims);
      authService.setToken(token);
      expect(authService.isAuthenticated()).toBe(true);

      let errorCaught = false;
      httpClient.get('/api/v1/protected-resource').subscribe({
        next: () => {
          throw new Error('Expected 401 error, but request succeeded');
        },
        error: (err) => {
          expect(err.status).toBe(401);
          errorCaught = true;
        },
      });

      const req = httpTesting.expectOne('/api/v1/protected-resource');
      req.flush('Unauthorized', { status: 401, statusText: 'Unauthorized' });

      expect(errorCaught).toBe(true);
      expect(authService.token()).toBeNull();
      expect(authService.currentUser()).toBeNull();
      expect(authService.isAuthenticated()).toBe(false);
      expect(storage.getItem(AUTH_STORAGE_KEY)).toBeNull();
    });
  });
});
