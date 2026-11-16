import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { SyncJobService } from './sync-job.service';
import { DriftService } from './drift.service';
import { TableStatsService } from './table-stats.service';
import {
  CircuitBreakerStatusResponse,
  ConnectionDiagnosticTestResponse,
  DescriptionDriftDetailResponse,
  DescriptionDriftResolutionResponse,
  ResolveDescriptionDriftRequest,
  SyncJobDetailResponse,
  SyncJobListResponse,
  SyncJobResponse,
  TableStatisticsHistoryResponse,
} from './sync-job.service';

describe('Phase 6 Core Services (T079)', () => {
  let httpMock: HttpTestingController;
  let syncJobService: SyncJobService;
  let driftService: DriftService;
  let tableStatsService: TableStatsService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        SyncJobService,
        DriftService,
        TableStatsService,
      ],
    });

    httpMock = TestBed.inject(HttpTestingController);
    syncJobService = TestBed.inject(SyncJobService);
    driftService = TestBed.inject(DriftService);
    tableStatsService = TestBed.inject(TableStatsService);
  });

  afterEach(() => {
    httpMock.verify();
  });

  describe('SyncJobService', () => {
    const connectionId = '11111111-1111-1111-1111-111111111111';
    const jobId = '22222222-2222-2222-2222-222222222222';

    it('should execute live connection diagnostic test via POST /api/v1/connections/:id/test', () => {
      const mockResponse: ConnectionDiagnosticTestResponse = {
        success: true,
        latency_ms: 12.5,
        dialect: 'POSTGRESQL',
        server_version: 'PostgreSQL 16.1',
        message: 'اتصال با موفقیت برقرار شد.',
        diagnostic_details: { tcp: true, auth: true, ssl: true, read_only: true },
        tested_at: '2026-11-16T12:00:00Z',
      };

      syncJobService.testConnection(connectionId).subscribe((res) => {
        expect(res).toEqual(mockResponse);
        expect(res.success).toBe(true);
        expect(res.latency_ms).toBe(12.5);
      });

      const req = httpMock.expectOne(`/api/v1/connections/${connectionId}/test`);
      expect(req.request.method).toBe('POST');
      req.flush(mockResponse);
    });

    it('should trigger manual sync via POST /api/v1/connections/:id/sync', () => {
      const mockJob: SyncJobResponse = {
        id: jobId,
        connection_id: connectionId,
        status: 'QUEUED',
        triggered_by: 'MANUAL',
        tables_scanned: 0,
        tables_created: 0,
        tables_updated: 0,
        tables_deleted: 0,
        columns_scanned: 0,
        changes_detected: 0,
        created_at: '2026-11-16T12:00:00Z',
      };

      syncJobService.triggerSync(connectionId).subscribe((res) => {
        expect(res.id).toBe(jobId);
        expect(res.status).toBe('QUEUED');
      });

      const req = httpMock.expectOne(`/api/v1/connections/${connectionId}/sync`);
      expect(req.request.method).toBe('POST');
      req.flush(mockJob);
    });

    it('should reset circuit breaker via POST /api/v1/connections/:id/circuit-breaker/reset', () => {
      const mockCb: CircuitBreakerStatusResponse = {
        connection_id: connectionId,
        circuit_state: 'CLOSED',
        consecutive_failures: 0,
        cooldown_until: null,
      };

      syncJobService.resetCircuitBreaker(connectionId).subscribe((res) => {
        expect(res.circuit_state).toBe('CLOSED');
        expect(res.consecutive_failures).toBe(0);
      });

      const req = httpMock.expectOne(`/api/v1/connections/${connectionId}/circuit-breaker/reset`);
      expect(req.request.method).toBe('POST');
      req.flush(mockCb);
    });

    it('should query sync jobs list with filters and cursor pagination via GET /api/v1/sync-jobs', () => {
      const mockList: SyncJobListResponse = {
        items: [
          {
            id: jobId,
            connection_id: connectionId,
            status: 'SUCCESS',
            triggered_by: 'SCHEDULE',
            tables_scanned: 45,
            tables_created: 2,
            tables_updated: 3,
            tables_deleted: 0,
            columns_scanned: 250,
            changes_detected: 5,
            created_at: '2026-11-16T12:00:00Z',
          },
        ],
        total: 1,
        next_cursor: 'cursor-token-123',
      };

      syncJobService.getSyncJobs('SUCCESS', connectionId, 'cursor-0', 20).subscribe((res) => {
        expect(res.items.length).toBe(1);
        expect(res.total).toBe(1);
        expect(res.next_cursor).toBe('cursor-token-123');
      });

      const req = httpMock.expectOne(
        (r) =>
          r.url === '/api/v1/sync-jobs' &&
          r.params.get('status') === 'SUCCESS' &&
          r.params.get('connection_id') === connectionId &&
          r.params.get('cursor') === 'cursor-0' &&
          r.params.get('limit') === '20'
      );
      expect(req.request.method).toBe('GET');
      req.flush(mockList);
    });

    it('should retrieve sync job detail with error trace via GET /api/v1/sync-jobs/:id', () => {
      const mockDetail: SyncJobDetailResponse = {
        id: jobId,
        connection_id: connectionId,
        connection_name: 'Core_Postgres',
        status: 'PARTIAL_FAILURE',
        triggered_by: 'SCHEDULE',
        tables_scanned: 10,
        tables_created: 0,
        tables_updated: 0,
        tables_deleted: 0,
        columns_scanned: 80,
        changes_detected: 0,
        created_at: '2026-11-16T12:00:00Z',
        error_message: 'Permission denied on schema audit',
        error_trace_jsonb: {
          failed_schemas: [
            {
              schema: 'audit',
              error: 'permission denied for schema audit',
              code: '42501',
            },
          ],
        },
      };

      syncJobService.getSyncJobDetail(jobId).subscribe((res) => {
        expect(res.connection_name).toBe('Core_Postgres');
        expect(res.error_trace_jsonb['failed_schemas'].length).toBe(1);
      });

      const req = httpMock.expectOne(`/api/v1/sync-jobs/${jobId}`);
      expect(req.request.method).toBe('GET');
      req.flush(mockDetail);
    });

    it('should retry a failed sync job via POST /api/v1/sync-jobs/:id/retry', () => {
      const mockRetriedJob: SyncJobResponse = {
        id: '33333333-3333-3333-3333-333333333333',
        connection_id: connectionId,
        status: 'QUEUED',
        triggered_by: 'RETRY',
        tables_scanned: 0,
        tables_created: 0,
        tables_updated: 0,
        tables_deleted: 0,
        columns_scanned: 0,
        changes_detected: 0,
        created_at: '2026-11-16T12:05:00Z',
      };

      syncJobService.retrySyncJob(jobId).subscribe((res) => {
        expect(res.triggered_by).toBe('RETRY');
        expect(res.status).toBe('QUEUED');
      });

      const req = httpMock.expectOne(`/api/v1/sync-jobs/${jobId}/retry`);
      expect(req.request.method).toBe('POST');
      req.flush(mockRetriedJob);
    });

    it('should get connection sync execution history via GET /api/v1/connections/:id/jobs', () => {
      const mockList: SyncJobListResponse = {
        items: [],
        total: 0,
      };

      syncJobService.getConnectionSyncJobs(connectionId, 10).subscribe((res) => {
        expect(res.total).toBe(0);
      });

      const req = httpMock.expectOne((r) => r.url === `/api/v1/connections/${connectionId}/jobs` && r.params.get('limit') === '10');
      expect(req.request.method).toBe('GET');
      req.flush(mockList);
    });
  });

  describe('DriftService', () => {
    const tableId = '44444444-4444-4444-4444-444444444444';

    it('should inspect table description drift details via GET /api/v1/tables/:id/drift', () => {
      const mockDrift: DescriptionDriftDetailResponse = {
        table_id: tableId,
        table_name: 'orders',
        table_source_description: 'Source DB Comment: Customer Orders Table',
        table_approved_description: 'Approved: جدول اصلی سفارشات مشتریان',
        drift_detected: true,
        column_drifts: [
          {
            column_id: '55555555-5555-5555-5555-555555555555',
            column_name: 'total_amount',
            source_description: 'Order total after taxes',
            approved_description: 'مبلغ کل سفارش',
            drift_detected: true,
          },
        ],
      };

      driftService.getTableDrift(tableId).subscribe((res) => {
        expect(res.drift_detected).toBe(true);
        expect(res.column_drifts.length).toBe(1);
      });

      const req = httpMock.expectOne(`/api/v1/tables/${tableId}/drift`);
      expect(req.request.method).toBe('GET');
      req.flush(mockDrift);
    });

    it('should resolve table drift via POST /api/v1/tables/:id/drift/resolve', () => {
      const payload: ResolveDescriptionDriftRequest = {
        resolution_action: 'RETAIN_APPROVED',
      };
      const mockResolution: DescriptionDriftResolutionResponse = {
        success: true,
        message: 'Description drift resolved successfully.',
        resolved_at: '2026-11-16T12:10:00Z',
      };

      driftService.resolveDrift(tableId, payload).subscribe((res) => {
        expect(res.success).toBe(true);
      });

      const req = httpMock.expectOne(`/api/v1/tables/${tableId}/drift/resolve`);
      expect(req.request.method).toBe('POST');
      expect(req.request.body).toEqual(payload);
      req.flush(mockResolution);
    });
  });

  describe('TableStatsService', () => {
    const tableId = '44444444-4444-4444-4444-444444444444';

    it('should fetch table statistics history via GET /api/v1/tables/:id/statistics', () => {
      const mockStats: TableStatisticsHistoryResponse = {
        table_id: tableId,
        data_points: [
          {
            recorded_at: '2026-11-16T10:00:00Z',
            row_count_estimate: 150000,
            size_bytes: 45000000,
          },
          {
            recorded_at: '2026-11-15T10:00:00Z',
            row_count_estimate: 148000,
            size_bytes: 44000000,
          },
        ],
      };

      tableStatsService.getTableStatistics(tableId, 30).subscribe((res) => {
        expect(res.table_id).toBe(tableId);
        expect(res.data_points.length).toBe(2);
      });

      const req = httpMock.expectOne((r) => r.url === `/api/v1/tables/${tableId}/statistics` && r.params.get('limit') === '30');
      expect(req.request.method).toBe('GET');
      req.flush(mockStats);
    });
  });
});
