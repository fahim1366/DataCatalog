import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import {
  DirectPermissionRule,
  PaginatedResponse,
  PermissionModule,
  RoleCreateDto,
  RoleItem,
  RoleUpdateDto,
  RoleUserItem,
} from '../models/access-control.models';

@Injectable({
  providedIn: 'root',
})
export class RoleManagementService {
  private http = inject(HttpClient);
  private baseUrl = '/api/v1/roles';

  getRoles(params: {
    page?: number;
    page_size?: number;
    search?: string;
  }): Observable<PaginatedResponse<RoleItem>> {
    let httpParams = new HttpParams();
    if (params.page !== undefined) httpParams = httpParams.set('page', params.page);
    if (params.page_size !== undefined) httpParams = httpParams.set('page_size', params.page_size);
    if (params.search) httpParams = httpParams.set('search', params.search);

    return this.http.get<PaginatedResponse<RoleItem>>(this.baseUrl, { params: httpParams });
  }

  getAllRolesList(): Observable<RoleItem[]> {
    return this.http.get<RoleItem[]>(this.baseUrl);
  }

  getRole(id: string): Observable<RoleItem> {
    return this.http.get<RoleItem>(`${this.baseUrl}/${id}`);
  }

  createRole(dto: RoleCreateDto): Observable<RoleItem> {
    return this.http.post<RoleItem>(this.baseUrl, dto);
  }

  updateRole(id: string, dto: RoleUpdateDto): Observable<RoleItem> {
    return this.http.put<RoleItem>(`${this.baseUrl}/${id}`, dto);
  }

  deleteRole(id: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/${id}`);
  }

  getPermissionsRegistry(): Observable<PermissionModule[]> {
    return this.http.get<PermissionModule[]>(`${this.baseUrl}/permissions-registry`);
  }

  getRoleUsers(roleId: string): Observable<RoleUserItem[]> {
    return this.http.get<RoleUserItem[]>(`${this.baseUrl}/${roleId}/users`);
  }

  getRolePermissions(roleId: string): Observable<DirectPermissionRule[]> {
    return this.http.get<DirectPermissionRule[]>(`${this.baseUrl}/${roleId}/permissions`);
  }

  updateRolePermissions(
    roleId: string,
    permissions: { resource_type: string; resource_id?: string; action: string; effect: string }[]
  ): Observable<DirectPermissionRule[]> {
    return this.http.put<DirectPermissionRule[]>(`${this.baseUrl}/${roleId}/permissions`, {
      permissions,
    });
  }
}
