import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

export type DriftResolutionAction = 'RETAIN_APPROVED' | 'OVERWRITE_WITH_SOURCE' | 'CUSTOM_MERGE';

export interface ColumnDriftDetail {
  column_id: string;
  column_name: string;
  source_description?: string | null;
  approved_description?: string | null;
  drift_detected: boolean;
}

export interface DescriptionDriftDetailResponse {
  table_id: string;
  table_name: string;
  table_source_description?: string | null;
  table_approved_description?: string | null;
  drift_detected: boolean;
  column_drifts: ColumnDriftDetail[];
}

export interface ColumnDriftResolutionRequest {
  column_id: string;
  resolution_action: DriftResolutionAction;
  custom_description?: string | null;
}

export interface ResolveDescriptionDriftRequest {
  resolution_action: DriftResolutionAction;
  custom_description?: string | null;
  column_resolutions?: ColumnDriftResolutionRequest[];
}

export interface DescriptionDriftResolutionResponse {
  success: boolean;
  message: string;
  resolved_at: string;
}

@Injectable({
  providedIn: 'root',
})
export class DriftService {
  private readonly http = inject(HttpClient);
  private readonly tablesUrl = '/api/v1/tables';

  /**
   * Returns side-by-side comparison between native source database comment
   * and curated approved description for a table and its columns.
   */
  getTableDrift(tableId: string): Observable<DescriptionDriftDetailResponse> {
    return this.http.get<DescriptionDriftDetailResponse>(
      `${this.tablesUrl}/${tableId}/drift`
    );
  }

  /**
   * Resolves description drift on a table and optionally on columns by choosing an action:
   * RETAIN_APPROVED, OVERWRITE_WITH_SOURCE, or CUSTOM_MERGE. Resets drift flag.
   */
  resolveDrift(
    tableId: string,
    payload: ResolveDescriptionDriftRequest
  ): Observable<DescriptionDriftResolutionResponse> {
    return this.http.post<DescriptionDriftResolutionResponse>(
      `${this.tablesUrl}/${tableId}/drift/resolve`,
      payload
    );
  }
}
