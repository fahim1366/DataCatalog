import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { By } from '@angular/platform-browser';
import { of, throwError } from 'rxjs';
import { ConnectionListComponent } from './connection-list.component';
import { ConnectionDialogComponent } from './connection-dialog.component';
import { ConnectionService } from './services/connection.service';
import { DatabaseConnection, ConnectionTestResult } from './models/connection.models';
import { AuthService } from '../../core/auth/auth.service';

const mockConnections: DatabaseConnection[] = [
  {
    id: '11111111-1111-1111-1111-111111111111',
    name: 'Production PostgreSQL Warehouse',
    engine_type: 'POSTGRESQL',
    host: 'postgres.internal.net',
    port: 5432,
    database_name: 'prod_warehouse',
    default_schema: 'public',
    owner: 'data-engineering',
    is_active: true,
    custom_params: { sslmode: 'require' },
    masked_credentials: { username: 'app_admin', password: '***' },
    sync_schedule: {
      id: 'sched-001',
      connection_id: '11111111-1111-1111-1111-111111111111',
      cron_expression: '0 2 * * *',
      is_enabled: true,
      last_run_at: '2026-11-10T02:00:00Z',
      last_run_status: 'SUCCESS',
      last_run_message: 'Sync completed in 1.4s',
      next_run_at: '2026-11-11T02:00:00Z',
      created_at: '2026-11-01T00:00:00Z',
      updated_at: '2026-11-10T02:00:00Z',
    },
    created_at: '2026-11-01T00:00:00Z',
    updated_at: '2026-11-10T02:00:00Z',
  },
  {
    id: '22222222-2222-2222-2222-222222222222',
    name: 'Analytics ClickHouse Cluster',
    engine_type: 'CLICKHOUSE',
    host: 'clickhouse.analytics.net',
    port: 8123,
    database_name: 'events_raw',
    default_schema: 'default',
    owner: 'analytics-team',
    is_active: false,
    custom_params: {},
    masked_credentials: { username: 'reader', password: '***' },
    sync_schedule: {
      id: 'sched-002',
      connection_id: '22222222-2222-2222-2222-222222222222',
      cron_expression: '*/30 * * * *',
      is_enabled: false,
      last_run_at: '2026-11-09T18:30:00Z',
      last_run_status: 'FAILED',
      last_run_message: 'Connection timeout after 5000ms',
      next_run_at: null,
      created_at: '2026-11-02T00:00:00Z',
      updated_at: '2026-11-09T18:30:00Z',
    },
    created_at: '2026-11-02T00:00:00Z',
    updated_at: '2026-11-09T18:30:00Z',
  },
];

