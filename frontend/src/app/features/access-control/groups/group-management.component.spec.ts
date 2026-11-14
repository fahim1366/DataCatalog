import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { of } from 'rxjs';
import { vi } from 'vitest';
import { GroupManagementComponent } from './group-management.component';
import { GroupManagementService } from '../services/group-management.service';
import { RoleManagementService } from '../services/role-management.service';
import { UserManagementService } from '../services/user-management.service';

describe('GroupManagementComponent', () => {
  let component: GroupManagementComponent;
  let fixture: ComponentFixture<GroupManagementComponent>;
  let groupService: GroupManagementService;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [GroupManagementComponent],
      providers: [
        GroupManagementService,
        RoleManagementService,
        UserManagementService,
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    }).compileComponents();

    groupService = TestBed.inject(GroupManagementService);
    const roleService = TestBed.inject(RoleManagementService);
    const userService = TestBed.inject(UserManagementService);

    vi.spyOn(groupService, 'getGroups').mockReturnValue(
      of({
        items: [
          {
            id: 'g-1',
            name: 'Data Engineering',
            description: 'Data Ops and Pipelines',
            is_active: true,
            user_count: 2,
            role_count: 1,
            roles: ['DataSteward'],
            role_ids: ['r-1'],
            created_at: '2026-11-14T00:00:00Z',
            updated_at: '2026-11-14T00:00:00Z',
          },
        ],
        total: 1,
        page: 1,
        page_size: 10,
        total_pages: 1,
      })
    );

    vi.spyOn(roleService, 'getAllRolesList').mockReturnValue(of([]));
    vi.spyOn(userService, 'getUsers').mockReturnValue(
      of({ items: [], total: 0, page: 1, page_size: 10, total_pages: 1 })
    );

    fixture = TestBed.createComponent(GroupManagementComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create and load groups grid', () => {
    expect(component).toBeTruthy();
    expect(component.groups().length).toBe(1);
    expect(component.groups()[0].name).toBe('Data Engineering');
  });

  it('should open create group modal', () => {
    component.openCreateModal();
    expect(component.showCreateModal()).toBe(true);
  });
});
