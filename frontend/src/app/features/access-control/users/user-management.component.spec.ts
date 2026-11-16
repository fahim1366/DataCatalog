
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

  it('should filter users by unified searchTerm and filterStatus', () => {
    component.searchTerm.set('admin');
    expect(component.filteredUsers().length).toBe(1);

    component.searchTerm.set('nonexistent');
    expect(component.filteredUsers().length).toBe(0);

    component.searchTerm.set('');
    component.filterStatus.set('ACTIVE');
    expect(component.filteredUsers().length).toBe(1);

    component.filterStatus.set('SUSPENDED');
    expect(component.filteredUsers().length).toBe(0);

    component.filterStatus.set('ALL');
    expect(component.filteredUsers().length).toBe(1);
  });

  it('should support cascading category database selection (checked, indeterminate, toggle)', () => {
    component.enableEditMode();

    const mockCategory = {
      id: 'cat-root',
      name: 'Financial Services',
      databases: [{ id: 'db-1', name: 'Postgres DB' }],
      children: [
        {
          id: 'cat-sub',
          name: 'Accounting',
          databases: [{ id: 'db-2', name: 'Oracle DB' }],
          children: [],
        },
      ],
    };

    // Initially only db-1 is selected (from beforeEach)
    component.formSelectedDatabaseIds.set(new Set(['db-1']));

    // Root category has db-1 and db-2 -> only 1 selected -> indeterminate
    expect(component.isCategoryChecked(mockCategory)).toBe(false);
    expect(component.isCategoryIndeterminate(mockCategory)).toBe(true);

    // Sub category has only db-2 -> 0 selected -> not checked, not indeterminate
    expect(component.isCategoryChecked(mockCategory.children[0])).toBe(false);
    expect(component.isCategoryIndeterminate(mockCategory.children[0])).toBe(false);

    // Toggle subcategory on
    const eventCheck = { target: { checked: true }, stopPropagation: vi.fn() } as any;
    component.toggleCategorySelection(mockCategory.children[0], eventCheck);

    expect(component.isDatabaseSelected('db-2')).toBe(true);
    expect(component.isCategoryChecked(mockCategory)).toBe(true);
    expect(component.isCategoryIndeterminate(mockCategory)).toBe(false);

    // Toggle root category off
    const eventUncheck = { target: { checked: false }, stopPropagation: vi.fn() } as any;
    component.toggleCategorySelection(mockCategory, eventUncheck);

    expect(component.isDatabaseSelected('db-1')).toBe(false);
    expect(component.isDatabaseSelected('db-2')).toBe(false);
    expect(component.isCategoryChecked(mockCategory)).toBe(false);
    expect(component.isCategoryIndeterminate(mockCategory)).toBe(false);
  });

  it('should include virtual uncategorized branch for databases without category assignment', () => {
    component.categoryTree.set([
      {
        id: 'cat-1',
        name: 'Finance',
        databases: [{ id: 'db-1', name: 'PostgreSQL DW' }],
        children: [],
      },
    ]);
    component.allConnections.set([
      { id: 'db-1', name: 'PostgreSQL DW', database_name: 'dw', engine_type: 'POSTGRESQL' },
      { id: 'db-2', name: 'Unassigned Mongo', database_name: 'mongo_raw', engine_type: 'MONGODB' },
    ]);

    const displayTree = component.displayCategoryTree();
    expect(displayTree.length).toBe(2);
    const uncatNode = displayTree.find((n) => n.id === 'uncategorized');
    expect(uncatNode).toBeDefined();
    expect(uncatNode?.name).toBe('پایگاه‌های داده دسته‌بندی‌نشده (Uncategorized)');
    expect(uncatNode?.databases.length).toBe(1);
    expect(uncatNode?.databases[0].id).toBe('db-2');
  });

  it('should manage in-app confirmation modal for deleting user without window.confirm', () => {
    const deleteSpy = vi.spyOn(userService, 'deleteUser').mockReturnValue(of(void 0 as any));
    const testUser = {
      id: 'u-99',
      username: 'johndoe',
      email: 'john@example.com',
      full_name: 'John Doe',
      status: 'ACTIVE' as const,
      is_superadmin: false,
      roles: [],
      role_ids: [],
      groups: [],
      group_ids: [],
      created_at: '2026-11-14T00:00:00Z',
    };

    expect(component.userToDelete()).toBeNull();

    // Trigger request
    component.requestDeleteUser(testUser);
    expect(component.userToDelete()).toEqual(testUser);

    // Cancel
    component.cancelDeleteUser();
    expect(component.userToDelete()).toBeNull();
    expect(deleteSpy).not.toHaveBeenCalled();

    // Confirm
    component.requestDeleteUser(testUser);
    component.confirmDeleteUser();
    expect(deleteSpy).toHaveBeenCalledWith('u-99');
    expect(component.userToDelete()).toBeNull();
  });
});
