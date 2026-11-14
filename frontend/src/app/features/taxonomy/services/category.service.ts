import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { CategoryTreeNode } from '../../catalog/models/catalog.models';

export interface CategoryResponse {
  id: string;
  name: string;
  description?: string | null;
  parent_id?: string | null;
  status: 'ACTIVE' | 'INACTIVE';
  assigned_tables_count: number;
}

export interface CategoryCreateRequest {
  name: string;
  description?: string | null;
  parent_id?: string | null;
  status?: 'ACTIVE' | 'INACTIVE';
}

export interface CategoryUpdateRequest {
  name: string;
  description?: string | null;
  parent_id?: string | null;
  status?: 'ACTIVE' | 'INACTIVE';
}

export interface CategoryAssignmentItem {
  id: string;
  category_id: string;
  category_name?: string;
  entity_type: string;
  entity_id: string;
  table_name?: string;
  schema_name?: string;
  database_name?: string;
  created_at?: string;
}

export interface AvailableTableItem {
  id: string;
  name: string;
  schema_name: string;
  database_name: string;
  row_count_estimate?: number;
}

@Injectable({
  providedIn: 'root',
})
export class CategoryService {
  private http = inject(HttpClient);
  private readonly baseUrl = '/api/v1/categories';

  getCategoryTree(): Observable<CategoryTreeNode[]> {
    return this.http.get<CategoryTreeNode[]>(`${this.baseUrl}/tree`);
  }

  listCategories(): Observable<CategoryResponse[]> {
    return this.http.get<CategoryResponse[]>(this.baseUrl);
  }

  createCategory(payload: CategoryCreateRequest): Observable<CategoryResponse> {
    return this.http.post<CategoryResponse>(this.baseUrl, payload);
  }

  updateCategory(id: string, payload: CategoryUpdateRequest): Observable<CategoryResponse> {
    return this.http.put<CategoryResponse>(`${this.baseUrl}/${id}`, payload);
  }

  deleteCategory(id: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/${id}`);
  }

  getAssignments(categoryId?: string, entityType = 'TABLE'): Observable<CategoryAssignmentItem[]> {
    let params = new HttpParams();
    if (categoryId) {
      params = params.set('category_id', categoryId);
    }
    if (entityType) {
      params = params.set('entity_type', entityType);
    }
    return this.http.get<CategoryAssignmentItem[]>(`${this.baseUrl}/assignments`, { params });
  }

  assignTable(categoryId: string, tableId: string): Observable<any> {
    return this.http.post(`${this.baseUrl}/assignments`, {
      category_id: categoryId,
      entity_type: 'TABLE',
      entity_id: tableId,
    });
  }

  unassignTable(categoryId: string, tableId: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/assignments/${categoryId}/TABLE/${tableId}`);
  }

  getAvailableTables(search?: string): Observable<AvailableTableItem[]> {
    let params = new HttpParams();
    if (search && search.trim()) {
      params = params.set('search', search.trim());
    }
    return this.http.get<AvailableTableItem[]>(`${this.baseUrl}/available-tables`, { params });
  }
}
