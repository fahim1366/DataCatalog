import { Component, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { AuthService, AUTH_STORAGE } from '../auth/auth.service';
import { RbacService, PermissionAction } from '../auth/rbac.service';
import { HasPermissionDirective } from './has-permission.directive';

function createMockJwt(payload: Record<string, unknown>): string {
  const header = { alg: 'HS256', typ: 'JWT' };
  const base64Url = (obj: unknown) => {
    const json = JSON.stringify(obj);
    return btoa(json).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');
  };
  return `${base64Url(header)}.${base64Url(payload)}.mock_signature`;
}

@Component({
  standalone: true,
  imports: [HasPermissionDirective],
  template: `
    <div id="default-container">
      <button *hasPermission="'EDIT_DESCRIPTION'" id="btn-edit">
        Edit Description
      </button>

      <button *hasPermission="'MANAGE_CONNECTIONS'" id="btn-conn">
        Manage Connections
      </button>

      <div *hasPermission="['VIEW_METADATA', 'MANAGE_TAGS']" id="tags-panel">
        Tags Panel
      </div>

      <div *hasPermission="'VIEW_AUDIT_LOGS'; else fallbackTpl" id="audit-panel">
        Audit Logs Allowed
      </div>
      <ng-template #fallbackTpl>
        <span id="audit-denied">Access Denied to Audit Logs</span>
      </ng-template>

      <div *hasPermission="dynamicAction()" id="dynamic-action-block">
        Dynamic Block
      </div>
    </div>
  `,
})
class TestHostComponent {
  public dynamicAction = signal<string>('VIEW_METADATA');
}

describe('HasPermissionDirective (T022)', () => {
  let fixture: ComponentFixture<TestHostComponent>;
  let hostComponent: TestHostComponent;
  let authService: AuthService;
  let rbacService: RbacService;
  let storage: Storage;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHostComponent, HasPermissionDirective],
      providers: [AuthService, RbacService],
    }).compileComponents();

    storage = TestBed.inject(AUTH_STORAGE);
    storage.clear();

    authService = TestBed.inject(AuthService);
    rbacService = TestBed.inject(RbacService);
    fixture = TestBed.createComponent(TestHostComponent);
    hostComponent = fixture.componentInstance;
  });

  afterEach(() => {
    storage.clear();
  });

  it('should remove elements from DOM when user is not authenticated or lacks permission', () => {
    authService.logout();
    fixture.detectChanges();

    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('#btn-edit')).toBeNull();
    expect(compiled.querySelector('#btn-conn')).toBeNull();
    expect(compiled.querySelector('#tags-panel')).toBeNull();
    expect(compiled.querySelector('#audit-panel')).toBeNull();
    // Fallback template should be rendered
    expect(compiled.querySelector('#audit-denied')).not.toBeNull();
    expect(compiled.querySelector('#audit-denied')?.textContent).toContain(
      'Access Denied to Audit Logs'
    );
  });

  it('should render allowed elements and omit disallowed elements based on user role', () => {
    // DATA_STEWARD has VIEW_METADATA, EDIT_DESCRIPTION, MANAGE_TAGS, EXPORT_REPORTS
    // does not have MANAGE_CONNECTIONS or VIEW_AUDIT_LOGS
    const token = createMockJwt({
      sub: 'steward-1',
      username: 'data_steward',
      email: 'steward@datacatalog.com',
      roles: ['DATA_STEWARD'],
      is_superadmin: false,
      exp: Math.floor(Date.now() / 1000) + 3600,
    });
    authService.setToken(token);
    fixture.detectChanges();

    const compiled = fixture.nativeElement as HTMLElement;
    // Allowed
    expect(compiled.querySelector('#btn-edit')).not.toBeNull();
    expect(compiled.querySelector('#tags-panel')).not.toBeNull();

    // Disallowed
    expect(compiled.querySelector('#btn-conn')).toBeNull();
    expect(compiled.querySelector('#audit-panel')).toBeNull();
    expect(compiled.querySelector('#audit-denied')).not.toBeNull();
  });

  it('should render all elements when user is superadmin', () => {
    const token = createMockJwt({
      sub: 'admin-1',
      username: 'superadmin',
      email: 'admin@datacatalog.com',
      roles: ['SUPERADMIN'],
      is_superadmin: true,
      exp: Math.floor(Date.now() / 1000) + 3600,
    });
    authService.setToken(token);
    fixture.detectChanges();

    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('#btn-edit')).not.toBeNull();
    expect(compiled.querySelector('#btn-conn')).not.toBeNull();
    expect(compiled.querySelector('#tags-panel')).not.toBeNull();
    expect(compiled.querySelector('#audit-panel')).not.toBeNull();
    expect(compiled.querySelector('#audit-denied')).toBeNull();
  });

  it('should reactively update DOM when authentication state changes via Signals', () => {
    // 1. Initially unauthenticated
    authService.logout();
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('#btn-edit')).toBeNull();

    // 2. User logs in with steward role
    const token = createMockJwt({
      sub: 'steward-2',
      username: 'steward2',
      email: 'steward2@datacatalog.com',
      roles: ['DATA_STEWARD'],
      is_superadmin: false,
      exp: Math.floor(Date.now() / 1000) + 3600,
    });
    authService.setToken(token);
    fixture.detectChanges();

    // DOM should immediately update to render #btn-edit
    expect(compiled.querySelector('#btn-edit')).not.toBeNull();

    // 3. User logs out
    authService.logout();
    fixture.detectChanges();

    // DOM should immediately clear #btn-edit
    expect(compiled.querySelector('#btn-edit')).toBeNull();
  });

  it('should reactively update DOM when dynamic action signal changes', () => {
    // VIEWER role only has VIEW_METADATA
    const token = createMockJwt({
      sub: 'viewer-1',
      username: 'viewer',
      email: 'viewer@datacatalog.com',
      roles: ['VIEWER'],
      is_superadmin: false,
      exp: Math.floor(Date.now() / 1000) + 3600,
    });
    authService.setToken(token);
    fixture.detectChanges();

    const compiled = fixture.nativeElement as HTMLElement;
    // Initially dynamicAction is VIEW_METADATA, which viewer has
    expect(compiled.querySelector('#dynamic-action-block')).not.toBeNull();

    // Switch action signal to MANAGE_CONNECTIONS, which viewer lacks
    hostComponent.dynamicAction.set('MANAGE_CONNECTIONS');
    fixture.detectChanges();

    expect(compiled.querySelector('#dynamic-action-block')).toBeNull();
  });
});

