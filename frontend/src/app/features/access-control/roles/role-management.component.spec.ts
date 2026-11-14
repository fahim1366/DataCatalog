import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
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
      ],
    }).compileComponents();

    roleService = TestBed.inject(RoleManagementService);

    vi.spyOn(roleService, 'getAllRolesList').mockReturnValue(
      of([
        {
          id: 'r-1',
          name: 'SuperAdmin',
          description: 'Root platform admin',
          is_system_role: true,
          user_count: 1,
          created_at: '2026-11-14T00:00:00Z',
        },
      ])
    );
    vi.spyOn(roleService, 'getPermissionsRegistry').mockReturnValue(of([]));

    fixture = TestBed.createComponent(RoleManagementComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create and load roles list', () => {
    expect(component).toBeTruthy();
    expect(component.roles().length).toBe(1);
    expect(component.roles()[0].name).toBe('SuperAdmin');
  });

  it('should open create role modal', () => {
    component.openCreateModal();
    expect(component.showCreateModal()).toBe(true);
  });
});
