import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';

export interface TableStatisticPoint {
  recorded_at: string;
  row_count_estimate: number;
  size_bytes: number;
  sync_job_id?: string | null;
}

export interface TableStatisticsHistoryResponse {
  table_id: string;
  data_points: TableStatisticPoint[];
}

@Injectable({
  providedIn: 'root',
})
export class TableStatsService {
  private readonly http = inject(HttpClient);
  private readonly tablesUrl = '/api/v1/tables';

  /**
   * Retrieves time-series history of estimated row counts and size in bytes
   * from table_statistics_history without structural version clutter.
   */
  getTableStatistics(
    tableId: string,
    limit: number = 30
  ): Observable<TableStatisticsHistoryResponse> {
    const params = new HttpParams().set('limit', limit.toString());
    return this.http.get<TableStatisticsHistoryResponse>(
      `${this.tablesUrl}/${tableId}/statistics`,
      { params }
    );
  }
}
