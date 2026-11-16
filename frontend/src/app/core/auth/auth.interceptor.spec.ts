import { TestBed } from '@angular/core/testing';
import { HttpClient, HttpErrorResponse, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Router } from '@angular/router';
import { authInterceptor } from './auth.interceptor';
import { AuthService } from './auth.service';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';

describe('authInterceptor', () => {
  let httpClient: HttpClient;
  let httpTestingController: HttpTestingController;
  let authServiceSpy: { getToken: ReturnType<typeof vi.fn>; isTokenExpired: ReturnType<typeof vi.fn>; logout: ReturnType<typeof vi.fn> };
  let routerSpy: { navigate: ReturnType<typeof vi.fn> };

  beforeEach(() => {
    authServiceSpy = {
      getToken: vi.fn(),
      isTokenExpired: vi.fn(),
      logout: vi.fn(),
    };
    routerSpy = {
      navigate: vi.fn().mockResolvedValue(true),
    };

    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
        { provide: AuthService, useValue: authServiceSpy },
        { provide: Router, useValue: routerSpy },
      ],
    });

    httpClient = TestBed.inject(HttpClient);
    httpTestingController = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpTestingController.verify();
  });

  it('should add Bearer token to request when valid token exists', () => {
    authServiceSpy.getToken.mockReturnValue('valid-token-123');
    authServiceSpy.isTokenExpired.mockReturnValue(false);

    httpClient.get('/api/v1/test').subscribe();

    const req = httpTestingController.expectOne('/api/v1/test');
    expect(req.request.headers.get('Authorization')).toBe('Bearer valid-token-123');
    req.flush({});
  });

  it('should logout and redirect to /login on 401 Unauthorized', () => {
    authServiceSpy.getToken.mockReturnValue(null);

    httpClient.get('/api/v1/test').subscribe({
      next: () => expect.fail('should have failed with 401'),
      error: (error: HttpErrorResponse) => {
        expect(error.status).toBe(401);
      },
    });

    const req = httpTestingController.expectOne('/api/v1/test');
    req.flush('Unauthorized', { status: 401, statusText: 'Unauthorized' });

    expect(authServiceSpy.logout).toHaveBeenCalledTimes(1);
    expect(routerSpy.navigate).toHaveBeenCalledWith(['/login']);
  });

  it('should NOT logout and NOT redirect to /login on 403 Forbidden, but propagate error', () => {
    authServiceSpy.getToken.mockReturnValue('valid-token');
    authServiceSpy.isTokenExpired.mockReturnValue(false);

    let errorReceived: any = null;
    httpClient.get('/api/v1/protected').subscribe({
      next: () => expect.fail('should have failed with 403'),
      error: (error) => {
        errorReceived = error;
      },
    });

    const req = httpTestingController.expectOne('/api/v1/protected');
    req.flush('Forbidden', { status: 403, statusText: 'Forbidden' });

    expect(authServiceSpy.logout).not.toHaveBeenCalled();
    expect(routerSpy.navigate).not.toHaveBeenCalled();
    expect(errorReceived).not.toBeNull();
    expect(errorReceived.status).toBe(403);
  });
});
