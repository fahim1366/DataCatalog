import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { of } from 'rxjs';
import { By } from '@angular/platform-browser';
import { SyncJobDiagnosticModalComponent } from './sync-job-diagnostic-modal.component';
import { SyncJobDetailResponse, SyncJobService } from '../../core/services/sync-job.service';

const mockDetail: SyncJobDetailResponse = {
  id: '22222222-2222-2222-2222-222222222222',
  connection_id: 'c2222222-2222-2222-2222-222222222222',
  connection_name: 'Core_Postgres_DB',
  status: 'PARTIAL_FAILURE',
  triggered_by: 'SCHEDULE',
  started_at: '2026-11-16T10:05:00Z',
  completed_at: '2026-11-16T10:05:04Z',
  duration_ms: 4100,
  tables_scanned: 80,
  tables_created: 0,
  tables_updated: 0,
  tables_deleted: 0,
  columns_scanned: 800,
  changes_detected: 0,
  error_message: 'Permission denied on schema audit',
  created_at: '2026-11-16T10:05:00Z',
  error_trace_jsonb: {
    failed_schemas: [
      {
        schema: 'audit',
        error: 'permission denied for schema audit',
        code: '42501',
      },
      {
        schema: 'secret_vault',
        error: 'role data_catalog_app does not have usage',
        code: '42501',
      },
    ],
    total_failed_schemas: 2,
  },
};

describe('SyncJobDiagnosticModalComponent (T082)', () => {
  let fixture: ComponentFixture<SyncJobDiagnosticModalComponent>;
  let component: SyncJobDiagnosticModalComponent;
  let syncJobService: SyncJobService;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [SyncJobDiagnosticModalComponent],
      providers: [
        SyncJobService,
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    }).compileComponents();

    syncJobService = TestBed.inject(SyncJobService);
    vi.spyOn(syncJobService, 'getSyncJobDetail').mockReturnValue(of(mockDetail));

    fixture = TestBed.createComponent(SyncJobDiagnosticModalComponent);
    component = fixture.componentInstance;
  });

  it('should display JSON breakdown of failed schemas when modal is opened', () => {
    component.jobId = mockDetail.id;
    component.visible = true;
    component.ngOnChanges({
      visible: {
        currentValue: true,
        previousValue: false,
        firstChange: true,
        isFirstChange: () => true,
      },
    });

    fixture.detectChanges();

    const titleEl = fixture.debugElement.query(By.css('.diag-modal-title'));
    expect(titleEl).toBeTruthy();
    expect(titleEl.nativeElement.textContent).toContain('Core_Postgres_DB');

    const schemaRows = fixture.debugElement.queryAll(By.css('.failed-schema-item'));
    expect(schemaRows.length).toBe(2);

    const text = fixture.nativeElement.textContent;
    expect(text).toContain('audit');
    expect(text).toContain('secret_vault');
    expect(text).toContain('42501');
  });

  it('should render copyable stack trace and copy JSON to clipboard', async () => {
    component.jobId = mockDetail.id;
    component.visible = true;
    component.jobDetail.set(mockDetail);
    fixture.detectChanges();

    const writeTextSpy = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, {
      clipboard: {
        writeText: writeTextSpy,
      },
    });

    const copyBtn = fixture.debugElement.query(By.css('.btn-copy-trace'));
    expect(copyBtn).toBeTruthy();

    copyBtn.nativeElement.click();
    fixture.detectChanges();
    await fixture.whenStable();

    expect(writeTextSpy).toHaveBeenCalled();
    expect(component.copied()).toBe(true);
  });
});
