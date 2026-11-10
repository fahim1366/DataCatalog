import { TestBed } from '@angular/core/testing';
import {
  ActivatedRouteSnapshot,
  Router,
  RouterStateSnapshot,
  UrlTree,
  provideRouter,
} from '@angular/router';
import { AuthService, AUTH_STORAGE } from '../auth/auth.service';
import { RbacService, PermissionAction } from '../auth/rbac.service';
import { permissionGuard } from './permission.guard';

function createMockJwt(payload: Record<string, unknown>): string {
  const header = { alg: 'HS256', typ: 'JWT' };
  const base64Url = (obj: unknown) => {
    const json = JSON.stringify(obj);
    return btoa(json).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');
  };
  return `${base64Url(header)}.${base64Url(payload)}.mock_signature`;
}

describe('permissionGuard (T022)', () => {
  let rbacService: RbacService;
  let authService: AuthService;
  let router: Router;
  let storage: Storage;

  const mockRoute = (data: Record<string, unknown> = {}): ActivatedRouteSnapshot => {
    return {
      data,
      params: {},
      queryParams: {},
      url: [],
    } as unknown as ActivatedRouteSnapshot;
  };

  const mockState = (url = '/protected'): RouterStateSnapshot => {
    return {
      url,
      root: {} as ActivatedRouteSnapshot,
    } as RouterStateSnapshot;
  };

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        AuthService,
        RbacService,
      ],
    });

    storage = TestBed.inject(AUTH_STORAGE);
    storage.clear();

    authService = TestBed.inject(AuthService);
    rbacService = TestBed.inject(RbacService);
    router = TestBed.inject(Router);
  });

  afterEach(() => {
    storage.clear();
  });

  describe('Factory usage: permissionGuard(requiredAction)', () => {
    it('should allow access (return true) when user has the required permission', () => {
      // Data steward has VIEW_METADATA and EDIT_DESCRIPTION
      const token = createMockJwt({
        sub: 'user-1',
        username: 'steward_user',
        email: 'steward@datacatalog.com',
        roles: ['DATA_STEWARD'],
        is_superadmin: false,
        exp: Math.floor(Date.now() / 1000) + 3600,
      });
      authService.setToken(token);

      const guard = permissionGuard(PermissionAction.EDIT_DESCRIPTION);
      const result = TestBed.runInInjectionContext(() =>
        guard(mockRoute(), mockState())
      );

      expect(result).toBe(true);
    });

    it('should block access and return UrlTree to /unauthorized when user lacks required permission', () => {
      // Viewer only has VIEW_METADATA, not EDIT_DESCRIPTION
      const token = createMockJwt({
        sub: 'user-2',
        username: 'viewer_user',
        email: 'viewer@datacatalog.com',
        roles: ['VIEWER'],
        is_superadmin: false,
        exp: Math.floor(Date.now() / 1000) + 3600,
      });
      authService.setToken(token);

      const guard = permissionGuard(PermissionAction.EDIT_DESCRIPTION);
      const result = TestBed.runInInjectionContext(() =>
        guard(mockRoute(), mockState())
      );

      expect(result instanceof UrlTree).toBe(true);
      expect((result as UrlTree).toString()).toBe('/unauthorized');
    });

    it('should always allow superadmin user regardless of required permission', () => {
      const token = createMockJwt({
        sub: 'admin-user',
        username: 'root_admin',
        email: 'root@datacatalog.com',
        roles: ['SUPERADMIN'],
        is_superadmin: true,
        exp: Math.floor(Date.now() / 1000) + 3600,
      });
      authService.setToken(token);

      const guard = permissionGuard(PermissionAction.MANAGE_CONNECTIONS);
      const result = TestBed.runInInjectionContext(() =>
        guard(mockRoute(), mockState())
      );

      expect(result).toBe(true);
    });

    it('should block unauthenticated user and redirect to /unauthorized', () => {
      authService.logout();

      const guard = permissionGuard(PermissionAction.VIEW_METADATA);
      const result = TestBed.runInInjectionContext(() =>
        guard(mockRoute(), mockState())
      );

      expect(result instanceof UrlTree).toBe(true);
      expect((result as UrlTree).toString()).toBe('/unauthorized');
    });

    it('should enforce multiple permissions when array is passed', () => {
      // Data steward has VIEW_METADATA, EDIT_DESCRIPTION, MANAGE_TAGS, EXPORT_REPORTS
      // but lacks MANAGE_CONNECTIONS
      const token = createMockJwt({
        sub: 'user-steward',
        username: 'steward',
        email: 'steward@datacatalog.com',
        roles: ['DATA_STEWARD'],
        is_superadmin: false,
        exp: Math.floor(Date.now() / 1000) + 3600,
      });
      authService.setToken(token);

      // Should pass when user has all requested
      const passingGuard = permissionGuard([
        PermissionAction.VIEW_METADATA,
        PermissionAction.EDIT_DESCRIPTION,
      ]);
      const passResult = TestBed.runInInjectionContext(() =>
        passingGuard(mockRoute(), mockState())
      );
      expect(passResult).toBe(true);

      // Should block when user lacks one of them
      const blockingGuard = permissionGuard([
        PermissionAction.VIEW_METADATA,
        PermissionAction.MANAGE_CONNECTIONS,
      ]);
      const blockResult = TestBed.runInInjectionContext(() =>
        blockingGuard(mockRoute(), mockState())
      );
      expect(blockResult instanceof UrlTree).toBe(true);
      expect((blockResult as UrlTree).toString()).toBe('/unauthorized');
    });
  });

  describe('Direct router usage: permissionGuard(route, state)', () => {
    it('should inspect route.data.permission and allow when authorized', () => {
      const token = createMockJwt({
        sub: 'engineer-1',
        username: 'engineer',
        email: 'eng@datacatalog.com',
        roles: ['DATA_ENGINEER'],
        is_superadmin: false,
        exp: Math.floor(Date.now() / 1000) + 3600,
      });
      authService.setToken(token);

      const route = mockRoute({ permission: PermissionAction.MANAGE_CONNECTIONS });
      const result = TestBed.runInInjectionContext(() =>
        permissionGuard(route, mockState('/connections'))
      );

      expect(result).toBe(true);
    });

    it('should inspect route.data.permission and block when unauthorized', () => {
      const token = createMockJwt({
        sub: 'engineer-2',
        username: 'engineer2',
        email: 'eng2@datacatalog.com',
        roles: ['DATA_ENGINEER'],
        is_superadmin: false,
        exp: Math.floor(Date.now() / 1000) + 3600,
      });
      authService.setToken(token);

      const route = mockRoute({ permission: PermissionAction.VIEW_AUDIT_LOGS });
      const result = TestBed.runInInjectionContext(() =>
        permissionGuard(route, mockState('/audit'))
      );

      expect(result instanceof UrlTree).toBe(true);
      expect((result as UrlTree).toString()).toBe('/unauthorized');
    });
  });
});

