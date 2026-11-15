
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { vi } from 'vitest';
import { UserManagementComponent } from './user-management.component';
import { UserManagementService } from '../services/user-management.service';
import { RoleManagementService } from '../services/role-management.service';

describe('UserManagementComponent', () => {
  let component: UserManagementComponent;
  let fixture: ComponentFixture<UserManagementComponent>;
  let userService: UserManagementService;
  let roleService: RoleManagementService;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [UserManagementComponent],
      providers: [
        UserManagementService,
        RoleManagementService,
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
      ],
    }).compileComponents();

    userService = TestBed.inject(UserManagementService);
    roleService = TestBed.inject(RoleManagementService);

    vi.spyOn(userService, 'getUsers').mockReturnValue(
      of({
        items: [
          {
            id: 'u-1',
            username: 'admin',
            email: 'admin@company.internal',
            full_name: 'Test User',
            phone: '00000000000',
            status: 'ACTIVE',
            is_superadmin: true,
            roles: ['System Administrator'],
            role_ids: ['r-1'],
            groups: [],
            group_ids: [],
            created_at: '2026-11-14T00:00:00Z',
          },
        ],
        total: 1,
        page: 1,
        page_size: 10,
        total_pages: 1,
      })
    );

    vi.spyOn(roleService, 'getAllRolesList').mockReturnValue(
      of([
        {
          id: 'r-1',
          name: 'SuperAdmin',
          description: 'System Administrator',
          is_system_role: true,
          user_count: 1,
          created_at: '2026-11-14T00:00:00Z',
        },
      ])
    );

    vi.spyOn(userService, 'getPhysicalTree').mockReturnValue(
      of([
        {
          id: 'db-1',
          name: 'PostgreSQL DW',
          database_name: 'analytics_dw',
          engine_type: 'POSTGRESQL',
          schemas: [
            {
              id: 'sch-1',
              name: 'public',
              table_count: 5,
              tables: [],
            },
          ],
        },
      ])
    );

    vi.spyOn(userService, 'getUserAssignedDatabases').mockReturnValue(
      of(['db-1'])
    );

    fixture = TestBed.createComponent(UserManagementComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create and load users grid with first name and last name split', () => {
    expect(component).toBeTruthy();
    expect(component.rawUsers().length).toBe(1);

    const viewRows = component.filteredUsers();

    expect(viewRows.length).toBe(1);
    expect(viewRows[0].firstName).toBe('Test');
    expect(viewRows[0].lastName).toBe('User');
    expect(viewRows[0].user.username).toBe('admin');
  });

  it('should select user in read-only mode by default and switch to edit mode on enableEditMode', () => {
    const user = component.rawUsers()[0];
    component.selectUser(user);

    expect(component.selectedUser()?.username).toBe('admin');
    expect(component.formFirstName()).toBe('Test');
    expect(component.formLastName()).toBe('User');
    expect(component.isCreateMode()).toBe(false);
    expect(component.isEditMode()).toBe(false);
    expect(component.isFormEditable()).toBe(false);

    component.enableEditMode();
    expect(component.isEditMode()).toBe(true);
    expect(component.isFormEditable()).toBe(true);

    component.cancelDetailEdit();
    expect(component.isEditMode()).toBe(false);
    expect(component.isFormEditable()).toBe(false);
  });

  it('should start edit user when edit icon is clicked on a row', () => {
    const user = component.rawUsers()[0];
    const event = { stopPropagation: vi.fn() } as any;
    component.startEditUser(user, event);

    expect(event.stopPropagation).toHaveBeenCalled();
    expect(component.selectedUser()?.username).toBe('admin');
    expect(component.isEditMode()).toBe(true);
    expect(component.isFormEditable()).toBe(true);
  });

  it('should toggle database selection and update status when in edit mode', () => {
    component.enableEditMode();
    const event = { target: { checked: false } } as any;
    component.toggleDatabaseSelection('db-1', event);

    expect(component.isDatabaseSelected('db-1')).toBe(false);

    const event2 = { target: { checked: true } } as any;
    component.toggleDatabaseSelection('db-1', event2);

    expect(component.isDatabaseSelected('db-1')).toBe(true);
  });

  it('should switch to create new user mode', () => {
    component.startCreateNewUser();

    expect(component.isCreateMode()).toBe(true);
    expect(component.selectedUser()).toBeNull();
    expect(component.formUsername()).toBe('');
    expect(component.formFirstName()).toBe('');
  });

  it('should call updateUser, updateUserAssignedDatabases, and bulkUpdateUserPermissions on saveUser in edit mode', () => {
    const user = component.rawUsers()[0];
    component.selectUser(user);
    component.enableEditMode();

    const updateSpy = vi.spyOn(userService, 'updateUser').mockReturnValue(of(user));
    const assignDbSpy = vi.spyOn(userService, 'updateUserAssignedDatabases').mockReturnValue(of(['db-1']));
    const bulkPermSpy = vi.spyOn(userService, 'bulkUpdateUserPermissions').mockReturnValue(of([]));

    component.formFirstName.set('Ali');
    component.formLastName.set('Rezaei');
    component.formEmail.set('ali.rezaei@company.internal');
    component.userDirectPermissions.set(new Set(['MANAGE_USERS', 'MANAGE_ROLES']));

    component.saveUser();

    expect(updateSpy).toHaveBeenCalledWith('u-1', {
      username: 'admin',
      full_name: 'Ali Rezaei',
      email: 'ali.rezaei@company.internal',
      phone: '00000000000',
      role_ids: ['r-1'],
    });

    expect(assignDbSpy).toHaveBeenCalledWith('u-1', ['db-1']);
    expect(bulkPermSpy).toHaveBeenCalledWith('u-1', expect.arrayContaining([
      expect.objectContaining({ resource_type: 'GLOBAL', resource_id: null, effect: 'ALLOW' }),
    ]));
    expect(component.isSaving()).toBe(false);
  });

  it('should successfully create a new user with empty phone number', () => {
    component.startCreateNewUser();
    expect(component.isCreateMode()).toBe(true);
    expect(component.formPhone()).toBe('');

    component.formFirstName.set('Sara');
    component.formLastName.set('Ahmadi');
    component.formUsername.set('sara_ahmadi');
    component.formEmail.set('sara@company.internal');
    component.formPassword.set('SecurePass123!');
    // phone is left intentionally empty
    component.formPhone.set('');

    const createdUser = {
      id: 'u-new',
      username: 'sara_ahmadi',
      email: 'sara@company.internal',
      full_name: 'Sara Ahmadi',
      status: 'ACTIVE' as const,
      is_superadmin: false,
      roles: [],
      role_ids: [],
      groups: [],
      group_ids: [],
      created_at: '2026-11-15T00:00:00Z',
    };

    const createSpy = vi.spyOn(userService, 'createUser').mockReturnValue(of(createdUser));
    const assignDbSpy = vi.spyOn(userService, 'updateUserAssignedDatabases').mockReturnValue(of([]));
    const bulkPermSpy = vi.spyOn(userService, 'bulkUpdateUserPermissions').mockReturnValue(of([]));

    component.saveUser();

    expect(createSpy).toHaveBeenCalledWith({
      username: 'sara_ahmadi',
      email: 'sara@company.internal',
      full_name: 'Sara Ahmadi',
      phone: undefined,
      password: 'SecurePass123!',
      role_ids: [],
    });
    expect(assignDbSpy).toHaveBeenCalledWith('u-new', []);
    expect(component.isSaving()).toBe(false);
    expect(component.successMessage()).toContain('با موفقیت تعریف');
  });

  it('should generate a random password', () => {
    component.generateRandomPassword();
    expect(component.formPassword().length).toBeGreaterThanOrEqual(8);
  });

  it('should show error when required fields are missing on saveUser', () => {
    component.formFirstName.set('');
    component.formLastName.set('');
    component.formUsername.set('');

    component.saveUser();

    expect(component.errorMessage()).toContain('لطفاً');
  });
});
