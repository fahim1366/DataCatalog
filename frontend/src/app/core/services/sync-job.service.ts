import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, Subject } from 'rxjs';
import { tap } from 'rxjs/operators';

export type SyncJobStatus = 'QUEUED' | 'RUNNING' | 'SUCCESS' | 'PARTIAL_FAILURE' | 'FAILED' | 'CANCELLED';
export type SyncTriggerType = 'SCHEDULE' | 'MANUAL' | 'RETRY';
export type CircuitState = 'CLOSED' | 'OPEN' | 'HALF_OPEN';

export interface ConnectionDiagnosticTestResponse {
  success: boolean;
  latency_ms: number;
  dialect: string;
  server_version?: string | null;
  message: string;
  diagnostic_details?: Record<string, any> | null;
  tested_at: string;
}

export interface SyncJobResponse {
  id: string;
  connection_id: string;
  schedule_id?: string | null;
  status: SyncJobStatus;
  triggered_by: SyncTriggerType;
  started_at?: string | null;
  completed_at?: string | null;
  duration_ms?: number | null;
  tables_scanned: number;
  tables_created: number;
  tables_updated: number;
  tables_deleted: number;
  columns_scanned: number;
  changes_detected: number;
  error_message?: string | null;
  priority?: number;
  created_at: string;
}

export interface SyncJobQueueItem extends SyncJobResponse {
  connection_name?: string | null;
  queue_position?: number | null;
  estimated_wait_seconds?: number | null;
}

export interface SyncQueueStatusResponse {
  worker_online: boolean;
  running_jobs: SyncJobQueueItem[];
  queued_jobs: SyncJobQueueItem[];
}

export interface BatchExpediteResponse {
  jobs: SyncJobResponse[];
  message: string;
  total_dispatched: number;
}

export interface SyncJobDetailResponse extends SyncJobResponse {
  connection_name?: string | null;
  error_trace_jsonb: Record<string, any>;
}

export interface SyncJobListResponse {
  items: SyncJobResponse[];
  total: number;
  next_cursor?: string | null;
}

export interface CircuitBreakerStatusResponse {
  connection_id: string;
  circuit_state: CircuitState;
  consecutive_failures: number;
  cooldown_until?: string | null;
}

// Re-export drift and stats interfaces for convenience
export type {
  DriftResolutionAction,
  ColumnDriftDetail,
  DescriptionDriftDetailResponse,
  ColumnDriftResolutionRequest,
  ResolveDescriptionDriftRequest,
  DescriptionDriftResolutionResponse,
} from './drift.service';

export type {
  TableStatisticPoint,
  TableStatisticsHistoryResponse,
} from './table-stats.service';

@Injectable({
  providedIn: 'root',
})
export class SyncJobService {
  private readonly http = inject(HttpClient);
  private readonly connectionsUrl = '/api/v1/connections';
  private readonly syncJobsUrl = '/api/v1/sync-jobs';

  /** Emits whenever a sync job is triggered, retried, or batch-expedited so /sync-monitor refreshes immediately. */
  private readonly _syncTriggered$ = new Subject<void>();
  readonly syncTriggered$ = this._syncTriggered$.asObservable();

  notifySyncTriggered(): void {
    this._syncTriggered$.next();
  }

  /**
   * Executes a transient dialect connectivity diagnostic test bounded by a 5.0s timeout.
   */
  testConnection(connectionId: string): Observable<ConnectionDiagnosticTestResponse> {
    return this.http.post<ConnectionDiagnosticTestResponse>(
      `${this.connectionsUrl}/${connectionId}/test`,
      {}
    );
  }

  /**
   * Triggers immediate synchronization of database metadata.
   */
  triggerSync(connectionId: string): Observable<SyncJobResponse> {
    return this.http.post<SyncJobResponse>(
      `${this.connectionsUrl}/${connectionId}/sync`,
      {}
    ).pipe(tap(() => this._syncTriggered$.next()));
  }

  /**
   * Resets tripped circuit breaker back to CLOSED state.
   */
  resetCircuitBreaker(connectionId: string): Observable<CircuitBreakerStatusResponse> {
    return this.http.post<CircuitBreakerStatusResponse>(
      `${this.connectionsUrl}/${connectionId}/circuit-breaker/reset`,
      {}
    );
  }

  /**
   * Queries global sync execution history with status filters and cursor pagination.
   */
  getSyncJobs(
    status?: string,
    connectionId?: string,
    cursor?: string,
    limit: number = 50
  ): Observable<SyncJobListResponse> {
    let params = new HttpParams().set('limit', limit.toString());
    if (status && status !== 'ALL') {
      params = params.set('status', status);
    }
    if (connectionId) {
      params = params.set('connection_id', connectionId);
    }
    if (cursor) {
      params = params.set('cursor', cursor);
    }

    return this.http.get<SyncJobListResponse>(this.syncJobsUrl, { params });
  }

  /**
   * Retrieves full sync job details including schema-level partial failures and error traces.
   */
  getSyncJobDetail(jobId: string): Observable<SyncJobDetailResponse> {
    return this.http.get<SyncJobDetailResponse>(`${this.syncJobsUrl}/${jobId}`);
  }

  /**
   * Re-enqueues a failed sync job with RETRY trigger.
   */
  retrySyncJob(jobId: string): Observable<SyncJobResponse> {
    return this.http.post<SyncJobResponse>(
      `${this.syncJobsUrl}/${jobId}/retry`,
      {}
    ).pipe(tap(() => this._syncTriggered$.next()));
  }

  /**
   * Retrieves the live sync queue status, worker state, and running/queued jobs.
   */
  getQueueStatus(): Observable<SyncQueueStatusResponse> {
    return this.http.get<SyncQueueStatusResponse>(`${this.syncJobsUrl}/queue`);
  }

  /**
   * Promotes a queued job to priority 10 and executes it immediately out of order.
   */
  expediteSyncJob(jobId: string): Observable<SyncJobResponse> {
    return this.http.post<SyncJobResponse>(
      `${this.syncJobsUrl}/${jobId}/expedite`,
      {}
    );
  }

  /**
   * Cancels a queued or running sync job and releases the connection concurrency lock.
   */
  cancelSyncJob(jobId: string): Observable<SyncJobResponse> {
    return this.http.post<SyncJobResponse>(
      `${this.syncJobsUrl}/${jobId}/cancel`,
      {}
    );
  }

  /**
   * Triggers immediate out-of-order synchronization for multiple selected databases.
   */
  batchExpediteSync(connectionIds: string[]): Observable<BatchExpediteResponse> {
    return this.http.post<BatchExpediteResponse>(
      `${this.syncJobsUrl}/batch-expedite`,
      { connection_ids: connectionIds }
    ).pipe(tap(() => this._syncTriggered$.next()));
  }

  /**
   * Lists sync job execution history specifically for a connection.
   */
  getConnectionSyncJobs(
    connectionId: string,
    limit: number = 20,
    cursor?: string
  ): Observable<SyncJobListResponse> {
    let params = new HttpParams().set('limit', limit.toString());
    if (cursor) {
      params = params.set('cursor', cursor);
    }

    return this.http.get<SyncJobListResponse>(
      `${this.connectionsUrl}/${connectionId}/jobs`,
      { params }
    );
  }
}
