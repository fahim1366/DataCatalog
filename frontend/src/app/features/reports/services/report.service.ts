import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import {
  CompletenessReport,
  ClassificationReport,
  ReportRefreshResponse,
  ReportPreviewItem,
} from '../models/report.models';

@Injectable({
  providedIn: 'root',
})
export class ReportService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = '/api/v1/reports';

  /**
   * Retrieves aggregated metadata completeness metrics across all or selected databases.
   */
  getCompletenessReport(connectionIds?: string[], excludeLowRowTables: boolean = false): Observable<CompletenessReport> {
    let params = new HttpParams();
    if (connectionIds && connectionIds.length > 0) {
      connectionIds.forEach((id) => {
        params = params.append('connection_ids', id);
      });
    }
    if (excludeLowRowTables) {
      params = params.set('exclude_low_row_tables', 'true');
    }
    return this.http.get<CompletenessReport>(`${this.baseUrl}/completeness`, { params });
  }

  /**
   * Retrieves security classification and PII distribution metrics across all or selected databases.
   */
  getClassificationDistribution(connectionIds?: string[], excludeLowRowTables: boolean = false): Observable<ClassificationReport> {
    let params = new HttpParams();
    if (connectionIds && connectionIds.length > 0) {
      connectionIds.forEach((id) => {
        params = params.append('connection_ids', id);
      });
    }
    if (excludeLowRowTables) {
      params = params.set('exclude_low_row_tables', 'true');
    }
    return this.http.get<ClassificationReport>(`${this.baseUrl}/classification-distribution`, { params });
  }

  /**
   * Triggers non-blocking concurrent refresh of all reporting materialized views.
   */
  refreshMaterializedViews(): Observable<ReportRefreshResponse> {
    return this.http.post<ReportRefreshResponse>(`${this.baseUrl}/refresh`, {});
  }

  /**
   * Fetches data dictionary preview items including both source and approved descriptions.
   */
  getReportPreview(connectionIds?: string[], limit: number = 100, excludeLowRowTables: boolean = false): Observable<ReportPreviewItem[]> {
    let params = new HttpParams().set('limit', limit);
    if (connectionIds && connectionIds.length > 0) {
      connectionIds.forEach((id) => {
        params = params.append('connection_ids', id);
      });
    }
    if (excludeLowRowTables) {
      params = params.set('exclude_low_row_tables', 'true');
    }
    return this.http.get<ReportPreviewItem[]>(`${this.baseUrl}/preview`, { params });
  }

  /**
   * Exports executive reporting summary to JSON, CSV, or XLSX format.
   */
  exportReports(format: 'json' | 'csv' | 'xlsx', connectionIds?: string[], excludeLowRowTables: boolean = false): Observable<Blob> {
    let params = new HttpParams().set('format', format);
    if (connectionIds && connectionIds.length > 0) {
      connectionIds.forEach((id) => {
        params = params.append('connection_ids', id);
      });
    }
    if (excludeLowRowTables) {
      params = params.set('exclude_low_row_tables', 'true');
    }
    return this.http.get(`${this.baseUrl}/export`, {
      params,
      responseType: 'blob',
    });
  }
}

