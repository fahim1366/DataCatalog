
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { vi } from 'vitest';
import { RoleManagementComponent } from './role-management.component';
import { RoleManagementService } from '../services/role-management.service';

describe('RoleManagementComponent', () => {
  let component: RoleManagementComponent;
  let fixture: ComponentFixture<RoleManagementComponent>;
  let roleService: RoleManagementService;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [RoleManagementComponent],
      providers: [
        RoleManagementService,
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
      ],
    }).compileComponents();

    roleService = TestBed.inject(RoleManagementService);

    vi.spyOn(roleService, 'getAllRolesList').mockReturnValue(
      of([
        {
          id: 'r-1',
          name: 'FieldInspector',
          description: 'Field Inspector',
          is_system_role: false,
          user_count: 2,
          created_at: '2026-11-14T00:00:00Z',
        },
      ])
    );

    vi.spyOn(roleService, 'getPermissionsRegistry').mockReturnValue(
      of([
        {
          module_key: 'CATALOG_METADATA',
          module_name: 'Data Catalog',
          description: 'Metadata access',
          permissions: [
            {
              action: 'VIEW_METADATA',
              name: 'View Metadata',
              description: 'View table structures',
            },
          ],
        },
      ])
    );

    vi.spyOn(roleService, 'getRolePermissions').mockReturnValue(
      of([
        {
          id: 'p-1',
          role_id: 'r-1',
          resource_type: 'GLOBAL',
          action: 'VIEW_METADATA',
          effect: 'ALLOW',
          created_at: '2026-11-14T00:00:00Z',
        },
      ])
    );

    vi.spyOn(roleService, 'getRoleUsers').mockReturnValue(
      of([
        {
          id: 'u-1',
          username: 'test_user',
          full_name: 'Test User',
          email: 'test.user@example.com',
          assignment_type: 'DIRECT',
        },
      ])
    );

    fixture = TestBed.createComponent(RoleManagementComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create and load roles list with Persian and English names', () => {
    expect(component).toBeTruthy();
    expect(component.rawRoles().length).toBe(1);
    expect(component.rawRoles()[0].name).toBe('FieldInspector');
    expect(component.rawRoles()[0].description).toBe('Field Inspector');
  });

  it('should select role and load permissions into detail panel in read-only mode by default', () => {
    const role = component.rawRoles()[0];
    component.selectRole(role);

    expect(component.selectedRole()?.name).toBe('FieldInspector');
    expect(component.formEnglishName()).toBe('FieldInspector');
    expect(component.formPersianName()).toBe('Field Inspector');
    expect(component.hasPermission('VIEW_METADATA')).toBe(true);
    expect(component.isEditMode()).toBe(false);
    expect(component.isFormEditable()).toBe(false);
  });

  it('should switch to edit mode on enableEditMode and startEditRole', () => {
    const role = component.rawRoles()[0];
    component.selectRole(role);
    expect(component.isFormEditable()).toBe(false);

    component.enableEditMode();
    expect(component.isEditMode()).toBe(true);
    expect(component.isFormEditable()).toBe(true);

    // Cancel restores read-only mode
    component.cancelDetailEdit();
    expect(component.isEditMode()).toBe(false);
    expect(component.isFormEditable()).toBe(false);

    // startEditRole switches directly to edit mode
    component.startEditRole(role);
    expect(component.isEditMode()).toBe(true);
    expect(component.isFormEditable()).toBe(true);
  });

  it('should not toggle permission selection when in read-only mode, and toggle when in edit mode', () => {
    const role = component.rawRoles()[0];
    component.selectRole(role);
    expect(component.isFormEditable()).toBe(false);

    const event = { target: { checked: false } } as any;
    component.togglePermission('VIEW_METADATA', event);
    // Should remain true because form is in read-only mode
    expect(component.hasPermission('VIEW_METADATA')).toBe(true);

    // Now enable edit mode
    component.enableEditMode();
    expect(component.isFormEditable()).toBe(true);

    component.togglePermission('VIEW_METADATA', event);
    expect(component.hasPermission('VIEW_METADATA')).toBe(false);

    const event2 = { target: { checked: true } } as any;
    component.togglePermission('VIEW_METADATA', event2);
    expect(component.hasPermission('VIEW_METADATA')).toBe(true);
  });

  it('should switch subtab to users and display assigned users', () => {
    component.switchSubTab('users');

    expect(component.activeSubTab()).toBe('users');
    expect(component.roleUsers().length).toBe(1);
    expect(component.roleUsers()[0].username).toBe('test_user');
  });

  it('should switch to create new role mode', () => {
    component.startCreateNewRole();

    expect(component.isCreateMode()).toBe(true);
    expect(component.selectedRole()).toBeNull();
    expect(component.formEnglishName()).toBe('');
    expect(component.formPersianName()).toBe('');
    expect(component.roleSelectedActions().size).toBe(0);
  });

  it('should filter roles using instant search input', () => {
    component.searchTerm.set('Inspector');
    expect(component.filteredRoles().length).toBe(1);

    component.searchTerm.set('SystemAdmin');
    expect(component.filteredRoles().length).toBe(0);

    component.searchTerm.set('');
    expect(component.filteredRoles().length).toBe(1);
  });

  it('should manage in-app confirmation modal for deleting role without window.confirm', () => {
    const deleteSpy = vi.spyOn(roleService, 'deleteRole').mockReturnValue(of(void 0 as any));
    const customRole = {
      id: 'r-custom',
      name: 'CustomAuditor',
      description: 'Custom Auditor Role',
      is_system_role: false,
      user_count: 0,
      created_at: '2026-11-14T00:00:00Z',
    };

    expect(component.roleToDelete()).toBeNull();

    // Request delete
    component.requestDeleteRole(customRole);
    expect(component.roleToDelete()).toEqual(customRole);

    // Cancel delete
    component.cancelDeleteRole();
    expect(component.roleToDelete()).toBeNull();
    expect(deleteSpy).not.toHaveBeenCalled();

    // Confirm delete
    component.requestDeleteRole(customRole);
    component.confirmDeleteRole();
    expect(deleteSpy).toHaveBeenCalledWith('r-custom');
    expect(component.roleToDelete()).toBeNull();
  });
});
