import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { of } from 'rxjs';
import { vi } from 'vitest';
import { UserManagementComponent } from './user-management.component';
import { UserManagementService } from '../services/user-management.service';
import { RoleManagementService } from '../services/role-management.service';
import { GroupManagementService } from '../services/group-management.service';

describe('UserManagementComponent', () => {
  let component: UserManagementComponent;
  let fixture: ComponentFixture<UserManagementComponent>;
  let userService: UserManagementService;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [UserManagementComponent],
      providers: [
        UserManagementService,
        RoleManagementService,
        GroupManagementService,
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    }).compileComponents();

    userService = TestBed.inject(UserManagementService);
    const roleService = TestBed.inject(RoleManagementService);
    const groupService = TestBed.inject(GroupManagementService);

    vi.spyOn(userService, 'getUsers').mockReturnValue(
      of({
        items: [
          {
            id: 'u-1',
            username: 'admin',
            email: 'admin@company.internal',
            full_name: 'Administrator',
            status: 'ACTIVE',
            is_superadmin: true,
            roles: ['SuperAdmin'],
            role_ids: ['r-1'],
            groups: ['DataOps'],
            group_ids: ['g-1'],
            created_at: '2026-11-14T00:00:00Z',
          },
        ],
        total: 1,
        page: 1,
        page_size: 10,
        total_pages: 1,
      })
    );

    vi.spyOn(roleService, 'getAllRolesList').mockReturnValue(of([]));
    vi.spyOn(groupService, 'getGroups').mockReturnValue(
      of({ items: [], total: 0, page: 1, page_size: 10, total_pages: 1 })
    );
    vi.spyOn(userService, 'getPhysicalTree').mockReturnValue(of([]));

    fixture = TestBed.createComponent(UserManagementComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create and load users grid', () => {
    expect(component).toBeTruthy();
    expect(component.users().length).toBe(1);
    expect(component.users()[0].username).toBe('admin');
  });

  it('should select user and load details', () => {
    vi.spyOn(userService, 'getUserDetails').mockReturnValue(
      of({
        user: component.users()[0],
        roles_detail: [],
        groups_detail: [],
        direct_permissions_count: 0,
        activity_count: 0,
      })
    );
    vi.spyOn(userService, 'getUserDirectPermissions').mockReturnValue(of([]));

    component.selectUser(component.users()[0]);
    expect(component.selectedUser()?.username).toBe('admin');
    expect(userService.getUserDetails).toHaveBeenCalledWith('u-1');
  });

  it('should switch tabs', () => {
    component.selectedUser.set(component.users()[0]);
    vi.spyOn(userService, 'getUserEffectivePermissions').mockReturnValue(of([]));

    component.switchTab('effective');
    expect(component.activeTab()).toBe('effective');
    expect(userService.getUserEffectivePermissions).toHaveBeenCalled();
  });
});
