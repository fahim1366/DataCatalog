import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, map } from 'rxjs';
import { TableVersionItem, VersionDiffResponse } from '../models/version.models';

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

