import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import {
  DirectPermissionRule,
  EffectivePermissionRow,
  PaginatedResponse,
  PhysicalDatabaseNode,
  UserActivityItem,
  UserCreateDto,
  UserDetailData,
  UserItem,
  UserUpdateDto,
} from '../models/access-control.models';

@Injectable({
  providedIn: 'root',
})
export class UserManagementService {
  private http = inject(HttpClient);
  private baseUrl = '/api/v1/users';

  getUsers(params: {
    page?: number;
    page_size?: number;
    search?: string;
    role_id?: string;
    group_id?: string;
    status?: string;
  }): Observable<PaginatedResponse<UserItem>> {
    let httpParams = new HttpParams();
    if (params.page !== undefined) httpParams = httpParams.set('page', params.page);
    if (params.page_size !== undefined) httpParams = httpParams.set('page_size', params.page_size);
    if (params.search) httpParams = httpParams.set('search', params.search);
    if (params.role_id) httpParams = httpParams.set('role_id', params.role_id);
    if (params.group_id) httpParams = httpParams.set('group_id', params.group_id);
    if (params.status) httpParams = httpParams.set('status', params.status);

    return this.http.get<PaginatedResponse<UserItem>>(this.baseUrl, { params: httpParams });
  }

  getUser(id: string): Observable<UserItem> {
    return this.http.get<UserItem>(`${this.baseUrl}/${id}`);
  }

  getUserDetails(id: string): Observable<UserDetailData> {
    return this.http.get<UserDetailData>(`${this.baseUrl}/${id}/details`);
  }

  createUser(dto: UserCreateDto): Observable<UserItem> {
    return this.http.post<UserItem>(this.baseUrl, dto);
  }

  updateUser(id: string, dto: UserUpdateDto): Observable<UserItem> {
    return this.http.put<UserItem>(`${this.baseUrl}/${id}`, dto);
  }

  updateUserStatus(id: string, status: string): Observable<UserItem> {
    return this.http.patch<UserItem>(`${this.baseUrl}/${id}/status`, { status });
  }

  resetPassword(id: string, newPassword: string): Observable<{ message: string }> {
    return this.http.post<{ message: string }>(`${this.baseUrl}/${id}/reset-password`, {
      new_password: newPassword,
    });
  }

  deleteUser(id: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/${id}`);
  }

  getUserGroups(id: string): Observable<any[]> {
    return this.http.get<any[]>(`${this.baseUrl}/${id}/groups`);
  }

  updateUserGroups(id: string, groupIds: string[]): Observable<any[]> {
    return this.http.put<any[]>(`${this.baseUrl}/${id}/groups`, { group_ids: groupIds });
  }

  getUserDirectPermissions(id: string): Observable<DirectPermissionRule[]> {
    return this.http.get<DirectPermissionRule[]>(`${this.baseUrl}/${id}/permissions`);
  }

  bulkUpdateUserPermissions(
    id: string,
    permissions: { resource_type: string; resource_id: string; action: string; effect: string }[]
  ): Observable<DirectPermissionRule[]> {
    return this.http.post<DirectPermissionRule[]>(`${this.baseUrl}/${id}/permissions/bulk`, {
      permissions,
    });
  }

  deleteUserPermissionRule(userId: string, ruleId: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/${userId}/permissions/${ruleId}`);
  }

  getUserEffectivePermissions(
    id: string,
    search?: string,
    action?: string
  ): Observable<EffectivePermissionRow[]> {
    let params = new HttpParams();
    if (search) params = params.set('search', search);
    if (action) params = params.set('action', action);
    return this.http.get<EffectivePermissionRow[]>(`${this.baseUrl}/${id}/effective-permissions`, {
      params,
    });
  }

  getUserActivity(id: string, limit: number = 50): Observable<UserActivityItem[]> {
    const params = new HttpParams().set('limit', limit);
    return this.http.get<UserActivityItem[]>(`${this.baseUrl}/${id}/activity`, { params });
  }

  getPhysicalTree(): Observable<PhysicalDatabaseNode[]> {
    return this.http.get<PhysicalDatabaseNode[]>('/api/v1/tables/tree/physical');
  }
}
