import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import {
  CompletenessReport,
  ClassificationReport,
  ReportRefreshResponse,
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
  getCompletenessReport(connectionIds?: string[]): Observable<CompletenessReport> {
    let params = new HttpParams();
    if (connectionIds && connectionIds.length > 0) {
      connectionIds.forEach((id) => {
        params = params.append('connection_ids', id);
      });
    }
    return this.http.get<CompletenessReport>(`${this.baseUrl}/completeness`, { params });
  }

  /**
   * Retrieves security classification and PII distribution metrics across all or selected databases.
   */
  getClassificationDistribution(connectionIds?: string[]): Observable<ClassificationReport> {
    let params = new HttpParams();
    if (connectionIds && connectionIds.length > 0) {
      connectionIds.forEach((id) => {
        params = params.append('connection_ids', id);
      });
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
   * Exports executive reporting summary to JSON or CSV format.
   */
  exportReports(format: 'json' | 'csv', connectionIds?: string[]): Observable<Blob> {
    let params = new HttpParams().set('format', format);
    if (connectionIds && connectionIds.length > 0) {
      connectionIds.forEach((id) => {
        params = params.append('connection_ids', id);
      });
    }
    return this.http.get(`${this.baseUrl}/export`, {
      params,
      responseType: 'blob',
    });
  }
}

