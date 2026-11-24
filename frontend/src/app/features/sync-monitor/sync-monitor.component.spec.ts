import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { of } from 'rxjs';
import { By } from '@angular/platform-browser';
import { SyncMonitorComponent } from './sync-monitor.component';
import { SyncJobService, SyncJobListResponse, SyncJobResponse } from '../../core/services/sync-job.service';
import { ConnectionService } from '../connections/services/connection.service';

const mockJobs: SyncJobResponse[] = [
  {
    id: '11111111-1111-1111-1111-111111111111',
    connection_id: 'c1111111-1111-1111-1111-111111111111',
    status: 'SUCCESS',
    triggered_by: 'SCHEDULE',
    started_at: '2026-11-16T10:00:00Z',
    completed_at: '2026-11-16T10:00:02Z',
    duration_ms: 2150,
    tables_scanned: 120,
    tables_created: 2,
    tables_updated: 5,
    tables_deleted: 0,
    columns_scanned: 1400,
    changes_detected: 7,
    created_at: '2026-11-16T10:00:00Z',
  },
  {
    id: '22222222-2222-2222-2222-222222222222',
    connection_id: 'c2222222-2222-2222-2222-222222222222',
    status: 'PARTIAL_FAILURE',
    triggered_by: 'MANUAL',
    started_at: '2026-11-16T10:05:00Z',
    completed_at: '2026-11-16T10:05:04Z',
    duration_ms: 4100,
    tables_scanned: 80,
    tables_created: 0,
    tables_updated: 0,
    tables_deleted: 0,
    columns_scanned: 800,
    changes_detected: 0,
    error_message: 'Schema audit failed with 42501',
    created_at: '2026-11-16T10:05:00Z',
  },
  {
    id: '33333333-3333-3333-3333-333333333333',
    connection_id: 'c1111111-1111-1111-1111-111111111111',
    status: 'FAILED',
    triggered_by: 'SCHEDULE',
    started_at: '2026-11-16T10:10:00Z',
    completed_at: '2026-11-16T10:10:05Z',
    duration_ms: 5000,
    tables_scanned: 0,
    tables_created: 0,
    tables_updated: 0,
    tables_deleted: 0,
    columns_scanned: 0,
    changes_detected: 0,
    error_message: 'Connection timed out',
    created_at: '2026-11-16T10:10:00Z',
  },
  {
    id: '44444444-4444-4444-4444-444444444444',
    connection_id: 'c3333333-3333-3333-3333-333333333333',
    status: 'RUNNING',
    triggered_by: 'RETRY',
    started_at: '2026-11-16T10:15:00Z',
    tables_scanned: 40,
    tables_created: 0,
    tables_updated: 0,
    tables_deleted: 0,
    columns_scanned: 350,
    changes_detected: 0,
    created_at: '2026-11-16T10:15:00Z',
  },
];

const mockListResponse: SyncJobListResponse = {
  items: mockJobs,
  total: 4,
  next_cursor: null,
};

