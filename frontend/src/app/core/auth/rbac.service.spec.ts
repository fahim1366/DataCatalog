import { TestBed } from '@angular/core/testing';
import { AuthService, AUTH_STORAGE } from './auth.service';
import { RbacService, PermissionAction } from './rbac.service';

function createMockJwt(payload: Record<string, unknown>): string {
  const header = { alg: 'HS256', typ: 'JWT' };
  const base64Url = (obj: unknown) => {
    const json = JSON.stringify(obj);
    return btoa(json).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');
  };
  return `${base64Url(header)}.${base64Url(payload)}.mock_signature`;
}

describe('RbacService', () => {
  let rbacService: RbacService;
  let authService: AuthService;
  let storage: Storage;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [AuthService, RbacService],
    });

    storage = TestBed.inject(AUTH_STORAGE);
    storage.clear();

    authService = TestBed.inject(AuthService);
    rbacService = TestBed.inject(RbacService);
  });

  afterEach(() => {
    storage.clear();
  });

  it('should return false for all checks when no user is logged in', () => {
    authService.logout();

    expect(rbacService.hasPermission(PermissionAction.VIEW_METADATA)).toBe(false);
    expect(rbacService.hasPermission(PermissionAction.EDIT_DESCRIPTION)).toBe(false);
    expect(rbacService.hasAnyPermission([PermissionAction.VIEW_METADATA])).toBe(false);
    expect(rbacService.hasRole('ADMIN')).toBe(false);
    expect(rbacService.effectivePermissions().size).toBe(0);
  });

  it('should grant all permissions and roles to superadmin', () => {
    const token = createMockJwt({
      sub: 'admin-uuid',
      username: 'superadmin',
      email: 'admin@datacatalog.com',
      roles: ['SUPERADMIN'],
      is_superadmin: true,
      exp: Math.floor(Date.now() / 1000) + 3600,
    });
    authService.setToken(token);

    expect(rbacService.hasPermission(PermissionAction.VIEW_METADATA)).toBe(true);
    expect(rbacService.hasPermission(PermissionAction.EDIT_DESCRIPTION)).toBe(true);
    expect(rbacService.hasPermission(PermissionAction.MANAGE_TAGS)).toBe(true);
    expect(rbacService.hasPermission(PermissionAction.MANAGE_CONNECTIONS)).toBe(true);
    expect(rbacService.hasPermission(PermissionAction.VIEW_AUDIT_LOGS)).toBe(true);
    expect(rbacService.hasPermission(PermissionAction.EXPORT_REPORTS)).toBe(true);
    expect(rbacService.hasPermission([PermissionAction.VIEW_AUDIT_LOGS, PermissionAction.MANAGE_CONNECTIONS])).toBe(true);
    expect(rbacService.hasRole('ANY_ROLE')).toBe(true);
  });

  it('should resolve standard role permissions for DATA_STEWARD', () => {
    const token = createMockJwt({
      sub: 'steward-uuid',
      username: 'steward',
      email: 'steward@datacatalog.com',
      roles: ['DATA_STEWARD'],
      is_superadmin: false,
      exp: Math.floor(Date.now() / 1000) + 3600,
    });
    authService.setToken(token);

    expect(rbacService.hasPermission(PermissionAction.VIEW_METADATA)).toBe(true);
    expect(rbacService.hasPermission(PermissionAction.EDIT_DESCRIPTION)).toBe(true);
    expect(rbacService.hasPermission(PermissionAction.MANAGE_TAGS)).toBe(true);
    expect(rbacService.hasPermission(PermissionAction.EXPORT_REPORTS)).toBe(true);

    // Should NOT have manage connections or view audit logs
    expect(rbacService.hasPermission(PermissionAction.MANAGE_CONNECTIONS)).toBe(false);
    expect(rbacService.hasPermission(PermissionAction.VIEW_AUDIT_LOGS)).toBe(false);
  });

  it('should support dynamic custom permissions and grant/revoke', () => {
    const token = createMockJwt({
      sub: 'viewer-uuid',
      username: 'viewer',
      email: 'viewer@datacatalog.com',
      roles: ['VIEWER'],
      is_superadmin: false,
      exp: Math.floor(Date.now() / 1000) + 3600,
    });
    authService.setToken(token);

    expect(rbacService.hasPermission(PermissionAction.EDIT_DESCRIPTION)).toBe(false);

    // Grant custom permission
    rbacService.grantPermission(PermissionAction.EDIT_DESCRIPTION);
    expect(rbacService.hasPermission(PermissionAction.EDIT_DESCRIPTION)).toBe(true);

    // Revoke custom permission
    rbacService.revokePermission(PermissionAction.EDIT_DESCRIPTION);
    expect(rbacService.hasPermission(PermissionAction.EDIT_DESCRIPTION)).toBe(false);
  });

  it('should support hasAnyPermission across multiple actions', () => {
    const token = createMockJwt({
      sub: 'analyst-uuid',
      username: 'analyst',
      email: 'analyst@datacatalog.com',
      roles: ['ANALYST'],
      is_superadmin: false,
      exp: Math.floor(Date.now() / 1000) + 3600,
    });
    authService.setToken(token);

    // Analyst has VIEW_METADATA and EXPORT_REPORTS
    expect(
      rbacService.hasAnyPermission([
        PermissionAction.VIEW_AUDIT_LOGS,
        PermissionAction.EXPORT_REPORTS,
      ])
    ).toBe(true);

    expect(
      rbacService.hasAnyPermission([
        PermissionAction.VIEW_AUDIT_LOGS,
        PermissionAction.MANAGE_CONNECTIONS,
      ])
    ).toBe(false);
  });
});