describe('ConnectionListComponent (T028: Angular Connection Management UI)', () => {
  let fixture: ComponentFixture<ConnectionListComponent>;
  let component: ConnectionListComponent;
  let connectionService: ConnectionService;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ConnectionListComponent, ConnectionDialogComponent],
      providers: [
        ConnectionService,
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    }).compileComponents();

    connectionService = TestBed.inject(ConnectionService);
    const authService = TestBed.inject(AuthService);
    const validClaims = {
      sub: '11111111-1111-1111-1111-111111111111',
      username: 'test_admin',
      roles: ['SUPERADMIN'],
      is_superadmin: true,
      iat: Math.floor(Date.now() / 1000) - 60,
      exp: Math.floor(Date.now() / 1000) + 3600,
    };
    const b64 = (obj: unknown) =>
      btoa(JSON.stringify(obj))
        .replace(/=/g, '')
        .replace(/\+/g, '-')
        .replace(/\//g, '_');
    const token = `${b64({ alg: 'HS256', typ: 'JWT' })}.${b64(validClaims)}.sig`;
    authService.setToken(token);

    vi.spyOn(connectionService, 'getConnections').mockReturnValue(of(mockConnections));

    fixture = TestBed.createComponent(ConnectionListComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
    await fixture.whenStable();
  });

  it('should render the list of registered database connections', () => {
    const rows = fixture.debugElement.queryAll(By.css('.connection-card, .connection-row'));
    expect(rows.length).toBe(2);

    const firstCardText = rows[0].nativeElement.textContent;
    expect(firstCardText).toContain('Production PostgreSQL Warehouse');
    expect(firstCardText).toContain('POSTGRESQL');
    expect(firstCardText).toContain('postgres.internal.net:5432');
    expect(firstCardText).toContain('prod_warehouse');

    const secondCardText = rows[1].nativeElement.textContent;
    expect(secondCardText).toContain('Analytics ClickHouse Cluster');
    expect(secondCardText).toContain('CLICKHOUSE');
    expect(secondCardText).toContain('clickhouse.analytics.net:8123');
  });

  it('should render active and inactive status badges correctly', () => {
    const statusBadges = fixture.debugElement.queryAll(By.css('.status-badge'));
    expect(statusBadges.length).toBeGreaterThanOrEqual(2);

    const activeBadge = statusBadges.find((b) => b.nativeElement.textContent.trim().toLowerCase().includes('active'));
    expect(activeBadge).toBeTruthy();
    expect(activeBadge?.nativeElement.className).toContain('badge-active');

    const inactiveBadge = statusBadges.find((b) => b.nativeElement.textContent.trim().toLowerCase().includes('inactive'));
    expect(inactiveBadge).toBeTruthy();
    expect(inactiveBadge?.nativeElement.className).toContain('badge-inactive');
  });

  it('should render sync schedule cron expression and schedule status badges', () => {
    const text = fixture.nativeElement.textContent;
    expect(text).toContain('0 2 * * *');
    expect(text).toContain('*/30 * * * *');

    const scheduleBadges = fixture.debugElement.queryAll(By.css('.schedule-badge'));
    expect(scheduleBadges.length).toBeGreaterThanOrEqual(2);
    expect(text).toContain('SUCCESS');
    expect(text).toContain('FAILED');
  });

  it('should dispatch test connection request and display latency when "Test Connection" button is clicked', async () => {
    const mockSuccessResult: ConnectionTestResult = {
      success: true,
      latency_ms: 24.5,
      dialect: 'POSTGRESQL',
      message: 'PostgreSQL connection ping succeeded',
      tested_at: '2026-11-10T12:00:00Z',
    };

    const testPingSpy = vi.spyOn(connectionService, 'testConnection').mockReturnValue(of(mockSuccessResult));

    const testButtons = fixture.debugElement.queryAll(By.css('.btn-test-connection'));
    expect(testButtons.length).toBeGreaterThanOrEqual(1);

    testButtons[0].nativeElement.click();
    fixture.detectChanges();
    await fixture.whenStable();

    expect(testPingSpy).toHaveBeenCalledWith('11111111-1111-1111-1111-111111111111');

    const testResultEl = fixture.debugElement.query(By.css('.test-result-indicator, .test-result-success'));
    expect(testResultEl).toBeTruthy();
    expect(testResultEl.nativeElement.textContent).toContain('24.5ms');
    expect(testResultEl.nativeElement.textContent).toContain('PostgreSQL connection ping succeeded');
  });

  it('should display error diagnostic structure when test connection fails', async () => {
    const mockFailedResult: ConnectionTestResult = {
      success: false,
      latency_ms: 5002.1,
      dialect: 'CLICKHOUSE',
      message: 'Connection timed out after 5.0 seconds',
      diagnostic_details: { error_code: 'CONNECTION_TIMEOUT', host: 'clickhouse.analytics.net' },
      tested_at: '2026-11-10T12:00:00Z',
    };

    vi.spyOn(connectionService, 'testConnection').mockReturnValue(of(mockFailedResult));

    const testButtons = fixture.debugElement.queryAll(By.css('.btn-test-connection'));
    testButtons[1].nativeElement.click();
    fixture.detectChanges();
    await fixture.whenStable();

    const failureEl = fixture.debugElement.query(By.css('.test-result-failure'));
    expect(failureEl).toBeTruthy();
    expect(failureEl.nativeElement.textContent).toContain('timed out');
  });

  it('should validate 5-part cron schedule with validation feedback in ConnectionDialogComponent', async () => {
    // Open connection creation dialog
    const addBtn = fixture.debugElement.query(By.css('.btn-add-connection'));
    expect(addBtn).toBeTruthy();
    addBtn.nativeElement.click();
    fixture.detectChanges();
    await fixture.whenStable();

    const dialogDebug = fixture.debugElement.query(By.directive(ConnectionDialogComponent));
    expect(dialogDebug).toBeTruthy();
    const dialogComponent = dialogDebug.componentInstance as ConnectionDialogComponent;

    const cronControl = dialogComponent.form.controls.cron_expression;
    expect(cronControl).toBeTruthy();

    // 1. Invalid cron with fewer than 5 fields
    cronControl.setValue('0 2 * *'); // 4 fields
    cronControl.markAsDirty();
    dialogComponent.form.updateValueAndValidity();
    fixture.detectChanges();

    expect(cronControl.invalid).toBe(true);
    expect(cronControl.errors?.['invalidCron']).toBeTruthy();

    const errorMsgEl = fixture.debugElement.query(By.css('.cron-error-feedback'));
    expect(errorMsgEl).toBeTruthy();
    expect(errorMsgEl.nativeElement.textContent).toContain('5 fields');

    // 2. Invalid cron with out-of-range minute (60)
    cronControl.setValue('60 2 * * *');
    cronControl.markAsDirty();
    dialogComponent.form.updateValueAndValidity();
    fixture.detectChanges();

    expect(cronControl.invalid).toBe(true);
    expect(cronControl.errors?.['invalidCron']).toBeTruthy();

    // 3. Invalid cron with invalid characters
    cronControl.setValue('invalid * * * *');
    cronControl.markAsDirty();
    dialogComponent.form.updateValueAndValidity();
    fixture.detectChanges();

    expect(cronControl.invalid).toBe(true);
    expect(cronControl.errors?.['invalidCron']).toBeTruthy();

    // 4. Valid 5-part cron schedule
    cronControl.setValue('0 2 * * 1-5'); // Mon-Fri at 2am
    cronControl.markAsDirty();
    dialogComponent.form.updateValueAndValidity();
    fixture.detectChanges();

    expect(cronControl.valid).toBe(true);
    expect(cronControl.errors).toBeNull();
    const validFeedbackEl = fixture.debugElement.query(By.css('.cron-valid-feedback'));
    expect(validFeedbackEl).toBeTruthy();
  });
});

