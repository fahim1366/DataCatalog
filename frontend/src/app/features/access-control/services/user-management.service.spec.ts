import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { UserManagementService } from './user-management.service';

describe('UserManagementService', () => {
  let service: UserManagementService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        UserManagementService,
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    });
    service = TestBed.inject(UserManagementService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('should fetch users with pagination and search parameters', () => {
    const mockResponse = {
      items: [
        {
          id: '123e4567-e89b-12d3-a456-426614174000',
          username: 'admin',
          email: 'admin@company.internal',
          full_name: 'Administrator',
          status: 'ACTIVE' as const,
          is_superadmin: true,
          roles: ['SuperAdmin'],
          role_ids: [],
          groups: [],
          group_ids: [],
          created_at: '2026-11-14T00:00:00Z',
        },
      ],
      total: 1,
      page: 1,
      page_size: 10,
      total_pages: 1,
    };

    service.getUsers({ page: 1, page_size: 10, search: 'admin' }).subscribe((res) => {
      expect(res.items.length).toBe(1);
      expect(res.items[0].username).toBe('admin');
    });

    const req = httpMock.expectOne((r) => r.url === '/api/v1/users' && r.params.has('page') && r.params.get('search') === 'admin');
    expect(req.request.method).toBe('GET');
    req.flush(mockResponse);
  });

  it('should call reset-password endpoint', () => {
    service.resetPassword('user-123', 'Secret#2026').subscribe((res) => {
      expect(res.message).toBe('Password reset successfully');
    });

    const req = httpMock.expectOne('/api/v1/users/user-123/reset-password');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ new_password: 'Secret#2026' });
    req.flush({ message: 'Password reset successfully' });
  });

  it('should fetch user effective permissions', () => {
    service.getUserEffectivePermissions('user-123', 'orders', 'VIEW_METADATA').subscribe((res) => {
      expect(res.length).toBe(0);
    });

    const req = httpMock.expectOne((r) => r.url === '/api/v1/users/user-123/effective-permissions' && r.params.get('search') === 'orders');
    expect(req.request.method).toBe('GET');
    req.flush([]);
  });
});
