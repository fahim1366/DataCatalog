import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { of } from 'rxjs';
import { By } from '@angular/platform-browser';
import { ConnectionDiagnosticDialogComponent } from './connection-diagnostic-dialog.component';
import { SyncJobService } from '../../core/services/sync-job.service';
import { DatabaseConnection } from './models/connection.models';

const mockConnection: DatabaseConnection = {
  id: '11111111-1111-1111-1111-111111111111',
  name: 'Production PostgreSQL Warehouse',
  engine_type: 'POSTGRESQL',
  host: 'postgres.internal.net',
  port: 5432,
  database_name: 'prod_warehouse',
  default_schema: 'public',
  owner: 'data-engineering',
  is_active: true,
  created_at: '2026-11-01T00:00:00Z',
  updated_at: '2026-11-10T02:00:00Z',
};

describe('ConnectionDiagnosticDialogComponent (T080)', () => {
  let fixture: ComponentFixture<ConnectionDiagnosticDialogComponent>;
  let component: ConnectionDiagnosticDialogComponent;
  let syncJobService: SyncJobService;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ConnectionDiagnosticDialogComponent],
      providers: [
        SyncJobService,
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    }).compileComponents();

    syncJobService = TestBed.inject(SyncJobService);

    vi.spyOn(syncJobService, 'testConnection').mockReturnValue(
      of({
        success: true,
        latency_ms: 18.4,
        dialect: 'POSTGRESQL',
        server_version: 'PostgreSQL 16.1',
        message: 'اتصال با موفقیت برقرار شد.',
        diagnostic_details: {
          tcp: true,
          auth: true,
          ssl: true,
          read_only: true,
        },
        tested_at: '2026-11-16T12:00:00Z',
      })
    );

    vi.spyOn(syncJobService, 'resetCircuitBreaker').mockReturnValue(
      of({
        connection_id: mockConnection.id,
        circuit_state: 'CLOSED',
        consecutive_failures: 0,
        cooldown_until: null,
      })
    );

    fixture = TestBed.createComponent(ConnectionDiagnosticDialogComponent);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('connection', mockConnection);
    fixture.componentRef.setInput('visible', true);
    fixture.detectChanges();
    await fixture.whenStable();
  });

  it('should render dialog header with connection name and dialect', () => {
    const headerEl = fixture.debugElement.query(By.css('.diag-dialog-title'));
    expect(headerEl).toBeTruthy();
    expect(headerEl.nativeElement.textContent).toContain('Production PostgreSQL Warehouse');
    expect(headerEl.nativeElement.textContent).toContain('POSTGRESQL');
  });

  it('should render 5 diagnostic checklist items (TCP, Auth, SSL, Read-Only, Latency)', async () => {
    component.runDiagnostic();
    fixture.detectChanges();
    await fixture.whenStable();

    const checklistItems = fixture.debugElement.queryAll(By.css('.diag-checklist-item'));
    expect(checklistItems.length).toBe(5);

    const checklistText = fixture.nativeElement.textContent;
    expect(checklistText).toContain('TCP');
    expect(checklistText).toContain('احراز هویت');
    expect(checklistText).toContain('SSL');
    expect(checklistText).toContain('فقط خواندنی');
    expect(checklistText).toContain('تأخیر');
    expect(checklistText).toContain('18.4');
  });

  it('should render reset circuit breaker button and reset state on click', async () => {
    const resetSpy = vi.spyOn(syncJobService, 'resetCircuitBreaker');
    const resetBtn = fixture.debugElement.query(By.css('.btn-reset-cb'));
    expect(resetBtn).toBeTruthy();

    resetBtn.nativeElement.click();
    fixture.detectChanges();
    await fixture.whenStable();

    expect(resetSpy).toHaveBeenCalledWith(mockConnection.id);
    expect(component.circuitState()).toBe('CLOSED');
    expect(component.consecutiveFailures()).toBe(0);

    const cbBadge = fixture.debugElement.query(By.css('.cb-badge'));
    expect(cbBadge.nativeElement.textContent).toContain('CLOSED');
  });
});
