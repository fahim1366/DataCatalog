import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import {
  ConnectionCreateRequest,
  ConnectionTestResult,
  DatabaseConnection,
} from '../models/connection.models';

@Injectable({
  providedIn: 'root',
})
export class ConnectionService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = '/api/v1/connections';

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
   * Executes live dialect connection ping test with 5s timeout.
   */
  testConnection(id: string): Observable<ConnectionTestResult> {
    return this.http.post<ConnectionTestResult>(`${this.baseUrl}/${id}/test`, {});
  }
}