describe('SyncMonitorComponent (T081)', () => {
  let fixture: ComponentFixture<SyncMonitorComponent>;
  let component: SyncMonitorComponent;
  let syncJobService: SyncJobService;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [SyncMonitorComponent],
      providers: [
        SyncJobService,
        ConnectionService,
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    }).compileComponents();

    syncJobService = TestBed.inject(SyncJobService);
    vi.spyOn(syncJobService, 'getSyncJobs').mockReturnValue(of(mockListResponse));
    vi.spyOn(syncJobService, 'getQueueStatus').mockReturnValue(
      of({
        worker_online: true,
        running_jobs: [],
        queued_jobs: [],
      })
    );

    fixture = TestBed.createComponent(SyncMonitorComponent);
    component = fixture.componentInstance;
  });

  it('should render sync job rows with semantic status badges (SUCCESS, PARTIAL_FAILURE, FAILED, RUNNING)', () => {
    fixture.detectChanges();

    const rows = fixture.debugElement.queryAll(By.css('.sync-job-row'));
    expect(rows.length).toBe(4);

    const successBadge = fixture.debugElement.query(By.css('.badge-status-success'));
    expect(successBadge).toBeTruthy();
    expect(successBadge.nativeElement.textContent).toContain('SUCCESS');

    const partialBadge = fixture.debugElement.query(By.css('.badge-status-partial_failure'));
    expect(partialBadge).toBeTruthy();
    expect(partialBadge.nativeElement.textContent).toContain('PARTIAL_FAILURE');

    const failedBadge = fixture.debugElement.query(By.css('.badge-status-failed'));
    expect(failedBadge).toBeTruthy();
    expect(failedBadge.nativeElement.textContent).toContain('FAILED');

    const runningBadge = fixture.debugElement.query(By.css('.badge-status-running'));
    expect(runningBadge).toBeTruthy();
    expect(runningBadge.nativeElement.textContent).toContain('RUNNING');
  });

  it('should format Persian dates using Intl.DateTimeFormat', () => {
    const formattedDate = component.formatPersianDate('2026-11-16T10:00:00Z');
    expect(formattedDate).toBeTruthy();
    // Persian locale format produces Persian digits or month names
    expect(typeof formattedDate).toBe('string');
  });

  it('should poll sync jobs every 3 seconds when polling is active', () => {
    vi.useFakeTimers();
    const getJobsSpy = vi.spyOn(syncJobService, 'getSyncJobs').mockReturnValue(of(mockListResponse));

    component.startPolling();
    expect(getJobsSpy).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(3000);
    expect(getJobsSpy).toHaveBeenCalledTimes(2);

    vi.advanceTimersByTime(3000);
    expect(getJobsSpy).toHaveBeenCalledTimes(3);

    // Toggle pause polling
    component.togglePolling();
    vi.advanceTimersByTime(6000);
    // Should NOT have polled during pause
    expect(getJobsSpy).toHaveBeenCalledTimes(3);

    component.ngOnDestroy();
    vi.useRealTimers();
  });

  it('should filter jobs by status when status filter changes', () => {
    fixture.detectChanges();
    const getJobsSpy = vi.spyOn(syncJobService, 'getSyncJobs').mockReturnValue(of(mockListResponse));

    component.onStatusFilterChange('FAILED');
    expect(component.selectedStatus()).toBe('FAILED');
    expect(getJobsSpy).toHaveBeenCalledWith('FAILED', undefined, undefined, 50);
  });

  it('should trigger retry for failed job when retry button is clicked', () => {
    fixture.detectChanges();
    const retrySpy = vi.spyOn(syncJobService, 'retrySyncJob').mockReturnValue(
      of({
        id: '55555555-5555-5555-5555-555555555555',
        connection_id: 'c1111111-1111-1111-1111-111111111111',
        status: 'QUEUED',
        triggered_by: 'RETRY',
        tables_scanned: 0,
        tables_created: 0,
        tables_updated: 0,
        tables_deleted: 0,
        columns_scanned: 0,
        changes_detected: 0,
        created_at: '2026-11-16T10:20:00Z',
      })
    );

    component.onRetryJob(mockJobs[2]); // FAILED job
    expect(retrySpy).toHaveBeenCalledWith(mockJobs[2].id);
  });

  it('should trigger expedite for queued job when expedite button is clicked', () => {
    fixture.detectChanges();
    const queuedJob: SyncJobResponse = {
      id: '66666666-6666-6666-6666-666666666666',
      connection_id: 'c1111111-1111-1111-1111-111111111111',
      status: 'QUEUED',
      triggered_by: 'MANUAL',
      priority: 0,
      tables_scanned: 0,
      tables_created: 0,
      tables_updated: 0,
      tables_deleted: 0,
      columns_scanned: 0,
      changes_detected: 0,
      created_at: '2026-11-16T10:25:00Z',
    };

    const expediteSpy = vi.spyOn(syncJobService, 'expediteSyncJob').mockReturnValue(
      of({
        ...queuedJob,
        priority: 10,
      })
    );

    component.onExpediteJob(queuedJob);
    expect(expediteSpy).toHaveBeenCalledWith(queuedJob.id);
  });

  it('should trigger cancel for job when cancel button is clicked', () => {
    fixture.detectChanges();
    const cancelSpy = vi.spyOn(syncJobService, 'cancelSyncJob').mockReturnValue(
      of({
        id: mockJobs[3].id,
        connection_id: mockJobs[3].connection_id,
        status: 'CANCELLED',
        triggered_by: mockJobs[3].triggered_by,
        tables_scanned: 0,
        tables_created: 0,
        tables_updated: 0,
        tables_deleted: 0,
        columns_scanned: 0,
        changes_detected: 0,
        error_message: 'لغو شده توسط کاربر',
        created_at: mockJobs[3].created_at,
      })
    );

    component.onCancelJob(mockJobs[3]);
    expect(cancelSpy).toHaveBeenCalledWith(mockJobs[3].id);
  });

  it('should open and handle batch expedite modal with connection selection and submit', () => {
    const connService = TestBed.inject(ConnectionService);
    const mockConnections = [
      {
        id: 'conn-1',
        name: 'Database One',
        engine_type: 'POSTGRESQL',
        host: '10.0.0.1',
        port: 5432,
        database_name: 'db_one',
        owner: 'admin',
        is_active: true,
        created_at: '2026-11-16T10:00:00Z',
      },
      {
        id: 'conn-2',
        name: 'Database Two',
        engine_type: 'POSTGRESQL',
        host: '10.0.0.2',
        port: 5432,
        database_name: 'db_two',
        owner: 'admin',
        is_active: true,
        created_at: '2026-11-16T10:00:00Z',
      },
    ];

    vi.spyOn(connService, 'getConnections').mockReturnValue(of(mockConnections as any));
    const batchSpy = vi.spyOn(syncJobService, 'batchExpediteSync').mockReturnValue(
      of({
        jobs: [],
        message: '2 پایگاه‌داده با موفقیت برای همگام‌سازی خارج از نوبت ارسال شدند.',
        total_dispatched: 2,
      })
    );

    component.openBatchExpediteModal();
    expect(component.showBatchModal()).toBe(true);
    expect(component.availableConnections().length).toBe(2);

    // Toggle select conn-1
    component.toggleConnectionSelection('conn-1');
    expect(component.isConnectionSelected('conn-1')).toBe(true);
    expect(component.isConnectionSelected('conn-2')).toBe(false);

    // Toggle select all
    component.toggleSelectAllConnections();
    expect(component.isConnectionSelected('conn-1')).toBe(true);
    expect(component.isConnectionSelected('conn-2')).toBe(true);

    // Submit batch expedite
    component.onBatchExpediteSubmit();
    expect(batchSpy).toHaveBeenCalledWith(['conn-1', 'conn-2']);
    expect(component.showBatchModal()).toBe(false);
  });
});
