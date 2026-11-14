import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
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
   * Retrieves aggregated metadata completeness metrics across all registered databases.
   * Queried directly from PostgreSQL mv_metadata_completeness in < 50ms.
   */
  getCompletenessReport(): Observable<CompletenessReport> {
    return this.http.get<CompletenessReport>(`${this.baseUrl}/completeness`);
  }

  /**
   * Retrieves security classification and PII distribution metrics across all catalog columns.
   * Queried directly from PostgreSQL mv_classification_distribution in < 50ms.
   */
  getClassificationDistribution(): Observable<ClassificationReport> {
    return this.http.get<ClassificationReport>(`${this.baseUrl}/classification-distribution`);
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
  exportReports(format: 'json' | 'csv'): Observable<Blob> {
    return this.http.get(`${this.baseUrl}/export?format=${format}`, {
      responseType: 'blob',
    });
  }
}

