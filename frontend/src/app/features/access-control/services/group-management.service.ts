import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import {
  DirectPermissionRule,
  GroupCreateDto,
  GroupItem,
  GroupMemberItem,
  GroupUpdateDto,
  PaginatedResponse,
} from '../models/access-control.models';

@Injectable({
  providedIn: 'root',
})
export class GroupManagementService {
  private http = inject(HttpClient);
  private baseUrl = '/api/v1/groups';

  getGroups(params: {
    page?: number;
    page_size?: number;
    search?: string;
    is_active?: boolean;
  }): Observable<PaginatedResponse<GroupItem>> {
    let httpParams = new HttpParams();
    if (params.page !== undefined) httpParams = httpParams.set('page', params.page);
    if (params.page_size !== undefined) httpParams = httpParams.set('page_size', params.page_size);
    if (params.search) httpParams = httpParams.set('search', params.search);
    if (params.is_active !== undefined) httpParams = httpParams.set('is_active', params.is_active);

    return this.http.get<PaginatedResponse<GroupItem>>(this.baseUrl, { params: httpParams });
  }

  getGroup(id: string): Observable<GroupItem> {
    return this.http.get<GroupItem>(`${this.baseUrl}/${id}`);
  }

  createGroup(dto: GroupCreateDto): Observable<GroupItem> {
    return this.http.post<GroupItem>(this.baseUrl, dto);
  }

  updateGroup(id: string, dto: GroupUpdateDto): Observable<GroupItem> {
    return this.http.put<GroupItem>(`${this.baseUrl}/${id}`, dto);
  }

  deleteGroup(id: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/${id}`);
  }

  getGroupMembers(groupId: string): Observable<GroupMemberItem[]> {
    return this.http.get<GroupMemberItem[]>(`${this.baseUrl}/${groupId}/members`);
  }

  updateGroupMembers(groupId: string, userIds: string[]): Observable<GroupMemberItem[]> {
    return this.http.put<GroupMemberItem[]>(`${this.baseUrl}/${groupId}/members`, {
      user_ids: userIds,
    });
  }

  addGroupMember(groupId: string, userId: string): Observable<void> {
    return this.http.post<void>(`${this.baseUrl}/${groupId}/members/${userId}`, {});
  }

  removeGroupMember(groupId: string, userId: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/${groupId}/members/${userId}`);
  }

  getGroupRoles(groupId: string): Observable<any[]> {
    return this.http.get<any[]>(`${this.baseUrl}/${groupId}/roles`);
  }

  updateGroupRoles(groupId: string, roleIds: string[]): Observable<any[]> {
    return this.http.put<any[]>(`${this.baseUrl}/${groupId}/roles`, { role_ids: roleIds });
  }

  getGroupPermissions(groupId: string): Observable<DirectPermissionRule[]> {
    return this.http.get<DirectPermissionRule[]>(`${this.baseUrl}/${groupId}/permissions`);
  }

  bulkUpdateGroupPermissions(
    groupId: string,
    permissions: { resource_type: string; resource_id: string; action: string; effect: string }[]
  ): Observable<DirectPermissionRule[]> {
    return this.http.post<DirectPermissionRule[]>(`${this.baseUrl}/${groupId}/permissions/bulk`, {
      permissions,
    });
  }
}
