import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import {
  ConnectionCreateRequest,
  ConnectionTestResult,
  DatabaseConnection,
} from '../models/connection.models';
import { CategoryTreeNode } from '../../catalog/models/catalog.models';

@Injectable({
  providedIn: 'root',
})
export class ConnectionService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = '/api/v1/connections';

  /**
   * Retrieves business category hierarchy tree.
   */
  getCategoryTree(): Observable<CategoryTreeNode[]> {
    return this.http.get<CategoryTreeNode[]>('/api/v1/categories/tree');
  }

  /**
   * Retrieves all registered database connections with optional active filter.
   */
  getConnections(isActive?: boolean): Observable<DatabaseConnection[]> {
    let params = new HttpParams();
    if (isActive !== undefined) {
      params = params.set('is_active', isActive.toString());
    }
    return this.http.get<DatabaseConnection[]>(this.baseUrl, { params });
  }

  /**
   * Retrieves single database connection by ID.
   */
  getConnection(id: string): Observable<DatabaseConnection> {
    return this.http.get<DatabaseConnection>(`${this.baseUrl}/${id}`);
  }

  /**
   * Registers a new database connection with envelope encrypted credentials.
   */
  createConnection(request: ConnectionCreateRequest): Observable<DatabaseConnection> {
    return this.http.post<DatabaseConnection>(this.baseUrl, request);
  }

  /**
   * Updates an existing database connection.
   */
  updateConnection(id: string, request: Partial<ConnectionCreateRequest>): Observable<DatabaseConnection> {
    return this.http.put<DatabaseConnection>(`${this.baseUrl}/${id}`, request);
  }

  /**
   * Deletes a database connection.
   */
  deleteConnection(id: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/${id}`);
  }

  /**
   * Executes live dialect connection ping test with 5s timeout.
   */
  testConnection(id: string): Observable<ConnectionTestResult> {
    return this.http.post<ConnectionTestResult>(`${this.baseUrl}/${id}/test`, {});
  }

  /**
   * Auto-discovers non-template databases from host.
   */
  discoverDatabases(payload: {
    engine_type: string;
    host: string;
    port: number;
    credentials: { username: string; password?: string };
    custom_params?: any;
  }): Observable<{ success: boolean; databases: string[]; latency_ms: number; server_version?: string; message: string }> {
    return this.http.post<{ success: boolean; databases: string[]; latency_ms: number; server_version?: string; message: string }>(
      `${this.baseUrl}/discover-databases`,
      payload
    );
  }

  /**
   * Executes live diagnostic connectivity test on unsaved connection payload before saving.
   */
  testUnsavedConnection(payload: {
    engine_type: string;
    host: string;
    port: number;
    database_name: string;
    credentials: { username: string; password?: string };
    custom_params?: any;
  }): Observable<ConnectionTestResult> {
    return this.http.post<ConnectionTestResult>(`${this.baseUrl}/test-unsaved`, payload);
  }
}

