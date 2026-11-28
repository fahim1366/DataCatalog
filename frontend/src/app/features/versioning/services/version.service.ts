import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, map } from 'rxjs';
import {
  DatabaseSnapshotPoint,
  DatabaseVersionDiffResponse,
  TableVersionItem,
  VersionDiffResponse,
} from '../models/version.models';
import { CategoryTreeNode } from '../../catalog/models/catalog.models';
import { DatabaseConnection } from '../../connections/models/connection.models';

@Injectable({
  providedIn: 'root',
})
export class VersionService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = '/api/v1/tables';

  /**
   * Retrieves all authorized catalog tables for version comparison.
   */
  getTables(): Observable<Array<{ id: string; name: string; schema_name?: string }>> {
    return this.http.get<{ items: Array<{ id: string; name: string; schema_name?: string }> }>('/api/v1/search?q=').pipe(
      map(res => res?.items || [])
    );
  }

  /**
   * Retrieves all database connections for database-centric version comparison.
   */
  getConnections(): Observable<DatabaseConnection[]> {
    return this.http.get<DatabaseConnection[]>('/api/v1/connections');
  }

  /**
   * Retrieves hierarchical category tree with assigned databases.
   */
  getCategoryTree(): Observable<CategoryTreeNode[]> {
    return this.http.get<CategoryTreeNode[]>('/api/v1/categories/tree');
  }

  /**
   * Retrieves chronological snapshot points for a database connection.
   */
  getConnectionVersions(connectionId: string): Observable<DatabaseSnapshotPoint[]> {
    return this.http.get<DatabaseSnapshotPoint[]>(`/api/v1/connections/${connectionId}/versions`);
  }

  /**
   * Compares database-wide schema versions between two timestamps.
   */
  compareConnectionVersions(
    connectionId: string,
    v1Ts: string,
    v2Ts: string,
    schemaName?: string
  ): Observable<DatabaseVersionDiffResponse> {
    let params = new HttpParams()
      .set('v1_ts', v1Ts)
      .set('v2_ts', v2Ts);
    if (schemaName) {
      params = params.set('schema_name', schemaName);
    }

    return this.http.get<DatabaseVersionDiffResponse>(
      `/api/v1/connections/${connectionId}/versions/compare`,
      { params }
    );
  }

  /**
   * Retrieves chronological list of snapshot versions for a catalog table.
   */
  getVersions(tableId: string): Observable<TableVersionItem[]> {
    return this.http.get<TableVersionItem[]>(`${this.baseUrl}/${tableId}/versions`);
  }

  /**
   * Compares two snapshot versions of a table side-by-side.
   */
  compareVersions(tableId: string, v1: number, v2: number): Observable<VersionDiffResponse> {
    const params = new HttpParams()
      .set('v1', v1.toString())
      .set('v2', v2.toString());

    return this.http.get<VersionDiffResponse>(`${this.baseUrl}/${tableId}/versions/compare`, {
      params,
    });
  }
}

